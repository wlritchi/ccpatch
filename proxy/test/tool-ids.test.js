import test from "node:test";
import assert from "node:assert/strict";

import {
  anthropicToContext,
  canonicalResponsesPayload,
  piMessageToAnthropic,
  streamAnthropicResponse,
} from "../bin/cc-openai-proxy.js";

const model = {
  id: "gpt-5.6-sol",
  provider: "openai-codex",
  api: "openai-codex-responses",
  input: ["text"],
};
const prefix = "ccpatch_tc1_";

/** @param {(string | undefined)[]} ids @returns {object} */
function piMessage(ids) {
  return {
    model: model.id,
    stopReason: "toolUse",
    content: ids.map((id, index) => ({
      type: "toolCall",
      id,
      name: `tool_${index}`,
      arguments: { index },
    })),
  };
}

/** @param {string[]} ids @returns {object} */
function history(ids) {
  return {
    model: model.id,
    messages: [
      {
        role: "assistant",
        content: ids.map((id, index) => ({
          type: "tool_use",
          id,
          name: `tool_${index}`,
          input: { index },
        })),
      },
      {
        role: "user",
        content: ids.map((id, index) => ({
          type: "tool_result",
          tool_use_id: id,
          content: `result ${index}`,
        })),
      },
    ],
  };
}

test("encodes composite tool IDs without collisions and decodes calls and results", () => {
  const composite = "call_first|fc_first";
  const encoded = prefix + Buffer.from(composite).toString("base64url");
  const ids = [
    composite,
    "call_first_fc_first",
    "call_first/fc_first",
    "toolu_native-123",
    encoded,
    prefix,
    "call_工具|fc_🌍",
    `call_${"a".repeat(59)}|fc_${"b".repeat(61)}`,
  ];
  const message = piMessage(ids);
  const response = piMessageToAnthropic(message, model.id);
  const wireIds = response.content.map((block) => block.id);
  assert.equal(wireIds[0], encoded);
  assert.equal(wireIds[1], ids[1]);
  assert.equal(wireIds[3], ids[3]);
  assert.equal(wireIds[7].length, 184);
  assert.equal(new Set(wireIds).size, ids.length);
  for (const id of wireIds) assert.match(id, /^[a-zA-Z0-9_-]+$/);
  assert.deepEqual(message, piMessage(ids));

  const request = history(wireIds);
  const before = structuredClone(request);
  const context = anthropicToContext(request);
  assert.deepEqual(
    context.messages[0].content.map((block) => block.id),
    ids,
  );
  assert.deepEqual(
    context.messages.slice(1).map((item) => item.toolCallId),
    ids,
  );
  assert.deepEqual(
    context.messages.slice(1).map((item) => item.toolName),
    ids.map((_, index) => `tool_${index}`),
  );
  assert.deepEqual(request, before);
  assert.deepEqual(
    piMessageToAnthropic(context.messages[0], model.id).content.map((block) => block.id),
    wireIds,
  );
});

test("uses Anthropic-valid fallback IDs for missing provider IDs", () => {
  const response = piMessageToAnthropic(piMessage([undefined, ""]), model.id);
  const ids = response.content.map((block) => block.id);
  for (const id of ids) assert.match(id, /^toolu_[a-zA-Z0-9_-]+$/);
  assert.notEqual(ids[0], ids[1]);
});

test("preserves legacy IDs and rejects malformed or noncanonical encoding envelopes", () => {
  const ids = [
    "call_old|fc_old",
    "toolu_native",
    prefix,
    `${prefix}a`,
    `${prefix}!!!!`,
    `${prefix}_w`,
    `${prefix}Y2FsbHx`,
    prefix + Buffer.from("call|fc").toString("base64"),
    prefix + Buffer.from("toolu_native").toString("base64url"),
    "ccpatch_tc2_Y2FsbHxmYw",
  ];
  const context = anthropicToContext(history(ids));
  assert.deepEqual(
    context.messages[0].content.map((block) => block.id),
    ids,
  );
  assert.deepEqual(
    context.messages.slice(1).map((item) => item.toolCallId),
    ids,
  );
});

test("mixed legacy and encoded history produces the same OpenAI payload as legacy history", () => {
  const ids = ["call_old|fc_old", "call_new|fc_new", "toolu_native"];
  const encoded = piMessageToAnthropic(piMessage(ids), model.id).content.map((block) => block.id);
  const mixed = history([ids[0], encoded[1], ids[2]]);
  const expected = canonicalResponsesPayload(model, history(ids)).payload;
  const actual = canonicalResponsesPayload(model, mixed).payload;
  assert.deepEqual(actual, expected);
  assert.deepEqual(
    actual.input.filter((item) => item.type === "function_call").map((item) => item.call_id),
    ["call_old", "call_new", "toolu_native"],
  );
  assert.deepEqual(
    actual.input.filter((item) => item.type === "function_call_output").map((item) => item.call_id),
    ["call_old", "call_new", "toolu_native"],
  );
});

test("streamed tool IDs match non-streaming IDs and round-trip into the OpenAI payload", async () => {
  const ids = ["call_stream|fc_stream", "call_second|fc_second", "toolu_native"];
  const message = piMessage(ids);
  const events = message.content.flatMap((toolCall, contentIndex) => [
    { type: "toolcall_start", contentIndex, partial: message },
    { type: "toolcall_end", contentIndex, toolCall },
  ]);
  events.push({ type: "done", message });
  let output = "";
  let ended = false;
  const response = {
    /** @param {number} status @param {object} headers @returns {void} */
    writeHead(status, headers) {
      assert.equal(status, 200);
      assert.equal(headers["content-type"], "text/event-stream");
    },
    /** @param {string} chunk @returns {void} */
    write(chunk) {
      output += chunk;
    },
    /** @returns {void} */
    end() {
      ended = true;
    },
  };
  await streamAnthropicResponse({}, response, events, model.id);
  assert.ok(ended);
  const starts = output
    .split("\n")
    .filter((line) => line.startsWith("data: "))
    .map((line) => JSON.parse(line.slice(6)))
    .filter((event) => event.type === "content_block_start");
  const wireIds = starts.map((event) => event.content_block.id);
  assert.deepEqual(
    wireIds,
    piMessageToAnthropic(message, model.id).content.map((block) => block.id),
  );
  for (const id of wireIds) assert.match(id, /^[a-zA-Z0-9_-]+$/);
  assert.deepEqual(
    canonicalResponsesPayload(model, history(wireIds)).payload,
    canonicalResponsesPayload(model, history(ids)).payload,
  );
});
