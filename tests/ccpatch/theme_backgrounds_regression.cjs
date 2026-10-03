const assert = require("node:assert/strict");
const { basename: ce, extname: ue } = require("node:path");

const theme = {
  diffAdded: "#38572e",
  diffAddedDimmed: "#2c4125",
  diffAddedWord: "#558844",
  diffRemoved: "#62222c",
  diffRemovedDimmed: "#481e24",
  diffRemovedWord: "#9b3141",
};
const _s = () => false;
const a = {};
const vi = (text, count) => text.repeat(count);
const re = (text, count) => text.slice(0, count);
const ot = (text, separator) => text.split(separator)[0];
const c = (error) => {
  throw error;
};
const fe = { level: 3 };
// Use equal-length ASCII words to isolate the renderer from the diff library.
const TNr = (before, after) => {
  assert.equal(before.length, after.length);
  return before.flatMap((value, index) =>
    value === after[index]
      ? [{ value: [value] }]
      : [
          { value: [value], removed: true },
          { value: [after[index]], added: true },
        ],
  );
};
const bXt = () => {
  throw Error("Unexpected syntax highlighting");
};
const UX = () => null;
const ae = (text) => text.length;
const pCt = [];
const Cgr = /‍/;
const Rgr = /‍/;
const sl = () => ({ claim: () => false });
const wa = () => new Intl.Segmenter();
const gRn = (text) => text;

/* NATIVE_RENDERER */

const hunk = {
  oldStart: 1,
  newStart: 1,
  oldLines: 1,
  newLines: 1,
  lines: ["-the old value stays readable", "+the new value stays readable"],
};
const renderer = new H(hunk, null, "file.unknown");
const background = (hex) =>
  `\x1b[48;2;${parseInt(hex.slice(1, 3), 16)};${parseInt(hex.slice(3, 5), 16)};${parseInt(hex.slice(5, 7), 16)}m`;
const colors = (text) => [...new Set(text.match(/\x1b\[48;2;\d+;\d+;\d+m/g))].sort();
for (const split of [false, true]) {
  for (const dim of [false, true]) {
    const rendered = split
      ? renderer.renderSplit("dark", 80, dim, theme)
      : renderer.render("dark", 80, dim, theme);
    const text = split ? [...rendered.gutters, ...rendered.contents].join("") : rendered.join("");
    const expected = ["Added", "Removed"].flatMap((kind) =>
      dim
        ? [background(theme[`diff${kind}Dimmed`])]
        : [background(theme[`diff${kind}`]), background(theme[`diff${kind}Word`])],
    );
    assert.deepEqual(colors(text), expected.sort());
    console.log(JSON.stringify({ split, dim, backgrounds: colors(text) }));
  }
}
const native = renderer.render("dark", 80, false).join("");
assert.ok(native.includes("\x1b[48;2;2;40;0m"));
assert.ok(!native.includes(background(theme.diffAdded)));

const YYe = () => 4;
const checkCache = () => {
  /* NATIVE_CACHE */

  const cache = new WeakMap();
  for (const split of [false, true]) {
    const render = (overrides) =>
      I(cache, hunk, null, "file.unknown", null, "dark", Z(overrides), 80, false, split);
    const original = render(theme);
    assert.equal(render(theme), original);
    const changed = { ...theme, diffAdded: "#123456", diffAddedWord: "#654321" };
    const updated = render(changed);
    assert.notEqual(updated, original);
    const text = (split ? updated.contents : updated.lines).join("");
    assert.ok(text.includes(background(changed.diffAdded)));
    assert.ok(text.includes(background(changed.diffAddedWord)));
    assert.ok(!text.includes(background(theme.diffAdded)));
    assert.equal(render(changed), updated);
    const fallback = render(undefined);
    assert.notEqual(fallback, updated);
    assert.ok((split ? fallback.contents : fallback.lines).join("").includes("\x1b[48;2;2;40;0m"));
  }
};
checkCache();
