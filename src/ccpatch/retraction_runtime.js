function createRetractionArchive() {
  const sessions = new Map();
  const recordType = "ccpatch-retracted";

  function session(id) {
    let state = sessions.get(id);
    if (!state) {
      state = { entries: new Map(), timeline: [], revision: 0 };
      sessions.set(id, state);
    }
    return state;
  }

  function observe(state, rows) {
    const known = new Set(state.timeline);
    for (let index = 0; index < rows.length; index++) {
      const uuid = rows[index]?.uuid;
      if (typeof uuid !== "string" || known.has(uuid)) continue;
      const next = rows.slice(index + 1).find((row) => known.has(row?.uuid));
      const offset = next ? state.timeline.indexOf(next.uuid) : state.timeline.length;
      state.timeline.splice(offset, 0, uuid);
      known.add(uuid);
    }
  }

  function capture(id, message, rows = [], reason = "tombstone") {
    if (!message || typeof message.uuid !== "string") return;
    const state = session(id);
    observe(state, rows);
    if (!state.timeline.includes(message.uuid)) state.timeline.push(message.uuid);
    if (state.entries.has(message.uuid)) return state.entries.get(message.uuid);
    const index = state.timeline.indexOf(message.uuid);
    const entry = {
      type: recordType,
      schemaVersion: 1,
      archiveId: crypto.randomUUID(),
      originalUuid: message.uuid,
      sessionId: id,
      retractedAt: new Date().toISOString(),
      reason,
      placement: {
        index,
        previousUuid: state.timeline[index - 1] ?? null,
        nextUuid: state.timeline[index + 1] ?? null,
        parentUuid: message.parentUuid ?? null,
      },
      payloadJson: JSON.stringify(message),
    };
    state.entries.set(message.uuid, entry);
    state.revision++;
    return entry;
  }

  function restore(record) {
    if (
      record?.type !== recordType ||
      record.schemaVersion !== 1 ||
      typeof record.sessionId !== "string" ||
      typeof record.originalUuid !== "string" ||
      typeof record.payloadJson !== "string"
    )
      return false;
    let payload;
    try {
      payload = JSON.parse(record.payloadJson);
    } catch {
      return false;
    }
    if (payload?.uuid !== record.originalUuid) return false;
    const state = session(record.sessionId);
    if (!state.entries.has(record.originalUuid)) {
      state.entries.set(record.originalUuid, JSON.parse(JSON.stringify(record)));
      observe(
        state,
        [record.placement?.previousUuid, record.originalUuid, record.placement?.nextUuid].map(
          (uuid) => ({ uuid }),
        ),
      );
      state.revision++;
    }
    return true;
  }

  function restoreTimeline(id, uuids) {
    const state = session(id);
    const ordered = [...new Set(uuids)];
    // Keep captured blocks that have not reached the transcript file.
    for (const uuid of state.timeline) {
      if (ordered.includes(uuid)) continue;
      const entry = state.entries.get(uuid);
      const next = ordered.indexOf(entry?.placement?.nextUuid);
      const previous = ordered.indexOf(entry?.placement?.previousUuid);
      ordered.splice(next >= 0 ? next : previous >= 0 ? previous + 1 : ordered.length, 0, uuid);
    }
    state.timeline = ordered;
    state.revision++;
  }

  function archiveLine(id, raw, position) {
    const message = JSON.parse(raw);
    const entry = capture(id, message);
    if (!entry) throw new Error("ccpatch: retracted record has no UUID");
    // Keep both snapshots if persistence changed the live block data.
    const persisted = JSON.stringify(message);
    if (entry.payloadJson !== persisted) entry.livePayloadJson ??= entry.payloadJson;
    entry.payloadJson = persisted;
    entry.placement.parentUuid = message.parentUuid ?? entry.placement.parentUuid;
    entry.placement.diskIndex = position;
    return JSON.stringify(entry) + "\n";
  }

  function pending(id, uuid) {
    return session(id).entries.get(uuid);
  }

  function snapshot(id) {
    const state = session(id);
    return {
      revision: state.revision,
      timeline: [...state.timeline],
      entries: [...state.entries.values()].map((entry) => ({
        ...JSON.parse(JSON.stringify(entry)),
        message: JSON.parse(entry.livePayloadJson ?? entry.payloadJson),
      })),
    };
  }

  return { recordType, capture, restore, restoreTimeline, archiveLine, pending, snapshot };
}
