// Protect secrets window: its row model, labels and request body. No DOM.

// One row per offered value. A value found on a line with no NAME= in front
// of it is named by its kind. A plain value reads like ordinary text (the
// server says so): its row warns and starts unticked.
export function secretRows(items) {
  return (Array.isArray(items) ? items : []).filter((item) => item && Number.isSafeInteger(item.id)).map((item) => {
    const key = String(item.key || "");
    const kind = String(item.kind || "Secret");
    return {
      id: item.id,
      name: key || kind,
      keyed: Boolean(key),
      kind,
      value: String(item.value ?? ""),
      plain: Boolean(item.plain),
      host: String(item.host || ""),
      place: String(item.file || "") + (item.line > 0 ? ":" + item.line : ""),
    };
  });
}

export function protectLabel(ticked) {
  return ticked > 0 ? `Protect ${ticked} selected` : "Protect none";
}

// The header checkbox: every row ticked, none, or some.
export function allState(ticked, total) {
  if (!ticked) return "none";
  return ticked < total ? "mixed" : "all";
}

// What the decision POSTs: the server settles only the ids the window
// showed, so a value found later is not declined unseen. Turning detection
// off protects nothing.
export function decision(ticked, shown, never = false) {
  return { protect: never ? [] : ticked, shown, never: Boolean(never) };
}
