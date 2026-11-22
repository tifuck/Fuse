// The chart kit: draws window.FUSE_CHART (a spec checked by app/artifacts.py
// _chart_text) into #chart as plain SVG in the viewer's theme (--fuse-*
// variables, --fuse-font, --fuse-scale). Inlined into every chart artifact
// page by _CHART_SHELL; no libraries, no network. Redraws on resize.
//
// Spec: {type, x, y, data, unit, prefix, headline, columns, value, sort}
//   line | area   change over time; y is one field or up to 4
//   bar           categories, horizontal, sorted by value (sort: false keeps order)
//   column        a value per period; several y fields stack (stacked is the same)
//   stat          key numbers: rows {label, value, delta, good, note}
//   table         rows; columns from the rows' keys or `columns`
//                 ([key | {key, label, kind: text|number|bar|spark|status, max}])
//   heatmap       x column, y row, value field
// Data is taken as written by a model: numbers may be strings ("1,234",
// "$9.50", "12%"), values may be missing, labels long and dates unsorted.
// Every label is measured and shortened to its room, and every kind keeps
// to MAX_H so an inline frame never cuts it off.
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var MAX_H = 580;
  var spec = window.FUSE_CHART || {};
  var root = document.getElementById("chart");
  var rows = Array.isArray(spec.data) ? spec.data.filter(function (r) { return r && typeof r === "object" && !Array.isArray(r); }) : [];
  var unit = typeof spec.unit === "string" ? spec.unit : "";
  var prefix = typeof spec.prefix === "string" ? spec.prefix : "";
  var P = {};
  var uid = 0;
  // The chat's text size (--fuse-scale): every size below is at 100%.
  var K = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--fuse-scale")) || 1;

  var style = document.createElement("style");
  style.textContent =
    "#chart svg{display:block;overflow:visible;font-family:var(--fuse-font)}" +
    ".fc-tip{position:absolute;pointer-events:none;z-index:2;white-space:nowrap;padding:4px 8px;border:1px solid var(--fuse-line);border-radius:6px;background:var(--fuse-raise);color:var(--fuse-text);font:calc(12px * var(--fuse-scale,1))/1.4 var(--fuse-font)}" +
    ".fc-stats{display:grid}" +
    ".fc-st{display:flex;flex-direction:column;justify-content:center;gap:2px;min-width:0;padding:12px 16px}" +
    ".fc-st.sep{border-left:1px solid var(--fuse-line)}.fc-st.top{border-top:1px solid var(--fuse-line)}.fc-st.first{padding-left:0}" +
    ".fc-sl,.fc-sd{font-size:calc(12px * var(--fuse-scale,1));white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.fc-sl{color:var(--fuse-muted)}" +
    ".fc-sv{font-weight:600;line-height:1.15;letter-spacing:-.01em;font-variant-numeric:tabular-nums;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}" +
    ".fc-sd span{color:var(--fuse-muted)}" +
    ".fc-tbl{width:100%;table-layout:fixed;border-collapse:collapse;font-size:calc(12.5px * var(--fuse-scale,1));font-variant-numeric:tabular-nums}" +
    ".fc-tbl th{height:calc(24px * var(--fuse-scale,1));padding:0 12px 4px 0;border-bottom:1px solid var(--fuse-line);color:var(--fuse-muted);font-size:calc(11.5px * var(--fuse-scale,1));font-weight:400;text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}" +
    ".fc-tbl td{height:calc(34px * var(--fuse-scale,1));padding:0 12px 0 0;border-bottom:1px solid var(--fuse-line);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}" +
    ".fc-tbl th:last-child,.fc-tbl td:last-child{padding-right:0}" +
    ".fc-tbl tr:last-child td{border-bottom:0}.fc-tbl .num{text-align:right}.fc-tbl .none{color:var(--fuse-muted)}" +
    ".fc-cell{display:flex;align-items:center;gap:6px;min-width:0}.fc-cell>span:last-child{overflow:hidden;text-overflow:ellipsis}" +
    ".fc-dot{flex:none;width:7px;height:7px;border-radius:50%}" +
    ".fc-track{flex:none;height:6px;overflow:hidden;border-radius:3px;background:var(--fuse-line)}.fc-fill{display:block;height:100%;border-radius:3px}" +
    ".fc-more{margin:6px 0 0;color:var(--fuse-muted);font-size:calc(12px * var(--fuse-scale,1))}";
  document.head.appendChild(style);

  // ---- helpers ----
  function cssVar(name, fallback) {
    return getComputedStyle(document.documentElement).getPropertyValue("--fuse-" + name).trim() || fallback;
  }
  function palette() {
    P = {
      bg: cssVar("bg", "#000"), text: cssVar("text", "#ececec"), muted: cssVar("muted", "#9a9a9a"),
      line: cssVar("line", "#262626"), accent: cssVar("accent", "#a5d8ff"), ok: cssVar("ok", "#aed397"), err: cssVar("error", "#f29d8e"),
    };
  }
  function el(tag, attrs, parent) {
    var node = document.createElementNS(NS, tag);
    for (var key in attrs) node.setAttribute(key, attrs[key]);
    if (parent) parent.appendChild(node);
    return node;
  }
  function text(parent, x, y, str, attrs) {
    attrs = Object.assign({ x: x, y: y }, attrs || {});
    if (attrs["font-size"]) attrs["font-size"] = attrs["font-size"] * K;
    var node = el("text", attrs, parent);
    node.textContent = str;
    return node;
  }
  var measured = {};
  function width(svg, str, size, weight) {
    var key = size + "|" + (weight || 400) + "|" + str;
    if (measured[key] == null) {
      var node = text(svg, -9999, -9999, str, { "font-size": size, "font-weight": weight || 400 });
      measured[key] = node.getComputedTextLength();
      node.remove();
    }
    return measured[key];
  }
  // str cut to fit max px, with an ellipsis.
  function fit(svg, str, max, size, weight) {
    str = String(str);
    if (width(svg, str, size, weight) <= max) return str;
    var lo = 0, hi = str.length;
    while (lo < hi) {
      var mid = Math.ceil((lo + hi) / 2);
      if (width(svg, str.slice(0, mid) + "…", size, weight) <= max) lo = mid; else hi = mid - 1;
    }
    return lo ? str.slice(0, lo) + "…" : "";
  }
  // Axis labels centred on their x, kept inside 0..W and shortened to room
  // px. One that would touch the label before it is left out; the last one
  // is kept over its neighbour.
  function xLabels(svg, items, y, room, W) {
    var kept = [];
    items.forEach(function (it, i) {
      var s = fit(svg, it.text, room, 11), w = width(svg, s, 11);
      if (!s) return;
      var left = Math.max(0, Math.min(W - w, it.x - w / 2)), box = { s: s, left: left, right: left + w };
      while (kept.length && box.left < kept[kept.length - 1].right + 8) {
        if (i !== items.length - 1) return;
        kept.pop();
      }
      kept.push(box);
    });
    kept.forEach(function (b) { text(svg, b.left, y, b.s, { fill: P.muted, "font-size": 11 }); });
  }
  function esc(str) {
    return String(str).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; });
  }
  // A number from a number or a number-like string ("1,234", "$9.50", "12%");
  // NaN for anything else, which charts draw as missing.
  function num(v) {
    if (typeof v === "number") return isFinite(v) ? v : NaN;
    if (typeof v !== "string") return NaN;
    var s = v.replace(/[\s,_$€£¥%]/g, "").replace(/^\+/, "");
    return /^-?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$/i.test(s) ? Number(s) : NaN;
  }
  function has(v) { return !isNaN(num(v)); }
  function trim(n) { return n.toFixed(1).replace(/\.0$/, ""); }
  function compact(n) {
    if (!isFinite(n)) return "–";
    if (n === 0) return "0";
    var a = Math.abs(n);
    var s = a >= 1e12 ? trim(a / 1e12) + "T" : a >= 1e9 ? trim(a / 1e9) + "B" : a >= 1e6 ? trim(a / 1e6) + "M" : a >= 1e4 ? Math.round(a / 1e3) + "k"
      : a >= 1e3 ? trim(a / 1e3) + "k" : String(Math.round(a * 100) / 100);
    return (n < 0 ? "-" : "") + prefix + s + unit;
  }
  function full(n) {
    if (!isFinite(n)) return "–";
    return (n < 0 ? "-" : "") + prefix + (Math.round(Math.abs(n) * 100) / 100).toLocaleString("en-US") + unit;
  }
  // A cell or stat as written: numbers formatted, objects as short JSON.
  function shown(v) {
    if (v == null) return "";
    if (typeof v === "number") return full(v);
    if (typeof v === "boolean") return v ? "yes" : "no";
    if (typeof v === "object") { try { return JSON.stringify(v).slice(0, 120); } catch (_) { return ""; } }
    return String(v);
  }
  function asDate(v) {
    if (typeof v !== "string" || !/^\d{4}-\d{2}(-\d{2})?$/.test(v)) return null;
    var d = new Date(v.length === 7 ? v + "-01" : v);
    return isNaN(d) ? null : d;
  }
  function label(v) {
    if (typeof v === "number") return String(v);
    var d = asDate(v);
    if (!d) return shown(v);
    return v.length === 7 ? d.toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" })
      : d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  }
  var DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  // Order for labels that have one (dates, weeks, times, numbers, weekdays);
  // null when they do not, so the data's own order stands.
  function sortKey(values) {
    var all = function (test) { return values.length && values.every(test); };
    if (all(function (v) { return typeof v === "string" && /^\d{4}-(\d{2}(-\d{2})?|W\d{1,2})([T ][\d:]+)?$/.test(v); })) return function (v) { return v; };
    if (all(function (v) { return typeof v === "string" && /^\d{1,2}(:\d{2}){1,2}$/.test(v); })) return function (v) { return v.padStart(8, "0"); };
    if (all(function (v) { return has(v); })) return function (v) { return num(v); };
    if (all(function (v) { return typeof v === "string" && DAYS.indexOf(v.slice(0, 3).toLowerCase()) >= 0; })) return function (v) { return DAYS.indexOf(v.slice(0, 3).toLowerCase()); };
    return null;
  }
  function sorted(list, field) {
    var key = sortKey(list.map(function (r) { return r[field]; }));
    if (!key) return list;
    return list.slice().sort(function (a, b) { var ka = key(a[field]), kb = key(b[field]); return ka < kb ? -1 : ka > kb ? 1 : 0; });
  }
  function nice(lo, hi, count) {
    if (hi <= lo) hi = lo + 1;
    var raw = (hi - lo) / count, p = Math.pow(10, Math.floor(Math.log10(raw)));
    var step = [1, 2, 2.5, 5, 10].map(function (m) { return m * p; }).find(function (s) { return s >= raw; });
    var bottom = Math.floor(lo / step) * step, top = Math.ceil(hi / step) * step, ticks = [];
    for (var v = bottom; v <= top + step / 1e6; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
    return { lo: bottom, hi: top, ticks: ticks };
  }
  function keysOf(list) {
    var keys = [];
    list.forEach(function (r) { Object.keys(r).forEach(function (k) { if (keys.indexOf(k) < 0) keys.push(k); }); });
    return keys;
  }
  function fields() {
    var keys = keysOf(rows);
    var numeric = function (k) { return rows.some(function (r) { return has(r[k]); }); };
    var x = typeof spec.x === "string" ? spec.x : keys.find(function (k) { return !numeric(k); }) || keys[0];
    var y = (Array.isArray(spec.y) ? spec.y : spec.y ? [spec.y] : keys.filter(function (k) { return k !== x && numeric(k); }))
      .filter(function (k) { return typeof k === "string"; }).slice(0, 4);
    return { x: x, y: y };
  }
  function svgIn(w, h) {
    root.innerHTML = "";
    return el("svg", { width: w, height: h, viewBox: "0 0 " + w + " " + h, role: "img" }, root);
  }
  function size(svg, w, h) {
    svg.setAttribute("height", h);
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);
  }
  function tip() {
    var node = document.createElement("div");
    node.className = "fc-tip";
    node.hidden = true;
    root.appendChild(node);
    return node;
  }
  function place(node, x, y, str) {
    node.textContent = str;
    node.hidden = false;
    var w = node.offsetWidth;
    node.style.left = Math.max(0, Math.min(root.clientWidth - w, x - w / 2)) + "px";
    node.style.top = Math.max(0, y - 36) + "px";
  }
  function plotHeight(w) { return Math.round(Math.max(200, Math.min(380, w * 0.48))); }
  // An optional line over the chart: {value, label, delta, good}. Label and
  // delta go beside the value when they fit, else on a line of their own.
  function headline(svg, W) {
    var h = spec.headline;
    if (!h || typeof h !== "object") return 0;
    var big = 20, value = h.value == null ? "" : shown(h.value);
    var vw = value ? width(svg, value, big, 600) + 10 : 0;
    var parts = [];
    if (h.label) parts.push([String(h.label), P.muted]);
    if (h.delta) parts.push([String(h.delta), h.good === true ? P.ok : h.good === false ? P.err : P.muted]);
    var whole = parts.map(function (p) { return p[0]; }).join(" · ");
    var beside = !value || vw + width(svg, whole, 12.5) <= W;
    if (value) text(svg, 0, big * K, value, { fill: P.text, "font-size": big, "font-weight": 600 });
    if (!parts.length) return (big + 14) * K;
    var x = beside ? vw : 0, y = beside ? big * K : (big + 18) * K, room = W - x;
    var line = text(svg, x, y, "", { "font-size": 12.5 });
    parts.forEach(function (p, i) {
      var s = (i ? " · " : "") + p[0];
      var cut = fit(svg, s, room, 12.5);
      room -= width(svg, cut, 12.5);
      if (cut) el("tspan", { fill: p[1] }, line).textContent = cut;
    });
    return (beside ? big + 14 : big + 32) * K;
  }
  var SERIES = function () { return [[P.accent, 1], [P.ok, 1], [P.muted, 1], [P.text, 0.7]]; };
  // x positions to label: all when they fit (at most most), else an even
  // spread that ends on the last.
  function xMarks(svg, list, field, slot, n, most) {
    var widest = Math.max.apply(null, list.map(function (r) { return width(svg, label(r[field]), 11); })) + 12;
    var every = Math.max(1, Math.ceil(widest / Math.max(1, slot)), Math.ceil(n / (most || n)));
    var marks = [];
    for (var i = n - 1; i >= 0; i -= every) marks.unshift(i);
    if (marks.length > 1 && marks[0] !== 0 && every > 1 && marks[0] >= every / 2) marks.unshift(0);
    if (marks.length > 1 && marks[1] - marks[0] < every) marks.splice(1, 1);
    return { marks: marks, room: slot * every - 8 };
  }

  // ---- line / area ----
  function drawLine(area) {
    var f = fields(), W = root.clientWidth;
    if (!f.y.length) throw new Error("line charts need a numeric y field");
    var list = sorted(rows, f.x), n = list.length;
    var svg = svgIn(W, 10), headH = headline(svg, W);
    var H = headH + plotHeight(W);
    size(svg, W, H);
    var series = f.y.map(function (k) { return list.map(function (r) { return num(r[k]); }); });
    var all = [].concat.apply([], series).filter(isFinite);
    if (!all.length) throw new Error("no numbers in " + f.y.join(", "));
    var sc = nice(Math.min(0, Math.min.apply(null, all)), Math.max.apply(null, all), 3);
    var yw = Math.max.apply(null, sc.ticks.map(function (t) { return width(svg, compact(t), 11); })) + 10;
    var multi = series.length > 1;
    var lastOf = function (s) { for (var i = s.length - 1; i >= 0; i--) if (isFinite(s[i])) return i; return -1; };
    var ends = series.map(function (s, i) { var j = lastOf(s); return (multi ? f.y[i] + " " : "") + compact(s[j]); });
    var lw = Math.min(W * 0.3, Math.max.apply(null, ends.map(function (s) { return width(svg, s, 12, 600); })) + 12);
    var x0 = yw, x1 = Math.max(x0 + 40, W - lw), y0 = headH + 14 * K, y1 = H - 22 * K;
    var X = function (i) { return n > 1 ? x0 + i * (x1 - x0) / (n - 1) : (x0 + x1) / 2; };
    var Y = function (v) { return y1 - (v - sc.lo) / (sc.hi - sc.lo) * (y1 - y0); };
    sc.ticks.forEach(function (t) {
      el("line", { x1: x0, x2: W, y1: Y(t), y2: Y(t), stroke: P.line, "stroke-width": 1 }, svg);
      text(svg, x0 - 10, Y(t) + 4 * K, compact(t), { fill: P.muted, "font-size": 11, "text-anchor": "end" });
    });
    var colors = SERIES(), endLabels = [], base = Y(Math.max(sc.lo, Math.min(0, sc.hi)));
    series.forEach(function (s, si) {
      var c = colors[si], runs = [], run = [];
      s.forEach(function (v, i) { if (isFinite(v)) run.push([X(i), Y(v)]); else if (run.length) { runs.push(run); run = []; } });
      if (run.length) runs.push(run);
      var id = "fcg" + (++uid);
      if (!multi || area) {
        var gr = el("linearGradient", { id: id, x1: 0, y1: 0, x2: 0, y2: 1 }, el("defs", {}, svg));
        el("stop", { offset: "0", "stop-color": c[0], "stop-opacity": multi ? 0.12 : 0.24 }, gr);
        el("stop", { offset: "1", "stop-color": c[0], "stop-opacity": 0 }, gr);
      }
      runs.forEach(function (pts) {
        var d = pts.map(function (p, i) { return (i ? "L" : "M") + p[0].toFixed(1) + "," + p[1].toFixed(1); }).join("");
        if (!multi || area) el("path", { d: "M" + pts[0][0] + "," + base + d.replace(/^M/, "L") + "L" + pts[pts.length - 1][0] + "," + base + "Z", fill: "url(#" + id + ")" }, svg);
        if (pts.length === 1) el("circle", { cx: pts[0][0], cy: pts[0][1], r: 2, fill: c[0] }, svg);
        else el("path", { d: d, fill: "none", stroke: c[0], "stroke-opacity": c[1], "stroke-width": 1.75, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
      });
      var j = lastOf(s);
      el("circle", { cx: X(j), cy: Y(s[j]), r: 3.5, fill: c[0] }, svg);
      endLabels.push({ y: Y(s[j]) + 4 * K, text: ends[si], color: si ? P.muted : P.text, x: X(j) + 8 });
      var finite = s.filter(isFinite), max = Math.max.apply(null, finite);
      if (!multi && finite.length > 2 && max > Math.min.apply(null, finite)) {
        var mi = s.indexOf(max);
        if (mi !== j) {
          el("circle", { cx: X(mi), cy: Y(max), r: 3, fill: P.bg, stroke: c[0], "stroke-width": 1.5 }, svg);
          var peak = "peak " + compact(max), pw = width(svg, peak, 11);
          text(svg, Math.max(x0, Math.min(x1 - pw, X(mi) - pw / 2)), Math.max(headH + 11 * K, Y(max) - 9), peak, { fill: P.muted, "font-size": 11 });
        }
      }
    });
    endLabels.sort(function (a, b) { return a.y - b.y; }).forEach(function (l, i, list2) {
      if (i && l.y - list2[i - 1].y < 14 * K) l.y = list2[i - 1].y + 14 * K;
      text(svg, l.x, l.y, fit(svg, l.text, W - l.x, 12, 600), { fill: l.color, "font-size": 12, "font-weight": 600 });
    });
    var xm = xMarks(svg, list, f.x, n > 1 ? (x1 - x0) / (n - 1) : x1 - x0, n, W < 420 ? 3 : 6);
    xLabels(svg, xm.marks.map(function (i) { return { x: X(i), text: label(list[i][f.x]) }; }), H - 4 * K, Math.max(48, Math.min(xm.room, W / 2)), W);
    var rule = el("line", { y1: y0, y2: y1, stroke: P.muted, "stroke-dasharray": "2 3", visibility: "hidden" }, svg);
    var hint = tip();
    svg.addEventListener("pointermove", function (e) {
      var r = svg.getBoundingClientRect(), i = n > 1 ? Math.round((e.clientX - r.left - x0) / (x1 - x0) * (n - 1)) : 0;
      i = Math.max(0, Math.min(n - 1, i));
      rule.setAttribute("x1", X(i)); rule.setAttribute("x2", X(i)); rule.setAttribute("visibility", "visible");
      var vals = series.map(function (s, si) { return (multi ? f.y[si] + " " : "") + full(s[i]); }).join(" · ");
      var ys = series.map(function (s) { return isFinite(s[i]) ? Y(s[i]) : y1; });
      place(hint, X(i), Math.min.apply(null, ys), label(list[i][f.x]) + " · " + vals);
    });
    svg.addEventListener("pointerleave", function () { rule.setAttribute("visibility", "hidden"); hint.hidden = true; });
  }

  // ---- horizontal bars (negatives grow left of a zero line) ----
  function drawBars() {
    var f = fields(), k = f.y[0], W = root.clientWidth;
    if (!k) throw new Error("bar charts need a numeric y field");
    var list = rows.filter(function (r) { return has(r[k]); });
    if (!list.length) throw new Error("no numbers in " + k);
    if (spec.sort !== false) list.sort(function (a, b) { return num(b[k]) - num(a[k]); });
    var svg = svgIn(W, 10), headH = headline(svg, W), rowH = 30 * K;
    var fitRows = Math.max(3, Math.floor((MAX_H - headH - 22 * K) / rowH));
    var extra = list.length > fitRows ? list.length - (fitRows - 1) : 0;
    if (extra) list = list.slice(0, fitRows - 1);
    var H = headH + list.length * rowH + (extra ? 22 * K : 0);
    size(svg, W, H);
    var vals = list.map(function (r) { return num(r[k]); });
    var lo = Math.min(0, Math.min.apply(null, vals)), hi = Math.max(0, Math.max.apply(null, vals)), spanV = hi - lo || 1;
    var total = vals.reduce(function (a, v) { return a + Math.max(0, v); }, 0);
    var lw = Math.min(W * 0.38, Math.max.apply(null, list.map(function (r) { return width(svg, label(r[f.x]), 12.5); })));
    var vw = Math.max.apply(null, vals.map(function (v) { return width(svg, compact(v), 12, 600); })) + 10;
    var left = lw + 14 + (lo < 0 ? vw : 0), span = Math.max(20, W - left - (hi > 0 ? vw : 0)), bh = 14 * K;
    var zero = left + (0 - lo) / spanV * span;
    if (lo < 0) el("line", { x1: zero, x2: zero, y1: headH, y2: headH + list.length * rowH, stroke: P.line, "stroke-width": 1 }, svg);
    list.forEach(function (r, i) {
      var cy = headH + i * rowH + rowH / 2, v = vals[i], g = el("g", {}, svg);
      el("title", {}, g).textContent = label(r[f.x]) + ": " + full(v) + (total > 0 && v > 0 ? " (" + (v / total * 100).toFixed(1) + "%)" : "");
      text(g, 0, cy + 4 * K, fit(svg, label(r[f.x]), lw, 12.5), { fill: P.text, "font-size": 12.5 });
      var w = Math.max(2, Math.abs(v) / spanV * span), x = v < 0 ? zero - w : zero;
      el("rect", { x: x, y: cy - bh / 2, width: w, height: bh, rx: 2, fill: v < 0 ? P.muted : P.accent, "fill-opacity": i === 0 && v > 0 ? 1 : v < 0 ? 0.6 : 0.42 }, g);
      var lead = i === 0 && v > 0;
      text(g, v < 0 ? x - 8 : x + w + 8, cy + 4 * K, compact(v), { fill: lead ? P.text : P.muted, "font-size": 12, "font-weight": lead ? 600 : 400, "text-anchor": v < 0 ? "end" : "start" });
    });
    if (extra) text(svg, 0, H - 6 * K, "+" + extra + " more", { fill: P.muted, "font-size": 12 });
  }

  // ---- columns (stacked when several y fields; negatives stack down) ----
  function drawColumns() {
    var f = fields(), W = root.clientWidth;
    if (!f.y.length) throw new Error("column charts need a numeric y field");
    var list = sorted(rows, f.x), n = list.length;
    var multi = f.y.length > 1, svg = svgIn(W, 10), headH = headline(svg, W), legendH = 0;
    var fills = [[P.accent, 1], [P.accent, 0.42], [P.muted, 0.5], [P.ok, 0.8]];
    if (multi) {
      var lx = 0, ly = headH;
      f.y.forEach(function (k, i) {
        var lw2 = 14 + width(svg, k, 12) + 18;
        if (lx && lx + lw2 > W) { lx = 0; ly += 18 * K; }
        el("rect", { x: lx, y: ly + 4 * K, width: 9 * K, height: 9 * K, rx: 2, fill: fills[i][0], "fill-opacity": fills[i][1] }, svg);
        text(svg, lx + 14 * K, ly + 12.5 * K, fit(svg, k, W - lx - 14, 12), { fill: P.muted, "font-size": 12 });
        lx += lw2;
      });
      legendH = ly - headH + 24 * K;
    }
    var H = headH + legendH + plotHeight(W);
    size(svg, W, H);
    var ups = list.map(function (r) { return f.y.reduce(function (a, k) { var v = num(r[k]); return a + (v > 0 ? v : 0); }, 0); });
    var downs = list.map(function (r) { return f.y.reduce(function (a, k) { var v = num(r[k]); return a + (v < 0 ? v : 0); }, 0); });
    var sc = nice(Math.min(0, Math.min.apply(null, downs)), Math.max(0, Math.max.apply(null, ups)), 3);
    var yw = Math.max.apply(null, sc.ticks.map(function (t) { return width(svg, compact(t), 11); })) + 10;
    var x0 = yw, y0 = headH + legendH + 14 * K, y1 = H - 22 * K, band = (W - x0) / n, cw = Math.max(1, Math.min(46, band * 0.62));
    var Y = function (v) { return y1 - (v - sc.lo) / (sc.hi - sc.lo) * (y1 - y0); };
    sc.ticks.forEach(function (t) {
      el("line", { x1: x0, x2: W, y1: Y(t), y2: Y(t), stroke: t === 0 && sc.lo < 0 ? P.muted : P.line, "stroke-width": 1 }, svg);
      text(svg, x0 - 10, Y(t) + 4 * K, compact(t), { fill: P.muted, "font-size": 11, "text-anchor": "end" });
    });
    list.forEach(function (r, j) {
      var cx = x0 + band * j + band / 2, up = 0, down = 0, g = el("g", {}, svg);
      el("title", {}, g).textContent = label(r[f.x]) + ": " + f.y.map(function (k) { return (multi ? k + " " : "") + full(num(r[k])); }).join(", ");
      f.y.forEach(function (k, i) {
        var v = num(r[k]);
        if (!isFinite(v) || v === 0) return;
        var from = v > 0 ? up : down, to = from + v;
        if (v > 0) up = to; else down = to;
        var top = Math.min(Y(from), Y(to)), h = Math.abs(Y(to) - Y(from));
        el("rect", { x: cx - cw / 2, y: top, width: cw, height: Math.max(0.5, h - (multi ? 1.5 : 0)), rx: cw > 6 ? 1.5 : 0, fill: fills[i][0],
          "fill-opacity": multi ? fills[i][1] : j === n - 1 ? 1 : 0.55 }, g);
      });
      // The column's total over it; with negatives in the chart a net
      // total would not match the bar, so there is none.
      if (band >= 40 * K && up && sc.lo >= 0) text(g, cx, Y(up) - 7, fit(svg, compact(up), band - 4, 11), { fill: P.muted, "font-size": 11, "text-anchor": "middle" });
    });
    var xm = xMarks(svg, list, f.x, band, n, 16);
    xLabels(svg, xm.marks.map(function (j) { return { x: x0 + band * j + band / 2, text: label(list[j][f.x]) }; }), H - 4 * K, Math.max(40, Math.min(xm.room, W / 2)), W);
  }

  // ---- key numbers ----
  function drawStats() {
    var W = root.clientWidth, items = rows.slice(0, 8), n = items.length;
    var cols = W >= 560 ? Math.min(n, 4) : n === 1 ? 1 : 2, colW = W / cols - 32;
    var svg = svgIn(1, 1), base = Math.max(20, Math.min(34, (W / cols) * 0.16)) * K;
    // One size for every value, shrunk until the longest fits its column.
    var longest = items.reduce(function (a, it) { return Math.max(a, width(svg, shown(it.value), base / K, 600)); }, 0);
    var big = longest > colW ? Math.max(16 * K, base * colW / longest) : base;
    var showNote = W / cols >= 190;
    root.innerHTML = '<div class="fc-stats" style="grid-template-columns:repeat(' + cols + ',minmax(0,1fr))">' + items.map(function (it, i) {
      var cls = "fc-st" + (i % cols ? " sep" : " first") + (i >= cols ? " top" : "");
      var tone = it.good === true ? P.ok : it.good === false ? P.err : P.muted;
      var value = shown(it.value);
      var delta = it.delta != null && it.delta !== "" ? '<div class="fc-sd" style="color:' + tone + '">' + esc(shown(it.delta)) +
        (it.note && showNote ? "<span> " + esc(shown(it.note)) + "</span>" : "") + "</div>" : it.note ? '<div class="fc-sd"><span>' + esc(shown(it.note)) + "</span></div>" : "";
      return '<div class="' + cls + '" title="' + esc(shown(it.label) + ": " + value) + '"><div class="fc-sl">' + esc(shown(it.label)) + '</div><div class="fc-sv" style="font-size:' +
        big.toFixed(1) + 'px">' + (value ? esc(value) : '<span style="color:var(--fuse-muted)">–</span>') + "</div>" + delta + "</div>";
    }).join("") + "</div>";
  }

  // ---- table ----
  function spark(values, w, h) {
    var nums = values.map(num).filter(isFinite);
    if (nums.length < 2) return "";
    var lo = Math.min.apply(null, nums), hi = Math.max.apply(null, nums), d = hi - lo || 1;
    var pts = nums.map(function (v, i) { return (i * w / (nums.length - 1)).toFixed(1) + "," + (h - 1 - (v - lo) / d * (h - 2)).toFixed(1); }).join(" ");
    return '<svg width="' + w + '" height="' + h + '" aria-hidden="true" style="flex:none"><polyline points="' + pts + '" fill="none" stroke="' + esc(P.accent) + '" stroke-width="1.25" stroke-linejoin="round"/></svg>';
  }
  var GOOD = /^(ok|okay|healthy|up|pass|passed|passing|online|running|active|green|yes|true|done|success|ready)\b/i;
  var BAD = /^(down|fail|failed|failing|error|bad|critical|offline|red|no|false|dead|crashed)\b/i;
  function drawTable() {
    var W = root.clientWidth, svg = svgIn(1, 1);
    var cols = (Array.isArray(spec.columns) && spec.columns.length ? spec.columns : keysOf(rows)).map(function (c) {
      var col = typeof c === "string" ? { key: c } : c && typeof c === "object" ? c : null;
      if (!col || typeof col.key !== "string") return null;
      var vals = rows.map(function (r) { return r[col.key]; }).filter(function (v) { return v != null && v !== ""; });
      var kind = col.kind || (vals.some(Array.isArray) ? "spark"
        : vals.length && vals.filter(has).length >= vals.length / 2 ? "number" : "text");
      var nums = vals.map(num).filter(isFinite);
      var max = num(col.max) || Math.max.apply(null, nums.concat([1]));
      return { key: col.key, label: shown(col.label || col.key), kind: kind, max: max };
    }).filter(Boolean);
    var sparkW = W < 500 ? 44 : 72, track = W < 500 ? 36 : 56;
    // Each column's natural width (header or widest cell), then shrink the
    // text columns to fit; columns that still do not fit are left off.
    var natural = cols.map(function (c) {
      var cell = c.kind === "spark" ? sparkW + 6 + 30 : c.kind === "bar" ? track + 6 + 36 : c.kind === "status" ? 13 : 0;
      var words = rows.slice(0, 40).reduce(function (a, r) { return Math.max(a, width(svg, c.kind === "number" ? full(num(r[c.key])) : shown(r[c.key]), 12.5)); }, 0);
      // HTML text runs a little wider than the SVG measure; the 8% keeps
      // a number from being cut by its own ellipsis.
      return Math.max(width(svg, c.label, 11.5), cell + (c.kind === "spark" || c.kind === "bar" ? 0 : words * 1.08)) + 16;
    });
    // Widths for the first m columns: text columns give way down to 72px.
    var layout = function (m) {
      var widths = natural.slice(0, m), sum = widths.reduce(function (a, b) { return a + b; }, 0);
      if (sum <= W) return widths;
      var fixed = widths.reduce(function (a, w, i) { return a + (cols[i].kind === "text" ? 0 : w); }, 0);
      var flex = widths.reduce(function (a, w, i) { return a + (cols[i].kind === "text" ? w : 0); }, 0);
      var scale = Math.max(0, (W - fixed) / (flex || 1));
      return widths.map(function (w, i) { return cols[i].kind === "text" ? Math.max(72, w * scale) : w; });
    };
    var shown2 = cols.length, widths = layout(shown2);
    while (shown2 > 1 && widths.reduce(function (a, b) { return a + b; }, 0) > W + 0.5) widths = layout(--shown2);
    if (shown2 === 1) widths = [W];
    var dropped = cols.length - shown2;
    cols = cols.slice(0, shown2);
    var rowH = 34 * K, headH = 24 * K;
    var fitRows = Math.max(3, Math.floor((MAX_H - headH - 24 * K) / rowH));
    var list = rows.length > fitRows ? rows.slice(0, fitRows - 1) : rows;
    var cell = function (col, v) {
      if (v == null || v === "") return '<td class="none' + (col.kind === "number" ? " num" : "") + '">–</td>';
      if (col.kind === "spark" && Array.isArray(v)) {
        var last = v.map(num).filter(isFinite).pop();
        return '<td><span class="fc-cell">' + spark(v, sparkW, 16 * K) + "<span>" + esc(last == null ? "" : compact(last)) + "</span></span></td>";
      }
      if (col.kind === "bar" && has(v)) {
        var share = Math.max(0, Math.min(1, num(v) / col.max));
        return '<td><span class="fc-cell"><span class="fc-track" style="width:' + track + 'px"><span class="fc-fill" style="width:' + (share * 100).toFixed(1) +
          '%;background:' + esc(P.accent) + '"></span></span><span>' + esc(typeof v === "number" ? compact(v) : shown(v)) + "</span></span></td>";
      }
      if (col.kind === "status") {
        var s = shown(v), tone = BAD.test(s) ? P.err : GOOD.test(s) ? P.ok : P.muted;
        return '<td title="' + esc(s) + '"><span class="fc-cell"><span class="fc-dot" style="background:' + esc(tone) + '"></span><span>' + esc(s) + "</span></span></td>";
      }
      if (col.kind === "number") return '<td class="num" title="' + esc(shown(v)) + '">' + esc(has(v) && typeof v === "number" ? full(v) : shown(v)) + "</td>";
      return '<td title="' + esc(shown(v)) + '">' + esc(shown(v)) + "</td>";
    };
    var notes = [];
    if (rows.length > list.length) notes.push("+" + (rows.length - list.length) + " more rows");
    if (dropped) notes.push("+" + dropped + " more column" + (dropped > 1 ? "s" : ""));
    root.innerHTML = '<table class="fc-tbl"><colgroup>' + widths.map(function (w) { return '<col style="width:' + w.toFixed(0) + 'px">'; }).join("") +
      "</colgroup><thead><tr>" + cols.map(function (c) {
        return "<th" + (c.kind === "number" ? ' class="num"' : "") + ' title="' + esc(c.label) + '">' + esc(c.label) + "</th>";
      }).join("") + "</tr></thead><tbody>" + list.map(function (r) {
        return "<tr>" + cols.map(function (c) { return cell(c, r[c.key]); }).join("") + "</tr>";
      }).join("") + "</tbody></table>" + (notes.length ? '<p class="fc-more">' + notes.join(" · ") + "</p>" : "");
  }

  // ---- heatmap ----
  function drawHeat() {
    var f = fields(), vk = typeof spec.value === "string" ? spec.value : f.y[0], W = root.clientWidth;
    var yk = Array.isArray(spec.y) ? spec.y[0] : spec.y;
    if (!vk || !yk || vk === yk) {
      var keys = keysOf(rows);
      yk = yk && yk !== vk ? yk : keys.find(function (k) { return k !== f.x && k !== vk; });
    }
    if (!vk || !yk) throw new Error("heatmaps need x, y and value fields");
    var cells = {}, xs = [], ys = [];
    rows.forEach(function (r) {
      var x = r[f.x], y = r[yk];
      if (xs.indexOf(x) < 0) xs.push(x);
      if (ys.indexOf(y) < 0) ys.push(y);
      var v = num(r[vk]), key = String(y) + "\u0000" + String(x);
      if (isFinite(v)) cells[key] = (cells[key] || 0) + v;
    });
    var kx = sortKey(xs), ky = sortKey(ys);
    var by = function (key) { return function (a, b) { var ka = key(a), kb = key(b); return ka < kb ? -1 : ka > kb ? 1 : 0; }; };
    if (kx) xs.sort(by(kx));
    if (ky) ys.sort(by(ky));
    var vals = Object.keys(cells).map(function (k) { return cells[k]; });
    if (!vals.length) throw new Error("no numbers in " + vk);
    var max = Math.max.apply(null, vals) || 1;
    var svg = svgIn(W, 10), best = Object.keys(cells).reduce(function (a, k) { return cells[k] > cells[a] ? k : a; });
    var top = 26 * K, left = Math.min(W * 0.3, Math.max.apply(null, ys.map(function (y) { return width(svg, label(y), 11); })) + 10);
    var cw = (W - left) / xs.length, ch = Math.max(10, Math.min(24 * K, cw)), gap = cw >= 8 ? 2 : cw >= 4 ? 1 : 0;
    var rowsShown = Math.min(ys.length, Math.max(3, Math.floor((MAX_H - top - 18 * K) / ch)));
    var H = top + rowsShown * ch + 18 * K;
    size(svg, W, H);
    var parts = best.split("\u0000"), head = text(svg, 0, 13 * K, "", { "font-size": 12.5 }), room = W;
    [["Highest  ", P.muted, 400], [label(parts[0]) + " " + label(parts[1]), P.text, 600], [" · " + full(cells[best]), P.muted, 400]].forEach(function (p) {
      var cut = fit(svg, p[0], room, 12.5, p[2]);
      room -= width(svg, cut, 12.5, p[2]);
      if (cut) el("tspan", { fill: p[1], "font-weight": p[2] }, head).textContent = cut;
    });
    var rowEvery = ch >= 15 ? 1 : 2;
    ys.slice(0, rowsShown).forEach(function (y, yi) {
      if (yi % rowEvery === 0) text(svg, left - 8, top + yi * ch + ch / 2 + 4 * K, fit(svg, label(y), left - 10, 11), { fill: P.muted, "font-size": 11, "text-anchor": "end" });
      xs.forEach(function (x, xi) {
        var v = cells[String(y) + "\u0000" + String(x)], on = v != null && v > 0;
        var r = el("rect", { x: left + xi * cw, y: top + yi * ch, width: Math.max(0.5, cw - gap), height: ch - gap, rx: cw > 8 ? 2 : 0,
          fill: on ? P.accent : P.line, "fill-opacity": on ? (0.06 + 0.94 * Math.pow(v / max, 1.1)).toFixed(3) : 1 }, svg);
        el("title", {}, r).textContent = label(y) + " " + label(x) + ": " + (v == null ? "no data" : full(v));
      });
    });
    var xm = xMarks(svg, xs.map(function (x) { return { x: x }; }), "x", cw, xs.length, 16);
    xLabels(svg, xm.marks.map(function (xi) { return { x: left + xi * cw + cw / 2, text: label(xs[xi]) }; }), top + rowsShown * ch + 13 * K, Math.max(40, Math.min(xm.room, W / 2)), W);
  }

  var DRAW = { line: function () { drawLine(false); }, area: function () { drawLine(true); }, bar: drawBars, column: drawColumns, stacked: drawColumns, stat: drawStats, table: drawTable, heatmap: drawHeat };

  function draw() {
    palette();
    try {
      if (!rows.length) throw new Error("no data rows");
      (DRAW[spec.type] || drawTable)();
    } catch (error) {
      root.innerHTML = '<p style="margin:0;color:var(--fuse-muted);font-size:13px">Could not draw this chart: ' + esc(error.message || error) + "</p>";
      throw error;
    }
  }

  var lastWidth = 0, pending = 0;
  window.addEventListener("resize", function () {
    if (root.clientWidth === lastWidth || pending) return;
    pending = requestAnimationFrame(function () { pending = 0; lastWidth = root.clientWidth; draw(); });
  });
  lastWidth = root.clientWidth;
  draw();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { measured = {}; draw(); });
})();
