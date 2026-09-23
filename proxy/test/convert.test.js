import test from "node:test";
import assert from "node:assert/strict";

import { createModels } from "@earendil-works/pi-ai";
import { openaiCodexProvider } from "@earendil-works/pi-ai/providers/openai-codex";

import {
  anthropicToContext,
  assertTokenizerMappings,
  canonicalResponsesPayload,
  countTokensResponse,
  estimateInputTokens,
  piContentToAnthropic,
  resolveModelId,
  thinkingToReasoning,
  wantsStreaming,
} from "../bin/cc-openai-proxy.js";

test("converts Anthropic messages, tools, and tool results to pi context", () => {
  const context = anthropicToContext({
    model: "claude-sonnet-4-5",
    system: [{ type: "text", text: "system" }],
    tools: [
      {
        name: "Read",
        description: "read a file",
        input_schema: {
          type: "object",
          properties: { file_path: { type: "string" } },
        },
      },
    ],
    messages: [
      { role: "user", content: "hello" },
      {
        role: "assistant",
        content: [
          {
            type: "tool_use",
            id: "toolu_1",
            name: "Read",
            input: { file_path: "a.txt" },
          },
        ],
      },
      {
        role: "user",
        content: [{ type: "tool_result", tool_use_id: "toolu_1", content: "contents" }],
      },
    ],
  });

  assert.equal(context.systemPrompt, "system");
  assert.equal(context.tools[0].name, "Read");
  assert.equal(context.messages[1].content[0].type, "toolCall");
  assert.equal(context.messages[2].role, "toolResult");
  assert.equal(context.messages[2].toolName, "Read");
});

test("maps Anthropic thinking budgets to pi reasoning levels", () => {
  assert.equal(thinkingToReasoning({ type: "disabled" }), "off");
  assert.equal(thinkingToReasoning({ type: "enabled", budget_tokens: 512 }), "low");
  assert.equal(thinkingToReasoning({ type: "enabled", budget_tokens: 4096 }), "medium");
  assert.equal(thinkingToReasoning({ type: "enabled", budget_tokens: 20000 }), "high");
});

test("maps Anthropic family names to OpenAI Codex defaults", () => {
  assert.equal(resolveModelId("claude-3-5-haiku-latest"), "gpt-6-luna");
  assert.equal(resolveModelId("claude-sonnet-4-5"), "gpt-5.6-terra");
  assert.equal(resolveModelId("claude-opus-4-8"), "gpt-6-sol");
  assert.equal(resolveModelId("claude-fable-5"), "gpt-6-astra");
  assert.equal(resolveModelId("gpt-5.4"), "gpt-5.4");
});

test("converts pi assistant blocks back to Anthropic content blocks", () => {
  const blocks = piContentToAnthropic([
    { type: "text", text: "answer" },
    {
      type: "toolCall",
      id: "toolu_1",
      name: "Bash",
      arguments: { command: "pwd" },
    },
  ]);

  assert.deepEqual(blocks, [
    { type: "text", text: "answer" },
    {
      type: "tool_use",
      id: "toolu_1",
      name: "Bash",
      input: { command: "pwd" },
    },
  ]);
});

const tokenModel = {
  id: "gpt-5.6-sol",
  provider: "openai-codex",
  api: "openai-codex-responses",
  input: ["text", "image"],
  reasoning: true,
  compat: { supportsOpenAIGrammarTools: false },
};

