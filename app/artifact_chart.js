// The chart kit: draws window.FUSE_CHART (a spec checked by app/artifacts.py
// _chart_text) into #chart as plain SVG in the viewer's theme (--fuse-*
// variables, --fuse-font). Inlined into every chart artifact page by
// _CHART_SHELL; no libraries, no network. Redraws on resize.
//
// Spec: {type, x, y, data, unit, prefix, headline, columns, value, sort}
//   line | area   change over time; y is one field or up to 4
//   bar           categories, horizontal, sorted by value (sort: false keeps order)
//   column        a value per period; several y fields stack (stacked is the same)
//   stat          key numbers: rows {label, value, delta, good}
//   table         rows; columns from the first row or `columns`
//                 ([key | {key, label, kind: text|number|bar|spark|status, max}])
//   heatmap       x column, y row, value field
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var spec = window.FUSE_CHART || {};
  var root = document.getElementById("chart");
  var rows = Array.isArray(spec.data) ? spec.data.filter(function (r) { return r && typeof r === "object"; }) : [];
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
    ".fc-tbl{width:100%;border-collapse:collapse;font-size:calc(12.5px * var(--fuse-scale,1));font-variant-numeric:tabular-nums}" +
    ".fc-tbl th{height:calc(24px * var(--fuse-scale,1));padding:0 12px 4px 0;border-bottom:1px solid var(--fuse-line);color:var(--fuse-muted);font-size:calc(11.5px * var(--fuse-scale,1));font-weight:400;text-align:left;white-space:nowrap}" +
    ".fc-tbl td{height:calc(34px * var(--fuse-scale,1));padding:0 12px 0 0;border-bottom:1px solid var(--fuse-line);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:22rem}" +
    ".fc-tbl tr:last-child td{border-bottom:0}.fc-tbl .num{text-align:right}" +
    ".fc-cell{display:flex;align-items:center;gap:6px}.fc-dot{flex:none;width:7px;height:7px;border-radius:50%}" +
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
  function width(svg, str, size, weight) {
    var node = text(svg, -9999, -9999, str, { "font-size": size, "font-weight": weight || 400 });
    var w = node.getComputedTextLength();
    node.remove();
    return w;
  }
  function esc(str) {
    return String(str).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; });
  }
  function num(v) { var n = Number(v); return isFinite(n) ? n : 0; }
  function trim(n) { return n.toFixed(1).replace(/\.0$/, ""); }
  function compact(n) {
    if (n === 0) return "0";
    var a = Math.abs(n);
    var s = a >= 1e9 ? trim(n / 1e9) + "B" : a >= 1e6 ? trim(n / 1e6) + "M" : a >= 1e4 ? Math.round(n / 1e3) + "k"
      : a >= 1e3 ? trim(n / 1e3) + "k" : String(Math.round(n * 100) / 100);
    return prefix + s + unit;
  }
  function full(n) { return prefix + (Math.round(n * 100) / 100).toLocaleString("en-US") + unit; }
  function shown(v) { return typeof v === "number" ? full(v) : String(v == null ? "" : v); }
  function asDate(v) {
    if (typeof v !== "string" || !/^\d{4}-\d{2}(-\d{2})?$/.test(v)) return null;
    var d = new Date(v.length === 7 ? v + "-01" : v);
    return isNaN(d) ? null : d;
  }
  function label(v) {
    var d = asDate(v);
    if (!d) return String(v == null ? "" : v);
    return v.length === 7 ? d.toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" })
      : d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  }
  function nice(lo, hi, count) {
    if (hi <= lo) hi = lo + 1;
    var raw = (hi - lo) / count, p = Math.pow(10, Math.floor(Math.log10(raw)));
    var step = [1, 2, 2.5, 5, 10].map(function (m) { return m * p; }).find(function (s) { return s >= raw; });
    var bottom = Math.floor(lo / step) * step, top = Math.ceil(hi / step) * step, ticks = [];
    for (var v = bottom; v <= top + step / 1e6; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
    return { lo: bottom, hi: top, ticks: ticks };
  }
  function fields() {
    var keys = rows.length ? Object.keys(rows[0]) : [];
    var x = typeof spec.x === "string" ? spec.x : keys.find(function (k) { return typeof rows[0][k] === "string"; }) || keys[0];
    var y = (Array.isArray(spec.y) ? spec.y : spec.y ? [spec.y] : keys.filter(function (k) { return k !== x && typeof rows[0][k] === "number"; }))
      .filter(function (k) { return typeof k === "string"; }).slice(0, 4);
    return { x: x, y: y };
  }
  function svgIn(w, h) {
    root.innerHTML = "";
    return el("svg", { width: w, height: h, viewBox: "0 0 " + w + " " + h, role: "img" }, root);
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
  // An optional line over the chart: {value, label, delta, good}.
  function headline(svg, top) {
    var h = spec.headline;
    if (!h || typeof h !== "object") return 0;
    var big = 20, x = 0;
    if (h.value != null) {
      text(svg, 0, top + big * K, shown(h.value), { fill: P.text, "font-size": big, "font-weight": 600 });
      x = width(svg, shown(h.value), big, 600) + 10;
    }
    var line = text(svg, x, top + big * K, "", { "font-size": 12.5 });
    if (h.label) el("tspan", { fill: P.muted }, line).textContent = String(h.label) + (h.delta ? " · " : "");
    if (h.delta) el("tspan", { fill: h.good === true ? P.ok : h.good === false ? P.err : P.muted }, line).textContent = String(h.delta);
    return (big + 14) * K;
  }
  var SERIES = function () { return [[P.accent, 1], [P.ok, 1], [P.muted, 1], [P.text, 0.7]]; };

  // ---- line / area ----
  function drawLine(area) {
    var f = fields(), n = rows.length, W = root.clientWidth;
    if (!f.y.length) throw new Error("line charts need a numeric y field");
    var svg = svgIn(W, 10), headH = headline(svg, 0);
    var H = headH + plotHeight(W);
    svg.setAttribute("height", H);
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    var series = f.y.map(function (k) { return rows.map(function (r) { return num(r[k]); }); });
    var all = [].concat.apply([], series), lo = Math.min(0, Math.min.apply(null, all)), sc = nice(lo, Math.max.apply(null, all), 3);
    var yw = Math.max.apply(null, sc.ticks.map(function (t) { return width(svg, compact(t), 11); })) + 10;
    var multi = series.length > 1;
    var ends = series.map(function (s, i) { return (multi ? f.y[i] + " " : "") + compact(s[n - 1]); });
    var lw = Math.max.apply(null, ends.map(function (s) { return width(svg, s, 12, 600); })) + 12;
    var x0 = yw, x1 = Math.max(x0 + 40, W - lw), y0 = headH + 10, y1 = H - 22;
    var X = function (i) { return n > 1 ? x0 + i * (x1 - x0) / (n - 1) : (x0 + x1) / 2; };
    var Y = function (v) { return y1 - (v - sc.lo) / (sc.hi - sc.lo) * (y1 - y0); };
    sc.ticks.forEach(function (t) {
      el("line", { x1: x0, x2: W, y1: Y(t), y2: Y(t), stroke: P.line, "stroke-width": 1 }, svg);
      text(svg, x0 - 10, Y(t) + 4, compact(t), { fill: P.muted, "font-size": 11, "text-anchor": "end" });
    });
    var colors = SERIES(), labelsY = [];
    series.forEach(function (s, si) {
      var c = colors[si], pts = s.map(function (v, i) { return [X(i), Y(v)]; });
      var d = pts.map(function (p, i) { return (i ? "L" : "M") + p[0].toFixed(1) + "," + p[1].toFixed(1); }).join("");
      if (!multi || area) {
        var id = "fcg" + (++uid), gr = el("linearGradient", { id: id, x1: 0, y1: 0, x2: 0, y2: 1 }, el("defs", {}, svg));
        el("stop", { offset: "0", "stop-color": c[0], "stop-opacity": multi ? 0.12 : 0.24 }, gr);
        el("stop", { offset: "1", "stop-color": c[0], "stop-opacity": 0 }, gr);
        el("path", { d: "M" + X(0) + "," + Y(Math.max(sc.lo, 0)) + d.replace(/^M/, "L") + "L" + X(n - 1) + "," + Y(Math.max(sc.lo, 0)) + "Z", fill: "url(#" + id + ")" }, svg);
      }
      el("path", { d: d, fill: "none", stroke: c[0], "stroke-opacity": c[1], "stroke-width": 1.75, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
      var last = pts[n - 1];
      el("circle", { cx: last[0], cy: last[1], r: 3.5, fill: c[0] }, svg);
      labelsY.push({ y: last[1] + 4, text: ends[si], color: si ? P.muted : P.text, x: last[0] + 8 });
      if (!multi && n > 2) {
        var max = Math.max.apply(null, s), mi = s.indexOf(max), mp = pts[mi];
        if (mi !== n - 1) {
          el("circle", { cx: mp[0], cy: mp[1], r: 3, fill: P.bg, stroke: c[0], "stroke-width": 1.5 }, svg);
          text(svg, Math.max(x0 + 36, Math.min(x1 - 36, mp[0])), mp[1] - 9, "peak " + compact(max), { fill: P.muted, "font-size": 11, "text-anchor": "middle" });
        }
      }
    });
    labelsY.sort(function (a, b) { return a.y - b.y; }).forEach(function (l, i, list) {
      if (i && l.y - list[i - 1].y < 14) l.y = list[i - 1].y + 14;
      text(svg, l.x, l.y, l.text, { fill: l.color, "font-size": 12, "font-weight": 600 });
    });
    var marks = n <= 12 && (x1 - x0) / n >= 52 ? rows.map(function (_, i) { return i; })
      : W < 420 ? [0, n - 1] : [0, Math.floor((n - 1) / 2), n - 1];
    marks.filter(function (v, i, a) { return a.indexOf(v) === i; }).forEach(function (i) {
      var anchor = marks.length > 3 ? "middle" : i === 0 ? "start" : i === n - 1 ? "end" : "middle";
      text(svg, X(i), H - 4, label(rows[i][f.x]), { fill: P.muted, "font-size": 11, "text-anchor": n === 1 ? "middle" : anchor });
    });
    var rule = el("line", { y1: y0, y2: y1, stroke: P.muted, "stroke-dasharray": "2 3", visibility: "hidden" }, svg);
    var hint = tip();
    svg.addEventListener("pointermove", function (e) {
      var r = svg.getBoundingClientRect(), i = n > 1 ? Math.round((e.clientX - r.left - x0) / (x1 - x0) * (n - 1)) : 0;
      i = Math.max(0, Math.min(n - 1, i));
      rule.setAttribute("x1", X(i)); rule.setAttribute("x2", X(i)); rule.setAttribute("visibility", "visible");
      var vals = series.map(function (s, si) { return (multi ? f.y[si] + " " : "") + full(s[i]); }).join(" · ");
      place(hint, X(i), Math.min.apply(null, series.map(function (s) { return Y(s[i]); })), label(rows[i][f.x]) + " · " + vals);
    });
    svg.addEventListener("pointerleave", function () { rule.setAttribute("visibility", "hidden"); hint.hidden = true; });
  }

  // ---- horizontal bars ----
  function drawBars() {
    var f = fields(), k = f.y[0], W = root.clientWidth;
    if (!k) throw new Error("bar charts need a numeric y field");
    var list = rows.slice();
    if (spec.sort !== false) list.sort(function (a, b) { return num(b[k]) - num(a[k]); });
    var extra = Math.max(0, list.length - 30);
    list = list.slice(0, 30);
    var svg = svgIn(W, 10), headH = headline(svg, 0), rowH = 30 * K, H = headH + list.length * rowH + (extra ? 22 : 0);
    svg.setAttribute("height", H);
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    var max = Math.max.apply(null, list.map(function (r) { return num(r[k]); }).concat([0])) || 1;
    var total = list.reduce(function (a, r) { return a + num(r[k]); }, 0);
    var lw = Math.min(W * 0.4, Math.max.apply(null, list.map(function (r) { return width(svg, label(r[f.x]), 12.5); })));
    var vw = Math.max.apply(null, list.map(function (r) { return width(svg, compact(num(r[k])), 12, 600); })) + 10;
    var bx = lw + 14, span = Math.max(20, W - bx - vw), bh = 14 * K;
    list.forEach(function (r, i) {
      var cy = headH + i * rowH + rowH / 2, name = label(r[f.x]), v = num(r[k]), g = el("g", {}, svg);
      el("title", {}, g).textContent = name + ": " + full(v) + (total > 0 && v >= 0 ? " (" + (v / total * 100).toFixed(1) + "%)" : "");
      while (name.length > 3 && width(svg, name, 12.5) > lw) name = name.slice(0, -2) + "…";
      text(g, 0, cy + 4, name, { fill: P.text, "font-size": 12.5 });
      var w = Math.max(2, Math.max(0, v) / max * span);
      el("rect", { x: bx, y: cy - bh / 2, width: w, height: bh, rx: 2, fill: P.accent, "fill-opacity": i === 0 ? 1 : 0.42 }, g);
      text(g, bx + w + 8, cy + 4, compact(v), { fill: i === 0 ? P.text : P.muted, "font-size": 12, "font-weight": i === 0 ? 600 : 400 });
    });
    if (extra) text(svg, 0, H - 6, "+" + extra + " more", { fill: P.muted, "font-size": 12 });
  }

  // ---- columns (stacked when several y fields) ----
  function drawColumns() {
    var f = fields(), n = rows.length, W = root.clientWidth;
    if (!f.y.length) throw new Error("column charts need a numeric y field");
    var multi = f.y.length > 1, svg = svgIn(W, 10), headH = headline(svg, 0), legendH = 0;
    var fills = [[P.accent, 1], [P.accent, 0.42], [P.muted, 0.5], [P.ok, 0.8]];
    if (multi) {
      var lx = 0;
      f.y.forEach(function (k, i) {
        el("rect", { x: lx, y: headH + 4, width: 9, height: 9, rx: 2, fill: fills[i][0], "fill-opacity": fills[i][1] }, svg);
        text(svg, lx + 14, headH + 12.5, k, { fill: P.muted, "font-size": 12 });
        lx += 14 + width(svg, k, 12) + 18;
      });
      legendH = 24;
    }
    var H = headH + legendH + plotHeight(W);
    svg.setAttribute("height", H);
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    var totals = rows.map(function (r) { return f.y.reduce(function (a, k) { return a + Math.max(0, num(r[k])); }, 0); });
    var sc = nice(0, Math.max.apply(null, totals), 3);
    var yw = Math.max.apply(null, sc.ticks.map(function (t) { return width(svg, compact(t), 11); })) + 10;
    var x0 = yw, y0 = headH + legendH + 12, y1 = H - 22, band = (W - x0) / n, cw = Math.min(46, band * 0.62);
    var Y = function (v) { return y1 - v / sc.hi * (y1 - y0); };
    sc.ticks.forEach(function (t) {
      el("line", { x1: x0, x2: W, y1: Y(t), y2: Y(t), stroke: P.line, "stroke-width": 1 }, svg);
      text(svg, x0 - 10, Y(t) + 4, compact(t), { fill: P.muted, "font-size": 11, "text-anchor": "end" });
    });
    var widest = Math.max.apply(null, rows.map(function (r) { return width(svg, label(r[f.x]), 11); })) + 10;
    var every = Math.max(1, Math.ceil(widest / band));
    rows.forEach(function (r, j) {
      var cx = x0 + band * j + band / 2, acc = 0, g = el("g", {}, svg);
      el("title", {}, g).textContent = label(r[f.x]) + ": " + f.y.map(function (k) { return (multi ? k + " " : "") + full(num(r[k])); }).join(", ");
      f.y.forEach(function (k, i) {
        var v = Math.max(0, num(r[k])), top = Y(acc + v), h = Y(acc) - top;
        el("rect", { x: cx - cw / 2, y: top, width: cw, height: Math.max(0.5, h - (multi ? 1.5 : 0)), rx: 1.5, fill: fills[i][0], "fill-opacity": multi ? fills[i][1] : j === n - 1 ? 1 : 0.55 }, g);
        acc += v;
      });
      if (band >= 40) text(g, cx, Y(totals[j]) - 7, compact(totals[j]), { fill: P.muted, "font-size": 11, "text-anchor": "middle" });
      if ((n - 1 - j) % every === 0) text(g, cx, H - 4, label(r[f.x]), { fill: P.muted, "font-size": 11, "text-anchor": "middle" });
    });
  }

  // ---- key numbers ----
  function drawStats() {
    var W = root.clientWidth, items = rows.slice(0, 8), n = items.length;
    var cols = W >= 560 ? Math.min(n, 4) : n === 1 ? 1 : 2, size = Math.max(20, Math.min(34, (W / cols) * 0.16)) * K;
    var showNote = W / cols >= 190;
    root.innerHTML = '<div class="fc-stats" style="grid-template-columns:repeat(' + cols + ',minmax(0,1fr))">' + items.map(function (it, i) {
      var cls = "fc-st" + (i % cols ? " sep" : " first") + (i >= cols ? " top" : "");
      var tone = it.good === true ? P.ok : it.good === false ? P.err : P.muted;
      var delta = it.delta != null && it.delta !== "" ? '<div class="fc-sd" style="color:' + tone + '">' + esc(it.delta) +
        (it.note && showNote ? "<span> " + esc(it.note) + "</span>" : "") + "</div>" : it.note ? '<div class="fc-sd"><span>' + esc(it.note) + "</span></div>" : "";
      return '<div class="' + cls + '"><div class="fc-sl">' + esc(it.label == null ? "" : it.label) + '</div><div class="fc-sv" style="font-size:' +
        size.toFixed(0) + 'px">' + esc(shown(it.value)) + "</div>" + delta + "</div>";
    }).join("") + "</div>";
  }

  // ---- table ----
  function spark(values, w, h) {
    var nums = values.map(num), lo = Math.min.apply(null, nums), hi = Math.max.apply(null, nums), d = hi - lo || 1;
    var pts = nums.map(function (v, i) { return (i * w / Math.max(1, nums.length - 1)).toFixed(1) + "," + (h - 1 - (v - lo) / d * (h - 2)).toFixed(1); }).join(" ");
    return '<svg width="' + w + '" height="' + h + '" aria-hidden="true" style="flex:none"><polyline points="' + pts + '" fill="none" stroke="' + esc(P.accent) + '" stroke-width="1.25" stroke-linejoin="round"/></svg>';
  }
  function drawTable() {
    var W = root.clientWidth, keys = rows.length ? Object.keys(rows[0]) : [];
    var cols = (Array.isArray(spec.columns) ? spec.columns : keys).map(function (c) {
      var col = typeof c === "string" ? { key: c } : c && typeof c === "object" ? c : null;
      if (!col || typeof col.key !== "string") return null;
      var sample = rows.map(function (r) { return r[col.key]; }).find(function (v) { return v != null; });
      var kind = col.kind || (Array.isArray(sample) ? "spark" : typeof sample === "number" ? "number" : "text");
      var max = num(col.max) || Math.max.apply(null, rows.map(function (r) { return num(r[col.key]); }).concat([1]));
      return { key: col.key, label: col.label || col.key, kind: kind, max: max };
    }).filter(Boolean);
    cols = cols.slice(0, Math.max(2, Math.floor(W / 110)));
    var list = rows.slice(0, 20), track = W < 500 ? 36 : 56, sparkW = W < 500 ? 44 : 80;
    var cell = function (col, v) {
      if (col.kind === "spark" && Array.isArray(v)) return '<td><span class="fc-cell">' + spark(v, sparkW, 16) + "<span>" + esc(compact(num(v[v.length - 1]))) + "</span></span></td>";
      if (col.kind === "bar") {
        var share = Math.max(0, Math.min(1, num(v) / col.max));
        return '<td><span class="fc-cell"><span class="fc-track" style="width:' + track + 'px"><span class="fc-fill" style="width:' + (share * 100).toFixed(1) +
          '%;background:' + esc(P.accent) + '"></span></span><span>' + esc(shown(v)) + "</span></span></td>";
      }
      if (col.kind === "status") {
        var bad = /^(down|fail|failed|error|bad|critical|offline|red)/i.test(String(v)), warn = /^(warn|degraded|slow|amber|yellow)/i.test(String(v));
        return '<td><span class="fc-cell"><span class="fc-dot" style="background:' + esc(bad ? P.err : warn ? P.muted : P.ok) + '"></span>' + esc(shown(v)) + "</span></td>";
      }
      if (col.kind === "number" || typeof v === "number") return '<td class="num">' + esc(typeof v === "number" ? full(v) : shown(v)) + "</td>";
      return "<td>" + esc(shown(v)) + "</td>";
    };
    root.innerHTML = '<table class="fc-tbl"><thead><tr>' + cols.map(function (c) {
      return "<th" + (c.kind === "number" ? ' class="num"' : "") + ">" + esc(c.label) + "</th>";
    }).join("") + "</tr></thead><tbody>" + list.map(function (r) {
      return "<tr>" + cols.map(function (c) { return cell(c, r[c.key]); }).join("") + "</tr>";
    }).join("") + "</tbody></table>" + (rows.length > list.length ? '<p class="fc-more">+' + (rows.length - list.length) + " more rows</p>" : "");
  }

  // ---- heatmap ----
  function drawHeat() {
    var f = fields(), vk = typeof spec.value === "string" ? spec.value : f.y[0], W = root.clientWidth;
    var yk = Array.isArray(spec.y) ? spec.y[0] : spec.y;
    if (!vk || !yk || vk === yk) {
      var keys = Object.keys(rows[0] || {});
      yk = yk && yk !== vk ? yk : keys.find(function (k) { return k !== f.x && k !== vk; });
    }
    if (!vk || !yk) throw new Error("heatmaps need x, y and value fields");
    var xs = [], ys = [], cells = {};
    rows.forEach(function (r) {
      var x = String(r[f.x]), y = String(r[yk]);
      if (xs.indexOf(x) < 0) xs.push(x);
      if (ys.indexOf(y) < 0) ys.push(y);
      cells[y + "\u0000" + x] = num(r[vk]);
    });
    var vals = Object.keys(cells).map(function (k) { return cells[k]; }), max = Math.max.apply(null, vals.concat([0])) || 1;
    var svg = svgIn(W, 10), best = Object.keys(cells).reduce(function (a, k) { return cells[k] > cells[a] ? k : a; });
    var top = 26 * K, left = Math.max.apply(null, ys.map(function (y) { return width(svg, label(y), 11); })) + 10;
    var cw = (W - left) / xs.length, ch = Math.max(10, Math.min(24 * K, cw)), gap = cw >= 8 ? 2 : 1;
    var H = top + ys.length * ch + 18 * K;
    svg.setAttribute("height", H);
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    var head = text(svg, 0, 13, "", { "font-size": 12.5 }), parts = best.split("\u0000");
    el("tspan", { fill: P.muted }, head).textContent = "Highest  ";
    el("tspan", { fill: P.text, "font-weight": 600 }, head).textContent = label(parts[0]) + " " + label(parts[1]);
    el("tspan", { fill: P.muted }, head).textContent = " · " + full(cells[best]);
    var rowEvery = ch >= 15 ? 1 : 2, colEvery = Math.max(1, Math.ceil((Math.max.apply(null, xs.map(function (x) { return width(svg, label(x), 11); })) + 8) / cw));
    ys.forEach(function (y, yi) {
      if (yi % rowEvery === 0) text(svg, left - 8, top + yi * ch + ch / 2 + 4, label(y), { fill: P.muted, "font-size": 11, "text-anchor": "end" });
      xs.forEach(function (x, xi) {
        var v = cells[y + "\u0000" + x], has = v != null && v > 0;
        var r = el("rect", { x: left + xi * cw, y: top + yi * ch, width: Math.max(1, cw - gap), height: ch - gap, rx: cw > 8 ? 2 : 0,
          fill: has ? P.accent : P.line, "fill-opacity": has ? (0.06 + 0.94 * Math.pow(v / max, 1.1)).toFixed(3) : 1 }, svg);
        el("title", {}, r).textContent = label(y) + " " + label(x) + ": " + (v == null ? "no data" : full(v));
      });
    });
    xs.forEach(function (x, xi) { if (xi % colEvery === 0) text(svg, left + xi * cw, top + ys.length * ch + 13, label(x), { fill: P.muted, "font-size": 11 }); });
  }

  var DRAW = { line: function () { drawLine(false); }, area: function () { drawLine(true); }, bar: drawBars, column: drawColumns, stacked: drawColumns, stat: drawStats, table: drawTable, heatmap: drawHeat };

  function draw() {
    palette();
    try {
      if (!rows.length) throw new Error("no data rows");
      (DRAW[spec.type] || drawTable)();
    } catch (error) {
      root.innerHTML = '<p style="margin:0;color:var(--fuse-muted);font-size:calc(13px * var(--fuse-scale,1))">Could not draw this chart: ' + esc(error.message || error) + "</p>";
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
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { draw(); });
})();
