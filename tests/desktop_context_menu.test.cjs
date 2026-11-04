const test = require("node:test");
const assert = require("node:assert/strict");
const { contextMenuTemplate } = require("../fuse-desktop/context-menu.js");

function fakeContents() {
  const calls = [];
  const wc = new Proxy({ session: { addWordToSpellCheckerDictionary: (w) => calls.push(["addWord", w]) } }, {
    get: (target, name) => (name in target ? target[name] : (...args) => calls.push([name, ...args])),
  });
  return { wc, calls };
}

const opened = [];
const copied = [];
const io = { openExternal: (url) => opened.push(url), copyText: (text) => copied.push(text) };
const labels = (items) => items.map((item) => item.type === "separator" ? "-" : item.label);
const page = { x: 10, y: 20, linkURL: "", selectionText: "", isEditable: false, editFlags: {}, misspelledWord: "", dictionarySuggestions: [], mediaType: "none" };

test("selected page text gets Copy", () => {
  const { wc, calls } = fakeContents();
  const items = contextMenuTemplate({ ...page, selectionText: "some text" }, wc, io);
  assert.deepEqual(labels(items), ["Copy"]);
  items[0].click();
  assert.deepEqual(calls, [["copy"]]);
});

test("a plain right-click with nothing selected shows no menu", () => {
  const { wc } = fakeContents();
  assert.deepEqual(contextMenuTemplate({ ...page, selectionText: "  " }, wc, io), []);
});

test("a text field gets the edit menu, enabled from its edit flags", () => {
  const { wc, calls } = fakeContents();
  const editFlags = { canUndo: false, canRedo: false, canCut: true, canCopy: true, canPaste: true, canSelectAll: true };
  const items = contextMenuTemplate({ ...page, isEditable: true, selectionText: "x", editFlags }, wc, io);
  assert.deepEqual(labels(items), ["Undo", "Redo", "-", "Cut", "Copy", "Paste", "-", "Select all"]);
  assert.deepEqual(items.filter((item) => item.label).map((item) => item.enabled), [false, false, true, true, true, true]);
  items.find((item) => item.label === "Paste").click();
  assert.deepEqual(calls, [["paste"]]);
});

test("a misspelled word lists the spellchecker's suggestions first", () => {
  const { wc, calls } = fakeContents();
  const items = contextMenuTemplate({ ...page, isEditable: true, misspelledWord: "teh", dictionarySuggestions: ["the", "ten"] }, wc, io);
  assert.deepEqual(labels(items).slice(0, 4), ["the", "ten", "Add to dictionary", "-"]);
  items[0].click();
  items[2].click();
  assert.deepEqual(calls, [["replaceMisspelling", "the"], ["addWord", "teh"]]);
});

test("web links open in the browser or copy; other schemes get no link items", () => {
  const { wc } = fakeContents();
  const items = contextMenuTemplate({ ...page, linkURL: "https://example.com/a" }, wc, io);
  assert.deepEqual(labels(items), ["Open in browser", "Copy link"]);
  items[0].click();
  items[1].click();
  assert.deepEqual(opened, ["https://example.com/a"]);
  assert.deepEqual(copied, ["https://example.com/a"]);
  assert.deepEqual(contextMenuTemplate({ ...page, linkURL: "javascript:alert(1)" }, wc, io), []);
});

test("an image can be copied from where it was clicked", () => {
  const { wc, calls } = fakeContents();
  const items = contextMenuTemplate({ ...page, mediaType: "image", hasImageContents: true }, wc, io);
  assert.deepEqual(labels(items), ["Copy image"]);
  items[0].click();
  assert.deepEqual(calls, [["copyImageAt", 10, 20]]);
});