for (const splitMessages of [false, true]) {
  test(`pairs parallel tool results before user text (split messages: ${splitMessages})`, () => {
    const ids = ["call_first|fc_first", "call_second|fc_second"];
    const blocks = [
      { type: "text", text: "before" },
      { type: "tool_result", tool_use_id: ids[0], content: "first result" },
      { type: "text", text: "between" },
      { type: "tool_result", tool_use_id: ids[1], content: "second result" },
      { type: "text", text: "after" },
    ];
    const request = {
      model: tokenModel.id,
      messages: [
        {
          role: "assistant",
          content: ids.map((id) => ({
            type: "tool_use",
            id,
            name: "Read",
            input: {},
          })),
        },
        ...(splitMessages
          ? blocks.map((block) => ({ role: "user", content: [block] }))
          : [{ role: "user", content: blocks }]),
      ],
    };
    const input = canonicalResponsesPayload(tokenModel, request).payload.input;
    const outputs = input.filter((item) => item.type === "function_call_output");
    assert.deepEqual(
      outputs.map((item) => [item.call_id, item.output]),
      [
        ["call_first", "first result"],
        ["call_second", "second result"],
      ],
    );
    assert.deepEqual(
      input.slice(0, 4).map((item) => item.type),
      ["function_call", "function_call", "function_call_output", "function_call_output"],
    );
    assert.deepEqual(
      input.slice(4).map((item) => item.content[0].text),
      ["before", "between", "after"],
    );
  });
}

test("preserves orphaned and duplicate tool results as user context", () => {
  const request = {
    model: tokenModel.id,
    messages: [
      {
        role: "user",
        content: [
          {
            type: "tool_result",
            tool_use_id: "call_orphan",
            content: "orphan output",
          },
        ],
      },
      {
        role: "assistant",
        content: [{ type: "tool_use", id: "call_actual", name: "Read", input: {} }],
      },
      {
        role: "user",
        content: [
          {
            type: "tool_result",
            tool_use_id: "call_actual",
            content: "actual output",
          },
          {
            type: "tool_result",
            tool_use_id: "call_actual",
            content: "duplicate output",
          },
        ],
      },
    ],
  };
  const input = canonicalResponsesPayload(tokenModel, request).payload.input;
  assert.deepEqual(
    input.filter((item) => item.type === "function_call_output").map((item) => item.output),
    ["actual output"],
  );
  const userText = JSON.stringify(input.filter((item) => item.role === "user"));
  assert.match(userText, /orphan output/);
  assert.match(userText, /duplicate output/);
});

function tokenRequest(content, extra = {}) {
  return {
    model: "claude-opus-4-8",
    messages: [{ role: "user", content }],
    ...extra,
  };
}

test("uses o200k tokens rather than UTF-8 bytes divided by four", () => {
  const samples = [
    "你好，世界！🌍",
    "repeat ".repeat(200),
    "function answer<T>(value: T): T { return value; }",
  ];
  for (const sample of samples) {
    const estimated = estimateInputTokens(tokenModel, tokenRequest(sample));
    const byteQuarter = Math.ceil(Buffer.byteLength(JSON.stringify(tokenRequest(sample))) / 4);
    assert.notEqual(estimated, byteQuarter, sample);
  }
});

test("tokenizes large repetitive input without pathological slowdown", () => {
  const started = performance.now();
  const estimated = estimateInputTokens(tokenModel, tokenRequest("x".repeat(1024 * 1024)));
  assert.ok(estimated > 100_000);
  assert.ok(performance.now() - started < 10_000);
});

