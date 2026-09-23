function createRetractionStorage(fs, createReadStream, createInterface, archive, syncFs) {
  async function* localRows(path) {
    const input = createReadStream(path, { encoding: "utf8" });
    const lines = createInterface({ input, crlfDelay: Infinity });
    try {
      for await (const line of lines) yield line;
    } finally {
      lines.close();
      input.destroy();
    }
  }

  async function* backendRows(backend, key) {
    let fromSeq;
    for (;;) {
      const result = await backend.readRecords(key, {
        order: "forward",
        ...(fromSeq === undefined ? {} : { fromSeq }),
        maxBytes: 1024 * 1024,
      });
      if (!result.ok) {
        if (result.error.code === "NotFound") return;
        throw new Error("ccpatch: archive record read failed", { cause: result.error });
      }
      for (const row of result.value.items) yield row;
      const next = result.value.nextSeq;
      if (next === undefined) return;
      if (fromSeq !== undefined && next <= fromSeq)
        throw new Error("ccpatch: archive cursor stalled");
      fromSeq = next;
    }
  }

  function parse(line) {
    try {
      return JSON.parse(line);
    } catch {
      return null;
    }
  }

  async function restore(path, backend, key) {
    const positions = [];
    const sessions = new Set();
    function accept(row) {
      if (archive.restore(row)) {
        sessions.add(row.sessionId);
        positions.push({ sessionId: row.sessionId, uuid: row.originalUuid });
      } else if (
        ["user", "assistant", "attachment", "system"].includes(row?.type) &&
        typeof row.uuid === "string"
      ) {
        positions.push({ sessionId: row.sessionId, uuid: row.uuid });
      }
    }
    try {
      if (backend && key) {
        for await (const row of backendRows(backend, key)) {
          accept(parse(Buffer.from(row.data).toString("utf8")));
        }
      } else {
        for await (const line of localRows(path)) accept(parse(line));
      }
      for (const id of sessions) {
        archive.restoreTimeline(
          id,
          positions
            .filter((row) => row.sessionId === undefined || row.sessionId === id)
            .map((row) => row.uuid),
        );
      }
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }

  async function replaceLocal(path, id, uuid) {
    let before;
    try {
      before = await fs.stat(path);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    const temporary = `${path}.ccpatch-retraction-${crypto.randomUUID()}.tmp`;
    let output;
    try {
      output = await fs.open(temporary, "wx", 0o600);
      let found = false;
      let alreadyArchived = false;
      let index = 0;
      if (before) {
        for await (const line of localRows(path)) {
          const row = parse(line);
          if (row?.type === archive.recordType && row.originalUuid === uuid) {
            alreadyArchived = true;
            archive.restore(row);
          }
          if (row?.uuid === uuid) {
            await output.writeFile(archive.archiveLine(id, line, index));
            found = true;
          } else await output.writeFile(line + "\n");
          index++;
        }
      }
      const pending = archive.pending(id, uuid);
      if (!found && !alreadyArchived && pending) {
        pending.placement.diskIndex = index;
        await output.writeFile(JSON.stringify(pending) + "\n");
        found = true;
      }
      if (!found) return;
      await output.sync();
      await output.close();
      output = undefined;
      let after;
      try {
        after = syncFs.statSync(path);
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
      if (
        Boolean(before) !== Boolean(after) ||
        (before &&
          (after.ino !== before.ino ||
            after.size !== before.size ||
            after.mtimeMs !== before.mtimeMs ||
            after.ctimeMs !== before.ctimeMs))
      )
        return false;
      // Do not yield between the final check and replacement.
      syncFs.renameSync(temporary, path);
      return true;
    } finally {
      await output?.close();
      await fs.unlink(temporary).catch((error) => {
        if (error.code !== "ENOENT") throw error;
      });
    }
  }

  async function replace(path, id, uuid, backend) {
    if (backend !== undefined) throw new Error("ccpatch: V5 archive writes are disabled");
    for (let attempt = 0; attempt < 3; attempt++) {
      if ((await replaceLocal(path, id, uuid)) !== false) return;
    }
    throw new Error("ccpatch: transcript changed during retraction archival");
  }

  return { replace, restore };
}
