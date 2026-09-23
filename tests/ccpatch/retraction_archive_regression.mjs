import assert from "node:assert/strict";
import * as fs from "node:fs/promises";
import { createReadStream, statSync, renameSync, appendFileSync } from "node:fs";
const syncFs = { statSync, renameSync };
import { createInterface } from "node:readline";
import { tmpdir } from "node:os";
import { join } from "node:path";
import vm from "node:vm";

const runtimeSource = await fs.readFile(process.argv[2], "utf8");
const storageSource = await fs.readFile(process.argv[3], "utf8");
const createArchive = vm.runInThisContext(`${runtimeSource};createRetractionArchive`);
const createStorage = vm.runInThisContext(`${storageSource};createRetractionStorage`);
const archive = createArchive();
const storage = createStorage(fs, createReadStream, createInterface, archive, syncFs);
const root = await fs.mkdtemp(join(tmpdir(), "ccpatch-retractions-"));
const user = { type: "user", uuid: "user", message: { role: "user", content: "hello" } };
const thinking = {
  type: "assistant",
  uuid: "thinking",
  parentUuid: "user",
  message: { content: [{ type: "thinking", thinking: "SECRET_THINKING", signature: "sig" }] },
};
const tool = {
  type: "assistant",
  uuid: "tool",
  parentUuid: "thinking",
  message: {
    content: [{ type: "tool_use", id: "call", name: "Bash", input: { command: "SECRET_TOOL" } }],
  },
};
const result = {
  type: "user",
  uuid: "result",
  parentUuid: "tool",
  message: { content: [{ type: "tool_result", tool_use_id: "call", content: "SECRET_RESULT" }] },
};
const output = {
  type: "assistant",
  uuid: "output",
  parentUuid: "result",
  message: { content: [{ type: "text", text: "SECRET_OUTPUT" }] },
};
const rows = [user, thinking, tool, result, output];
const lines = (values) => values.map((value) => JSON.stringify(value) + "\n").join("");
try {
  const path = join(root, "session.jsonl");
  await fs.writeFile(path, lines(rows));
  for (const row of rows.slice(1)) {
    archive.capture("session", row, rows);
    await storage.replace(path, "session", row.uuid);
    await storage.replace(path, "session", row.uuid);
  }
  const saved = (await fs.readFile(path, "utf8")).trim().split("\n").map(JSON.parse);
  assert.equal(saved.length, rows.length);
  assert.deepEqual(
    saved.filter((row) => row.type !== archive.recordType),
    [user],
  );
  for (let i = 1; i < saved.length; i++) {
    const record = saved[i];
    assert.equal(record.type, archive.recordType);
    assert.deepEqual(JSON.parse(record.payloadJson), rows[i]);
    assert.equal(record.placement.index, i);
    assert.equal(record.placement.diskIndex, i);
    assert.equal(record.placement.previousUuid, rows[i - 1].uuid);
    assert.equal(record.placement.nextUuid, rows[i + 1]?.uuid ?? null);
    assert(!JSON.stringify(record).includes(`"uuid":"${rows[i].uuid}"`));
    assert(!JSON.stringify(record).includes(`"type":"${rows[i].type}"`));
  }
  assert.equal((await fs.stat(path)).mode & 0o777, 0o600);
  const resumed = createArchive();
  await createStorage(fs, createReadStream, createInterface, resumed, syncFs).restore(path);
  assert.equal(resumed.snapshot("session").entries.length, 4);
  assert.deepEqual(
    resumed.snapshot("session").entries.map((entry) => entry.message),
    rows.slice(1),
  );
  assert.equal(resumed.snapshot("other").entries.length, 0);
  const snapshot = resumed.snapshot("session");
  snapshot.entries[0].message.message.content[0].thinking = "changed";
  assert.equal(
    resumed.snapshot("session").entries[0].message.message.content[0].thinking,
    "SECRET_THINKING",
  );

  // Restore live anchors before a later retraction.
  const orderPath = join(root, "order.jsonl");
  const orderRows = ["u", "a", "b", "c"].map((uuid) => ({ type: "assistant", uuid }));
  const first = createArchive();
  const orderStorage = createStorage(fs, createReadStream, createInterface, first, syncFs);
  await fs.writeFile(orderPath, lines(orderRows));
  for (const row of orderRows.slice(1, 3)) {
    first.capture("order", row, orderRows);
    await orderStorage.replace(orderPath, "order", row.uuid);
  }
  const direct = createArchive();
  direct.restore(first.pending("order", "a"));
  direct.restore(first.pending("order", "b"));
  direct.capture("order", orderRows[3], [orderRows[0], orderRows[3]]);
  assert.deepEqual(direct.snapshot("order").timeline, ["u", "a", "b", "c"]);
  assert.equal(direct.pending("order", "c").placement.previousUuid, "b");
  const second = createArchive();
  const secondStorage = createStorage(fs, createReadStream, createInterface, second, syncFs);
  await secondStorage.restore(orderPath);
  second.capture("order", orderRows[3], [orderRows[0], orderRows[3]]);
  assert.deepEqual(second.snapshot("order").timeline, ["u", "a", "b", "c"]);
  assert.equal(second.pending("order", "c").placement.previousUuid, "b");
  await secondStorage.replace(orderPath, "order", "c");
  await secondStorage.restore(orderPath);
  assert.deepEqual(second.snapshot("order").timeline, ["u", "a", "b", "c"]);

  // Retry if native metadata arrives before the final synchronous check.
  const racePath = join(root, "race.jsonl");
  await fs.writeFile(racePath, lines([tool]));
  const metadata = { type: "custom-title", customTitle: "keep me" };
  let injected = false;
  let checks = 0;
  const racingSyncFs = {
    ...syncFs,
    statSync(path) {
      checks++;
      if (!injected) {
        injected = true;
        appendFileSync(path, lines([metadata]));
      }
      return statSync(path);
    },
  };
  await createStorage(fs, createReadStream, createInterface, archive, racingSyncFs).replace(
    racePath,
    "session",
    tool.uuid,
  );
  const raced = (await fs.readFile(racePath, "utf8")).trim().split("\n").map(JSON.parse);
  assert.equal(checks, 2);
  assert.deepEqual(raced[1], metadata);
  assert.equal(raced[0].type, archive.recordType);

  const unpersisted = {
    type: "attachment",
    uuid: "attachment",
    attachment: { type: "structured_output", output: "SECRET_ATTACHMENT" },
  };
  archive.capture("session", unpersisted, rows);
  await storage.replace(path, "session", unpersisted.uuid);
  assert.equal((await fs.readFile(path, "utf8")).trim().split("\n").length, 6);

  const brokenSyncFs = {
    ...syncFs,
    renameSync: () => {
      throw new Error("disk failure");
    },
  };
  const failed = join(root, "failed.jsonl");
  await fs.writeFile(failed, lines([tool]));
  await assert.rejects(
    createStorage(fs, createReadStream, createInterface, archive, brokenSyncFs).replace(
      failed,
      "session",
      tool.uuid,
    ),
    /disk failure/,
  );
  assert.deepEqual(await fs.readFile(failed, "utf8"), lines([tool]));
  assert(!(await fs.readdir(root)).some((name) => name.endsWith(".tmp")));

  // An injected backend must never receive archival payloads.
  const calls = [];
  const backend = new Proxy(
    {},
    {
      get(_target, name) {
        return () => {
          calls.push(name);
          throw new Error("unexpected backend call");
        };
      },
    },
  );
  await assert.rejects(
    storage.replace("unused", "session", tool.uuid, backend),
    /V5 archive writes are disabled/,
  );
  assert.deepEqual(calls, []);
  assert(archive.snapshot("session").entries.some((entry) => entry.originalUuid === tool.uuid));
} finally {
  await fs.rm(root, { recursive: true, force: true });
}
console.log("retraction archive regression passed");