test("counts canonical system, history, tool schemas, arguments, results, and IDs", () => {
  const request = tokenRequest("run it", {
    system: "Keep this system instruction.",
    tools: [
      {
        name: "execute",
        description: "Execute code safely.",
        input_schema: {
          type: "object",
          properties: { code: { type: "string" } },
          required: ["code"],
        },
      },
    ],
    messages: [
      { role: "user", content: "run it" },
      {
        role: "assistant",
        content: [
          {
            type: "tool_use",
            id: "call_important_id",
            name: "execute",
            input: { code: "console.log('hello')" },
          },
        ],
      },
      {
        role: "user",
        content: [
          {
            type: "tool_result",
            tool_use_id: "call_important_id",
            content: "hello",
          },
        ],
      },
    ],
  });
  const canonical = canonicalResponsesPayload(tokenModel, request).payload;
  const serialized = JSON.stringify(canonical);
  for (const expected of [
    "Keep this system instruction.",
    "execute",
    "console.log",
    "call_important_id",
    "hello",
  ]) {
    assert.match(serialized, new RegExp(expected.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.ok(
    estimateInputTokens(tokenModel, request) >
      estimateInputTokens(tokenModel, tokenRequest("run it")),
  );
});

test("maps every supported advertised model to a deterministic tokenizer", () => {
  const models = createModels();
  models.setProvider(openaiCodexProvider());
  const advertisedModels = models.getModels("openai-codex");
  assert.doesNotThrow(() => assertTokenizerMappings(advertisedModels));
  for (const model of advertisedModels) {
    assert.equal(
      estimateInputTokens(model, tokenRequest("mapping probe")),
      estimateInputTokens(model, tokenRequest("mapping probe")),
      model.id,
    );
  }
  assert.throws(
    () => estimateInputTokens({ ...tokenModel, id: "future-unknown" }, tokenRequest("x")),
    /no local tokenizer mapping for future-unknown/,
  );
});

test("adds conservative image and encrypted reasoning estimates", () => {
  const plain = estimateInputTokens(tokenModel, tokenRequest("look"));
  const image = estimateInputTokens(
    tokenModel,
    tokenRequest([
      { type: "text", text: "look" },
      {
        type: "image",
        source: { type: "base64", media_type: "image/png", data: "secret" },
      },
    ]),
  );
  const reasoning = estimateInputTokens(tokenModel, {
    model: "claude-opus-4-8",
    messages: [
      {
        role: "assistant",
        content: [
          {
            type: "redacted_thinking",
            data: "encrypted-reasoning-secret",
          },
          { type: "text", text: "look" },
        ],
      },
    ],
  });
  const fakeImageUrl = "x".repeat(64 * 1024);
  const toolMetadata = estimateInputTokens(tokenModel, {
    ...tokenRequest("run it"),
    tools: [
      {
        name: "inspect",
        description: "Inspect structured data.",
        input_schema: {
          type: "object",
          properties: { image_url: { const: fakeImageUrl } },
        },
      },
    ],
    messages: [
      {
        role: "assistant",
        content: [
          {
            type: "tool_use",
            id: "toolu_image_url",
            name: "inspect",
            input: { image_url: fakeImageUrl },
          },
        ],
      },
    ],
  });
  assert.ok(image >= plain + 2000);
  assert.ok(reasoning > plain);
  assert.ok(toolMetadata > image + 10_000);
  assert.doesNotMatch(
    JSON.stringify(canonicalResponsesPayload(tokenModel, tokenRequest("look")).payload),
    /secret/,
  );
});

test("bounds Anthropic tool result image estimates independently of payload length", () => {
  const requestWithImage = (data) => ({
    model: "claude-opus-4-8",
    messages: [
      {
        role: "assistant",
        content: [
          {
            type: "tool_use",
            id: "toolu_screenshot",
            name: "screenshot",
            input: {},
          },
        ],
      },
      {
        role: "user",
        content: [
          {
            type: "tool_result",
            tool_use_id: "toolu_screenshot",
            content: [
              { type: "text", text: "Screenshot captured." },
              {
                type: "image",
                source: { type: "base64", media_type: "image/png", data },
              },
            ],
          },
        ],
      },
    ],
  });
  const textOnly = requestWithImage("");
  textOnly.messages[1].content[0].content.pop();
  const plain = estimateInputTokens(tokenModel, textOnly);
  const shortImage = estimateInputTokens(tokenModel, requestWithImage("a"));
  const longImage = estimateInputTokens(tokenModel, requestWithImage("a".repeat(1024 * 1024)));

  assert.ok(shortImage >= plain + 2000);
  assert.equal(longImage, shortImage);
});

test("count token response includes Anthropic and validation usage shapes", () => {
  assert.deepEqual(countTokensResponse(12), {
    input_tokens: 12,
    usage: {
      input_tokens: 12,
      output_tokens: 0,
      cache_creation_input_tokens: 0,
      cache_read_input_tokens: 0,
    },
  });
});

test("streams only when the request sets stream to true", () => {
  assert.equal(wantsStreaming({ stream: true }), true);
  assert.equal(wantsStreaming({ stream: false }), false);
  // The SDK omits the field on non-streaming calls (/model validation probes).
  assert.equal(wantsStreaming({}), false);
  assert.equal(wantsStreaming({ stream: "true" }), false);
});
