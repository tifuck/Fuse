// Fuse Desktop keeps the desktop layout at every window width. Zoomed in, its
// narrowest window falls under the 800px phone breakpoint, and the phone
// layout (full-screen drawer, sheets, back-gesture stack) does not belong in a
// desktop window. Loaded after every stylesheet, before first paint: width
// media queries, in the CSS and through matchMedia, answer as if the viewport
// were at least 801px wide.
(() => {
  const DESKTOP_MIN = 801;

  function desktopQuery(query) {
    const kept = [];
    for (const medium of String(query).split(",")) {
      const text = medium.trim();
      if (text.startsWith("not ")) {
        kept.push(text);
        continue;
      }
      let never = false;
      const parts = text.split(/\s+and\s+/).filter((part) => {
        const width = /^\((max|min)-width:\s*([\d.]+)px\)$/.exec(part);
        if (!width) return true;
        if (width[1] === "min") return Number(width[2]) > DESKTOP_MIN;
        if (Number(width[2]) < DESKTOP_MIN) never = true;
        return true;
      });
      if (!never) kept.push(parts.join(" and ") || "all");
    }
    return kept.join(", ") || "not all";
  }

  function rewrite(rules) {
    for (const rule of rules) {
      if (rule instanceof CSSImportRule) {
        if (rule.styleSheet) rewrite(rule.styleSheet.cssRules);
        continue;
      }
      if (rule instanceof CSSMediaRule) {
        const text = desktopQuery(rule.media.mediaText);
        if (text !== rule.media.mediaText) rule.media.mediaText = text;
      }
      if (rule.cssRules) rewrite(rule.cssRules);
    }
  }

  for (const sheet of document.styleSheets) {
    try {
      rewrite(sheet.cssRules);
    } catch (_) {} // cross-origin (Google Fonts) sheets are unreadable and carry no layout
  }

  const nativeMatchMedia = window.matchMedia.bind(window);
  window.matchMedia = (query) => nativeMatchMedia(desktopQuery(query));
})();
