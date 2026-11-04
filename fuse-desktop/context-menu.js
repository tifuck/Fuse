// Electron draws no right-click menu of its own, so without this a right-click
// in Fuse Desktop did nothing: no Copy on selected text, no Paste in a field.
// Menus the page draws itself (transcript links, sidebar rows, the composer's
// spelling popover) cancel the event and never reach here.
function contextMenuTemplate(params, wc, { openExternal, copyText }) {
  const items = [];
  const group = (...entries) => {
    if (items.length) items.push({ type: "separator" });
    items.push(...entries);
  };
  if (params.misspelledWord) {
    group(
      ...(params.dictionarySuggestions || []).map((word) => ({ label: word, click: () => wc.replaceMisspelling(word) })),
      { label: "Add to dictionary", click: () => wc.session.addWordToSpellCheckerDictionary(params.misspelledWord) },
    );
  }
  if (/^(https?|mailto):/i.test(params.linkURL || "")) {
    group(
      { label: "Open in browser", click: () => openExternal(params.linkURL) },
      { label: "Copy link", click: () => copyText(params.linkURL) },
    );
  }
  if (params.mediaType === "image" && params.hasImageContents) {
    group({ label: "Copy image", click: () => wc.copyImageAt(params.x, params.y) });
  }
  const can = params.editFlags || {};
  if (params.isEditable) {
    group(
      { label: "Undo", enabled: Boolean(can.canUndo), click: () => wc.undo() },
      { label: "Redo", enabled: Boolean(can.canRedo), click: () => wc.redo() },
    );
    group(
      { label: "Cut", enabled: Boolean(can.canCut), click: () => wc.cut() },
      { label: "Copy", enabled: Boolean(can.canCopy), click: () => wc.copy() },
      { label: "Paste", enabled: Boolean(can.canPaste), click: () => wc.paste() },
    );
    group({ label: "Select all", enabled: Boolean(can.canSelectAll), click: () => wc.selectAll() });
  } else if ((params.selectionText || "").trim()) {
    group({ label: "Copy", click: () => wc.copy() });
  }
  return items;
}

module.exports = { contextMenuTemplate };
