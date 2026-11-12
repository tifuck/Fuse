/* Usage page: fetches /usage.json for the chosen range/provider/scope and
   paints the summary, the chart, one tabbed breakdown (details open per
   row), the heatmap, errors and the request log. Relies on escapeText()
   from app.js. */
(() => {
  const root = document.querySelector("[data-usage-dashboard]");
  if (!root) return;

  const $ = (sel) => root.querySelector(sel);
  const esc = (s) => escapeText(s == null ? "" : String(s));
  const compactFmt = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
  const intFmt = new Intl.NumberFormat();
  const SERIES = ["--uz-s1", "--uz-s2", "--uz-s3", "--uz-s4", "--uz-s5", "--uz-s6"];
  // A provider keeps one colour in every view: its place in this list picks it.
  const PROVIDER_ORDER = ["zai", "claude", "grok", "chatgpt", "openrouter", "mimo", "kimi", "llama", "devin", "cursor", "ollama", "explabs", "nanogpt", "refuseless", "openai"];
  const PALETTE = ["--uz-s1", "--uz-s2", "--uz-s3", "--uz-s4", "--uz-s5", "--uz-s6", "--uz-s7", "--uz-s8", "--uz-s9", "--uz-s10"];
  const MIX = { uncached: "--uz-s1", cached: "--uz-s3", write: "--uz-s6", visible: "--uz-s4", reasoning: "--uz-s5" };
  const RANGE_TEXT = { "30m": "Last 30 minutes", "6h": "Last 6 hours", "24h": "Last 24 hours", "3d": "Last 3 days", "7d": "Last 7 days", "30d": "Last 30 days", all: "All time" };
  const RANGES = Object.keys(RANGE_TEXT);
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const ROWS_PREVIEW = 8;

  const state = { range: "24h", provider: "", scope: "account", mode: "tokens", group: "provider", tab: "providers", sort: "tokens", dir: -1, all: false };
  const open = new Set();
  const params = new URLSearchParams(location.search);
  if (RANGES.includes(params.get("range"))) state.range = params.get("range");
  if (params.get("provider")) state.provider = params.get("provider").slice(0, 32);
  if (root.dataset.admin === "1") {
    const scope = params.get("scope") || "";
    if (scope === "all" || /^user:\d+$/.test(scope)) state.scope = scope;
    if (scope === "all") state.tab = "accounts";
  }

  let data = null;
  let controller = null;
  // Responses by query: a revisited view paints at once, then refreshes.
  const cache = new Map();
  // Model colours follow the model, not its rank: slots are handed out in
  // first-seen order and kept for the session, so filters never repaint survivors.
  const slots = new Map();

  function cssVar(name) {
    return getComputedStyle(root).getPropertyValue(name).trim() || "#888";
  }
  function providerColor(id) {
    if (id === "other") return cssVar("--uz-other");
    let i = PROVIDER_ORDER.indexOf(id);
    if (i < 0) i = [...String(id)].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7);
    return cssVar(PALETTE[i % PALETTE.length]);
  }
  function modelColor(id) {
    if (id === "other") return cssVar("--uz-other");
    if (!slots.has(id)) {
      const used = new Set(slots.values());
      slots.set(id, SERIES.find((s) => !used.has(s)) || null);
    }
    const slot = slots.get(id);
    return slot ? cssVar(slot) : cssVar("--uz-other");
  }

  function compact(n) {
    return compactFmt.format(Number(n) || 0);
  }
  function money(micros, { zero = "$0" } = {}) {
    const d = (Number(micros) || 0) / 1e6;
    if (!d) return zero;
    if (d >= 1000) return "$" + compactFmt.format(d);
    if (d >= 0.01) return "$" + d.toFixed(2);
    return "$" + d.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  }
  function pct(n, digits = 1) {
    if (n === null || n === undefined || !Number.isFinite(Number(n))) return "—";
    return Number(n).toFixed(digits).replace(/\.0$/, "") + "%";
  }
  function plural(n, word) {
    return `${intFmt.format(n)} ${word}${n === 1 ? "" : "s"}`;
  }
  function when(value, opts) {
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? "" : d.toLocaleString([], opts);
  }
  function tickLabel(at) {
    const r = state.range;
    if (r === "30m" || r === "6h" || r === "24h") return when(at, { hour: "numeric", minute: "2-digit" });
    if (r === "3d") return when(at, { weekday: "short", hour: "numeric" });
    return when(at, { month: "short", day: "numeric" });
  }
  function bucketLabel(at) {
    const start = new Date(at);
    const size = data.range.bucket_seconds || 3600;
    const end = new Date(start.getTime() + size * 1000);
    const day = { weekday: "short", month: "short", day: "numeric" };
    const time = { hour: "numeric", minute: "2-digit" };
    if (size >= 604800) return "Week of " + start.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
    if (size >= 86400) return start.toLocaleDateString([], day);
    return `${start.toLocaleDateString([], day)}, ${start.toLocaleTimeString([], time)} – ${end.toLocaleTimeString([], time)}`;
  }
  function ago(value) {
    const t = new Date(value).getTime();
    if (!t) return "";
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    return Math.floor(s / 86400) + "d ago";
  }
  function lifetime() {
    return data.range.id === "all";
  }
  function sinceText() {
    return when(data.range.first_at, { month: "short", day: "numeric", year: "numeric" });
  }
  function hitRate(r) {
    if (r.cache_hit !== undefined) return r.cache_hit;
    return r.prompt ? (r.cached * 100) / r.prompt : null;
  }

  /* —— Controls —— */
  function syncPressed(group, attr, value) {
    if (!group) return;
    group.querySelectorAll(`[${attr}]`).forEach((b) => {
      const on = b.getAttribute(attr) === value;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }
  function syncControls() {
    syncPressed($("[data-usage-range]"), "data-range", state.range);
    syncPressed($("[data-chart-mode]"), "data-mode", state.mode);
    syncPressed($("[data-chart-group]"), "data-group", state.group);
    const userSel = $("[data-usage-user]");
    if (userSel && [...userSel.options].some((o) => o.value === state.scope)) userSel.value = state.scope;
  }
  function syncUrl() {
    const q = new URLSearchParams(location.search);
    state.range === "24h" ? q.delete("range") : q.set("range", state.range);
    state.provider ? q.set("provider", state.provider) : q.delete("provider");
    state.scope === "account" ? q.delete("scope") : q.set("scope", state.scope);
    history.replaceState(null, "", location.pathname + (q.toString() ? "?" + q : "") + location.hash);
  }

  $("[data-usage-range]")?.addEventListener("click", (e) => {
    const b = e.target.closest("[data-range]");
    if (!b || b.dataset.range === state.range) return;
    state.range = b.dataset.range;
    load();
  });
  $("[data-usage-user]")?.addEventListener("change", (e) => {
    if (e.target.value === state.scope) return;
    state.scope = e.target.value;
    if (state.scope === "all") selectTab("accounts", false);
    load();
  });
  $("[data-usage-provider]")?.addEventListener("change", (e) => {
    state.provider = e.target.value;
    load();
  });
  $("[data-chart-mode]")?.addEventListener("click", (e) => {
    const b = e.target.closest("[data-mode]");
    if (!b || b.dataset.mode === state.mode) return;
    state.mode = b.dataset.mode;
    syncControls();
    if (data) plot.render(true);
  });
  $("[data-chart-group]")?.addEventListener("click", (e) => {
    const b = e.target.closest("[data-group]");
    if (!b || b.dataset.group === state.group) return;
    state.group = b.dataset.group;
    syncControls();
    if (data) plot.render(true);
  });
  root.querySelectorAll("[data-sort]").forEach((b) => {
    b.addEventListener("click", () => {
      const key = b.dataset.sort;
      state.dir = state.sort === key ? -state.dir : -1;
      state.sort = key;
      root.querySelectorAll("[data-sort]").forEach((x) => {
        if (x === b) x.setAttribute("aria-sort", state.dir < 0 ? "descending" : "ascending");
        else x.removeAttribute("aria-sort");
      });
      if (data) renderRows();
    });
  });
  function selectTab(tab, paint = true) {
    state.tab = tab;
    state.all = false;
    root.querySelectorAll("[data-tab]").forEach((b) => {
      const on = b.dataset.tab === tab;
      b.classList.toggle("on", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    if (paint && data) renderRows();
  }
  $("[data-tabs]").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (b && b.dataset.tab !== state.tab) selectTab(b.dataset.tab);
  });
  $("[data-tabs]").addEventListener("keydown", (e) => {
    const tabs = [...root.querySelectorAll("[data-tab]:not([hidden])")];
    const i = tabs.indexOf(document.activeElement);
    const to = { ArrowRight: i + 1, ArrowLeft: i - 1 }[e.key];
    if (i < 0 || to === undefined) return;
    const next = tabs[(to + tabs.length) % tabs.length];
    e.preventDefault();
    next.focus();
    selectTab(next.dataset.tab);
  });

  /* —— Header —— */
  function renderCaption() {
    const el = $("[data-usage-caption]");
    const provider = state.provider ? (data.provider_options || []).find((p) => p.id === state.provider)?.name || state.provider : "";
    const picked = (data.account_options || []).find((u) => "user:" + u.id === state.scope);
    const who = data.scope === "all" ? "all accounts" : picked ? picked.email : "your account";
    const tail = lifetime()
      ? data.range.first_at ? `since ${esc(sinceText())}` : "no usage recorded yet"
      : `compared with the previous ${esc((RANGE_TEXT[data.range.id] || "").replace("Last ", ""))}`;
    el.innerHTML = `<b>${esc(RANGE_TEXT[data.range.id] || "")}</b> · ${esc(who)}${provider ? " · " + esc(provider) : ""} · ${tail}`;
  }
  function renderProviderOptions() {
    const sel = $("[data-usage-provider]");
    sel.innerHTML =
      '<option value="">All providers</option>' +
      (data.provider_options || []).map((p) => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
    sel.value = state.provider;
  }
  function renderUserOptions() {
    const sel = $("[data-usage-user]");
    if (!sel) return;
    const options = data.account_options || [];
    const known = options.some((u) => "user:" + u.id === state.scope);
    sel.innerHTML =
      '<option value="account">My account</option><option value="all">All accounts</option>' +
      (!known && state.scope.startsWith("user:") ? `<option value="${esc(state.scope)}">User ${esc(state.scope.slice(5))}</option>` : "") +
      options.map((u) => `<option value="user:${u.id}">${esc(u.email)}</option>`).join("");
    sel.value = state.scope;
  }

  /* —— Summary —— */
  function badge(value, { unit = "%", good = null } = {}) {
    if (value === null || value === undefined) return { text: "", cls: "" };
    const n = Number(value) || 0;
    const size = Math.abs(n) >= 100 ? Math.abs(n).toFixed(0) : Math.abs(n).toFixed(1).replace(/\.0$/, "");
    let cls = "flat";
    if (Math.abs(n) >= 0.5) cls = good ? ((n > 0) === (good === "up") ? "good" : "bad") : "moved";
    return { text: `${n > 0 ? "↑" : n < 0 ? "↓" : "→"} ${size}${unit}`, cls };
  }
  function setStat(id, value, sub, b, title = "") {
    const el = root.querySelector(`[data-kpi="${id}"]`);
    el.querySelector("[data-kpi-value]").innerHTML = value;
    el.querySelector("[data-kpi-sub]").textContent = sub || "\u00a0";
    const d = el.querySelector("[data-kpi-delta]");
    d.textContent = b.text;
    d.className = "uz-badge " + b.cls;
    d.hidden = !b.text;
    d.title = b.text ? "Change from the previous period" : "";
    el.title = title;
  }
  function renderStats() {
    const s = data.summary;
    const none = { text: "", cls: "" };
    // Lifetime has no previous period; an empty range has nothing to compare.
    const vs = (value, opts) => (s.empty || lifetime() ? none : badge(value, opts));
    const days = Math.max(1, data.activity_days || 1);
    const output = s.output || s.completion;
    const mixTitle = (data.mix || []).filter((p) => p.tokens).map((p) => `${p.name}: ${intFmt.format(p.tokens)} (${pct(p.share)})`).join("\n");
    setStat("tokens", compact(s.tokens), s.empty ? "No traffic in this range" : `${compact(s.prompt)} in · ${compact(output)} out`, vs(s.token_change), mixTitle);
    setStat(
      "requests",
      intFmt.format(s.requests),
      s.success_rate === null ? "No requests" : `${pct(s.success_rate)} succeeded`,
      vs(s.request_change),
      s.requests ? `${intFmt.format(s.ok_requests)} succeeded, ${intFmt.format(s.failed)} failed, ${intFmt.format(s.cancelled)} cancelled` : ""
    );
    const noRates = !s.pricing_coverage && !s.cost;
    let costSub = "";
    if (s.empty) costSub = "No traffic in this range";
    else if (noRates) costSub = s.untracked >= s.requests ? "Billed to your OpenRouter key" : "No rates for these models";
    else if (lifetime()) costSub = `${money(s.cost / days)} a day`;
    else if (s.pricing_coverage < 100) costSub = `${s.pricing_coverage}% of requests priced`;
    else if (s.blended !== null) costSub = `${money(s.blended * 1e6)} per 1M tokens`;
    setStat("cost", noRates ? "—" : esc(s.cost_label), costSub, noRates ? none : vs(s.cost_change));
    const saved = s.cache_saved_known && s.cache_saved;
    const cacheSub = s.cache_hit === null ? "No input tokens" : saved ? `${money(s.cache_saved)} saved` : `${compact(s.cached)} cached tokens`;
    setStat(
      "cache",
      pct(s.cache_hit),
      s.empty ? "No traffic in this range" : cacheSub,
      s.cache_hit === null ? none : vs(s.cache_hit_change, { unit: " pts", good: "up" }),
      s.prompt ? `${intFmt.format(s.cached)} of ${intFmt.format(s.prompt)} input tokens read from cache${saved ? `; about ${money(s.cache_saved)} saved at list rates` : ""}` : ""
    );
  }

  /* —— Stacked activity chart —— */
  const MODE = {
    tokens: { idx: 0, key: "segments", total: "tokens", title: "Tokens", fmt: (v) => intFmt.format(v), axis: compact },
    requests: { idx: 1, key: "request_segments", total: "requests", title: "Requests", fmt: (v) => intFmt.format(v), axis: compact },
    cost: { idx: 2, key: "cost_segments", total: "cost", title: "Estimated cost", fmt: (v) => money(v), axis: (v) => money(v) },
  };
  function seriesList() {
    const byProvider = state.group === "provider";
    const list = (byProvider ? data.series_providers : data.series_models) || [];
    return list.map((s) => ({
      id: s.id,
      name: s.name,
      note: byProvider ? "" : s.provider_label,
      color: byProvider ? providerColor(s.id) : modelColor(s.id),
    }));
  }
  function segValue(point, id) {
    const m = MODE[state.mode];
    if (state.group === "provider") return Number((point.providers || {})[id]?.[m.idx]) || 0;
    return Number((point[m.key] || {})[id]) || 0;
  }
  const plot = (() => {
    const el = $("[data-plot]");
    const tip = $("[data-tip]");
    let touched = false;
    let drawnW = 0;
    el.addEventListener("touchstart", () => (touched = true), { passive: true });

    function hide() {
      el.classList.remove("is-hovering");
      el.querySelectorAll(".col.is-hot").forEach((c) => c.classList.remove("is-hot"));
      tip.hidden = true;
    }
    function show(col, ev) {
      const m = MODE[state.mode];
      const point = data.series[Number(col.dataset.i)];
      if (!point) return;
      el.classList.add("is-hovering");
      el.querySelectorAll(".col.is-hot").forEach((c) => c !== col && c.classList.remove("is-hot"));
      col.classList.add("is-hot");
      const total = Number(point[m.total]) || 0;
      const rows = seriesList()
        .concat([{ id: "other", name: state.group === "provider" ? "Other providers" : "Other models", color: cssVar("--uz-other") }])
        .map((s) => ({ ...s, v: segValue(point, s.id) }))
        .filter((r) => r.v)
        .sort((a, b) => b.v - a.v);
      const head = [`${intFmt.format(point.tokens)} tokens`, plural(point.requests, "request")];
      if (point.cost) head.push(money(point.cost));
      if (point.failed) head.push(`${point.failed} failed`);
      tip.innerHTML =
        `<strong>${esc(bucketLabel(point.at))}</strong><span class="head">${esc(head.join(" · "))}</span>` +
        rows
          .map(
            (r) =>
              `<span class="line"><i class="uz-dot" style="background:${r.color}"></i><span>${esc(r.name)}</span><b>${esc(m.fmt(r.v))} · ${total ? Math.round((r.v * 100) / total) : 0}%</b></span>`
          )
          .join("");
      tip.hidden = false;
      const wrap = el.parentElement.getBoundingClientRect();
      const box = col.getBoundingClientRect();
      let x = (ev && ev.clientX ? ev.clientX : box.left + box.width / 2) - wrap.left + 14;
      if (x + tip.offsetWidth > wrap.width - 4) x = x - tip.offsetWidth - 28;
      tip.style.left = Math.max(4, x) + "px";
      tip.style.top = Math.max(4, Math.min((ev && ev.clientY ? ev.clientY - wrap.top : 40) - tip.offsetHeight / 2, wrap.height - tip.offsetHeight - 4)) + "px";
    }
    el.addEventListener("mousemove", (ev) => {
      if (touched) return;
      const col = ev.target.closest(".col");
      col ? show(col, ev) : hide();
    });
    el.addEventListener("mouseleave", () => !touched && hide());
    el.addEventListener("click", (ev) => {
      const col = ev.target.closest(".col");
      col ? show(col, ev) : hide();
    });
    el.addEventListener("focusin", (ev) => {
      const col = ev.target.closest(".col");
      if (col) show(col);
    });
    el.addEventListener("focusout", hide);
    // One tab stop for the whole chart (the newest bar); arrows walk the bars.
    el.addEventListener("keydown", (ev) => {
      const cols = [...el.querySelectorAll(".col")];
      const i = cols.indexOf(document.activeElement);
      const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: cols.length - 1 }[ev.key];
      if (i < 0 || to === undefined || !cols[to]) return;
      ev.preventDefault();
      cols[i].tabIndex = -1;
      cols[to].tabIndex = 0;
      cols[to].focus();
    });

    function niceMax(v) {
      if (v <= 0) return 1;
      const exp = Math.pow(10, Math.floor(Math.log10(v)));
      const step = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((c) => v / exp <= c);
      return step * exp;
    }

    function render(force) {
      const w = Math.floor(el.clientWidth);
      const h = Math.floor(el.clientHeight);
      if (w < 80 || h < 80) return;
      if (!force && Math.abs(w - drawnW) < 4) return;
      drawnW = w;
      hide();
      const m = MODE[state.mode];
      $("[data-chart-title]").textContent = `${m.title} by ${state.group}`;
      const s = data.summary;
      const total = state.mode === "cost" ? money(s.cost) : intFmt.format(s[m.total]);
      $("[data-chart-sub]").textContent = `${total} total · ${data.range.bucket} buckets`;
      const points = data.series || [];
      const max = Math.max(0, ...points.map((p) => Number(p[m.total]) || 0));
      const series = seriesList();
      const ids = series.map((x) => x.id).concat(["other"]);
      const colors = Object.fromEntries(series.map((x) => [x.id, x.color]).concat([["other", cssVar("--uz-other")]]));
      renderLegend(series);
      if (!max) {
        el.innerHTML = `<p class="uz-empty">${state.mode === "cost" && s.tokens ? "No rates configured for these models yet." : "No recorded requests in this range."}</p>`;
        return;
      }
      const narrow = w < 560;
      const minStep = narrow ? 3 : 5;
      const top = 8;
      const bottom = 24;
      const top_ = niceMax(max);
      const labels = [0, 1, 2, 3, 4].map((i) => m.axis((top_ * i) / 4));
      const left = Math.max(...labels.map((l) => l.length)) * 6.4 + 12;
      const right = 4;
      // Scroll only when the bars cannot fit beside the axis gutter, or the gutter scrolls out of view.
      const width = Math.max(w, Math.ceil(points.length * minStep + left + right));
      const plotW = width - left - right;
      const plotH = h - top - bottom;
      const step = plotW / points.length;
      const barW = Math.max(2, Math.min(26, step * 0.66));
      const y = (v) => (v / top_) * plotH;
      let svg = `<svg width="${width}" height="${h}" viewBox="0 0 ${width} ${h}" role="group" aria-label="${esc(m.title)} by ${state.group}">`;
      labels.forEach((label, i) => {
        const gy = Math.round(top + plotH - (plotH * i) / 4) + 0.5;
        svg += `<line class="grid${i ? "" : " base"}" x1="${left}" x2="${width - right}" y1="${gy}" y2="${gy}"/>`;
        svg += `<text class="axis" x="${left - 8}" y="${gy + 3.5}" text-anchor="end" aria-hidden="true">${esc(label)}</text>`;
      });
      const want = narrow ? 4 : Math.max(4, Math.min(8, Math.floor(plotW / 110)));
      const every = Math.max(1, Math.ceil(points.length / want));
      points.forEach((p, i) => {
        const x = left + i * step + (step - barW) / 2;
        let yy = top + plotH;
        const label = `${bucketLabel(p.at)}: ${m.fmt(Number(p[m.total]) || 0)}`;
        svg += `<g class="col" data-i="${i}" tabindex="${i === points.length - 1 ? 0 : -1}" role="graphics-symbol" aria-label="${esc(label)}">`;
        svg += `<rect class="hit" x="${left + i * step}" y="${top}" width="${step}" height="${plotH}"/>`;
        const stack = ids.map((id) => [id, segValue(p, id)]).filter(([, v]) => v);
        stack.forEach(([id, v], k) => {
          const bh = Math.max(1, y(v));
          yy -= bh;
          const r = k === stack.length - 1 ? Math.min(2, barW / 2, bh) : 0;
          // Only the top of the stack is rounded; the baseline stays square.
          const d = r
            ? `M${x} ${yy + bh} V${yy + r} Q${x} ${yy} ${x + r} ${yy} H${x + barW - r} Q${x + barW} ${yy} ${x + barW} ${yy + r} V${yy + bh} Z`
            : `M${x} ${yy + bh} V${yy} H${x + barW} V${yy + bh} Z`;
          svg += `<path class="bar" d="${d}" fill="${colors[id]}"/>`;
        });
        svg += `</g>`;
        if (i % every === 0 && points.length - i > every * 0.4) {
          svg += `<text class="axis" x="${x + barW / 2}" y="${h - 6}" text-anchor="${i === 0 ? "start" : "middle"}" aria-hidden="true">${esc(tickLabel(p.at))}</text>`;
        }
      });
      svg += "</svg>";
      el.innerHTML = svg;
      if (width > w) el.scrollLeft = el.scrollWidth;
    }
    return { render };
  })();

  function renderLegend(series) {
    const el = $("[data-legend]");
    const hasOther = (data.series || []).some((p) => segValue(p, "other"));
    el.innerHTML =
      series
        .map((s) => `<span title="${esc(s.name)}${s.note ? " · " + esc(s.note) : ""}"><i class="uz-dot" style="background:${s.color}"></i><span class="n">${esc(s.name)}</span>${s.note ? `<em>${esc(s.note)}</em>` : ""}</span>`)
        .join("") +
      (hasOther ? `<span><i class="uz-dot" style="background:${cssVar("--uz-other")}"></i>Other ${state.group === "provider" ? "providers" : "models"}</span>` : "");
    el.hidden = !series.length;
  }

  /* —— Token breakdown and rates (folded under the chart) —— */
  function renderFacts() {
    const mix = (data.mix || []).filter((p) => p.tokens > 0);
    const s = data.summary;
    $("[data-mix-bar]").innerHTML = mix
      .map((p) => `<span style="flex:${p.tokens} 1 0;background:${cssVar(MIX[p.id])}" title="${esc(p.name)} ${pct(p.share)}"></span>`)
      .join("");
    $("[data-mix]").innerHTML = mix.length
      ? mix.map((p) => `<div><dt><i class="uz-dot" style="background:${cssVar(MIX[p.id])}"></i>${esc(p.name)}</dt><dd>${compact(p.tokens)}<small>${pct(p.share)}</small></dd></div>`).join("")
      : '<p class="uz-empty">No tokens in this range.</p>';
    const facts = [];
    const priced = s.pricing_coverage > 0 || s.cost > 0;
    // Rates cover priced traffic only; say so when some of the range is unpriced.
    const partial = s.priced < s.requests ? " (priced)" : "";
    if (priced && s.blended !== null) facts.push(["Cost per 1M tokens" + partial, money(s.blended * 1e6)]);
    if (priced && s.cost_per_request !== null) facts.push(["Cost per request" + partial, money(s.cost_per_request)]);
    if (s.cache_saved_known && s.cache_saved) facts.push(["Saved by cache (list rates)", money(s.cache_saved), "good"]);
    if (s.per_request !== null) facts.push(["Tokens per request", compact(s.per_request)]);
    if (s.prompt) facts.push(["Output per 1K input", compact((s.output * 1000) / s.prompt) + " tokens"]);
    if (s.output) facts.push(["Reasoning share of output", pct((s.reasoning * 100) / s.output)]);
    if (s.peak_at) facts.push(["Busiest bucket", `${tickLabel(s.peak_at)} · ${compact(s.peak_tokens)}`]);
    if (s.models_used) facts.push(["Models used", intFmt.format(s.models_used)]);
    $("[data-facts]").innerHTML = facts.map(([k, v, cls]) => `<div><dt>${esc(k)}</dt><dd class="${cls || ""}">${esc(v)}</dd></div>`).join("");
  }

  /* —— Breakdown tabs —— */
  function costCell(r) {
    if (r.cost_tracked === false) return '<span class="uz-quiet" title="Billed to your own OpenRouter key; Fuse does not cost it">own key</span>';
    if (r.cost_known || r.cost) return money(r.cost);
    if (r.cost_known === false) return '<span class="uz-quiet" title="No price configured for these models">no rate</span>';
    return '<span class="uz-quiet">—</span>';
  }
  function change(r) {
    if (lifetime()) return null;
    if (r.is_new) return "New this period";
    if (r.token_change === null || r.token_change === undefined) return null;
    const n = Number(r.token_change);
    return `${n > 0 ? "↑" : n < 0 ? "↓" : "→"} ${Math.abs(n).toFixed(Math.abs(n) >= 100 ? 0 : 1).replace(/\.0$/, "")}% tokens vs previous`;
  }
  // Detail values: [shown, exact on hover].
  function tok(n) {
    return [compact(n), `${intFmt.format(n)} tokens`];
  }
  function failedText(r) {
    return r.failed ? `${intFmt.format(r.failed)} (${pct((r.failed * 100) / (r.requests || 1))})` : "None";
  }
  function trendSvg(values, color) {
    const nums = (values || []).map((v) => Number(v) || 0);
    const max = Math.max(0, ...nums);
    if (nums.length < 2 || !max) return "";
    const pts = nums.map((v, i) => `${((i * 120) / (nums.length - 1)).toFixed(1)} ${(23 - (v / max) * 21).toFixed(1)}`);
    return `<svg class="uz-trend" viewBox="0 0 120 24" preserveAspectRatio="none" aria-hidden="true"><path d="M${pts.join(" L")}" stroke="${color}" vector-effect="non-scaling-stroke"/></svg>`;
  }
  const TABS = {
    providers: {
      head: "Provider",
      rows: () => data.providers || [],
      key: (r) => r.id,
      meta: (r) => plural(r.models, "model"),
      color: (r) => providerColor(r.id),
      detail: (r) => ({
        facts: [["Input", tok(r.prompt)], ["Output", tok(r.output || r.completion)], ["Reasoning", tok(r.reasoning)], ["Cached input", tok(r.cached)], ["Failed", failedText(r)], ["Change", change(r)]],
        action: state.provider === r.id ? "" : `<button type="button" class="btn ghost sm" data-pick-provider="${esc(r.id)}">Show only ${esc(r.name)}</button>`,
      }),
    },
    models: {
      head: "Model",
      rows: () => data.models || [],
      key: (r) => r.id,
      meta: (r) => r.provider_label,
      color: (r) => providerColor(r.provider),
      detail: (r) => ({
        facts: [
          ["Input", tok(r.prompt)],
          ["Output", tok(r.output || r.completion)],
          ["Reasoning", tok(r.reasoning)],
          ["Tokens per request", r.per_request ? compact(r.per_request) : "—"],
          ["Cost per 1M tokens", r.per_million !== null && r.per_million !== undefined ? money(r.per_million) : "—"],
          ["Failed", failedText(r)],
          ["Change", change(r)],
        ],
        extra: r.spark ? trendSvg(r.spark, providerColor(r.provider)) : "",
        action: state.provider === r.provider ? "" : `<button type="button" class="btn ghost sm" data-pick-provider="${esc(r.provider)}">Show only ${esc(r.provider_label)}</button>`,
      }),
    },
    keys: {
      head: "Source",
      rows: () => data.keys || [],
      key: (r) => r.kind + r.id,
      name: (r) => r.name,
      meta: (r) => (r.kind === "chat" ? "Dashboard chat, agents and tasks" : r.prefix ? r.prefix + "…" : "API key"),
      color: () => "",
      detail: (r) => ({
        facts: [["Last request", r.last_at ? `${when(r.last_at, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })} (${ago(r.last_at)})` : "—"], ["Input", tok(r.prompt)], ["Failed", failedText(r)]],
        action: r.kind === "key" ? '<a class="btn ghost sm" href="/keys">Manage keys</a>' : "",
      }),
    },
    accounts: {
      head: "Account",
      rows: () => data.users || [],
      key: (r) => r.id,
      name: (r) => r.email,
      meta: (r) => r.providers.slice(0, 3).map((p) => p.name).join(", ") + (r.providers.length > 3 ? ` +${r.providers.length - 3}` : ""),
      color: () => "",
      bar: (r) => r.providers.map((p) => (p.tokens ? `<span style="flex:${p.tokens} 1 0;background:${providerColor(p.id)}"></span>` : "")).join(""),
      detail: (r) => ({
        facts: [["Failed", failedText(r)]],
        extra: `<div class="uz-chips">${r.providers
          .filter((p) => p.tokens || p.requests)
          .map((p) => `<span class="uz-chip" title="${esc(p.name)}: ${intFmt.format(p.tokens)} tokens, ${plural(p.requests, "request")}"><i class="uz-dot" style="background:${providerColor(p.id)}"></i>${esc(p.name)} <b>${compact(p.tokens)}</b>${p.cost ? `<em>${money(p.cost)}</em>` : ""}</span>`)
          .join("")}</div>`,
        action: `<button type="button" class="btn ghost sm" data-pick-user="${r.id}">Show only this account</button>`,
      }),
    },
  };
  function renderTabs() {
    const accounts = root.querySelector('[data-tab="accounts"]');
    if (accounts) accounts.hidden = data.scope !== "all";
    if (state.tab === "accounts" && data.scope !== "all") selectTab("providers", false);
    const counts = { providers: (data.providers || []).length, models: data.summary.models_used, keys: (data.keys || []).length, accounts: (data.users || []).length };
    root.querySelectorAll("[data-tab]").forEach((b) => {
      b.querySelector("[data-tab-count]").textContent = counts[b.dataset.tab] ? intFmt.format(counts[b.dataset.tab]) : "";
    });
  }
  function renderRows() {
    const tab = TABS[state.tab];
    const body = $("[data-rows]");
    const more = $("[data-rows-more]");
    $("[data-name-head]").textContent = tab.head;
    const rows = tab.rows().slice();
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="5" class="uz-empty">No ${state.tab === "providers" ? "provider" : state.tab === "models" ? "model" : state.tab === "keys" ? "source" : "account"} usage in this range.</td></tr>`;
      more.hidden = true;
      return;
    }
    const val = (r) => {
      const v = state.sort === "cache_hit" ? hitRate(r) : r[state.sort];
      return v === null || v === undefined ? -1 : Number(v);
    };
    rows.sort((a, b) => (val(a) - val(b)) * state.dir || b.tokens - a.tokens);
    const total = data.summary.tokens || rows.reduce((a, r) => a + r.tokens, 0);
    const shown = state.all ? rows : rows.slice(0, ROWS_PREVIEW);
    body.innerHTML = shown
      .map((r, i) => {
        const id = `${state.tab}:${tab.key(r)}`;
        const isOpen = open.has(id);
        const share = total ? (r.tokens * 100) / total : 0;
        const color = tab.color(r);
        const name = tab.name ? tab.name(r) : r.name;
        const d = tab.detail(r);
        const facts = d.facts.filter(([, v]) => v !== null && v !== undefined);
        const bar = tab.bar ? `<i class="uz-stack">${tab.bar(r)}</i>` : `<i><b style="width:${Math.max(share, 0.5).toFixed(1)}%${color ? ";background:" + color : ""}"></b></i>`;
        return `<tr class="uz-row${isOpen ? " is-open" : ""}">
          <td class="uz-cell-name"><button type="button" class="uz-row-toggle" data-row="${esc(id)}" aria-expanded="${isOpen}" aria-controls="uz-detail-${i}">
            <svg class="uz-chev" viewBox="0 0 12 12" aria-hidden="true"><path d="M4.5 2.5 8 6l-3.5 3.5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
            ${color ? `<i class="uz-dot" style="background:${color}"></i>` : ""}
            <span class="uz-name"><strong title="${esc(name)}">${esc(name)}</strong><small>${esc(tab.meta(r))}</small></span>
          </button></td>
          <td class="num uz-cell-tokens"><span class="uz-share">${bar}<span>${compact(r.tokens)}</span></span><small>${pct(share, share >= 10 ? 0 : 1)}</small></td>
          <td class="num uz-cell-req">${intFmt.format(r.requests)}<span class="uz-unit"> req</span></td>
          <td class="num uz-cell-cache">${pct(hitRate(r), 0)}<span class="uz-unit"> cache</span></td>
          <td class="num uz-cell-cost">${costCell(r)}</td>
        </tr>
        <tr class="uz-detail" id="uz-detail-${i}"${isOpen ? "" : " hidden"}><td colspan="5"><div class="uz-detail-body">
          <dl>${facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd${Array.isArray(v) ? ` title="${esc(v[1])}"` : ""}>${esc(Array.isArray(v) ? v[0] : v)}</dd></div>`).join("")}</dl>
          ${d.extra || ""}${d.action ? `<div class="uz-detail-actions">${d.action}</div>` : ""}
        </div></td></tr>`;
      })
      .join("");
    more.hidden = rows.length <= ROWS_PREVIEW;
    more.textContent = state.all ? "Show fewer" : `Show all ${rows.length}`;
    more.setAttribute("aria-expanded", state.all ? "true" : "false");
  }
  $("[data-rows]").addEventListener("click", (e) => {
    const t = e.target.closest("[data-row]");
    if (!t) return;
    const isOpen = !open.has(t.dataset.row);
    isOpen ? open.add(t.dataset.row) : open.delete(t.dataset.row);
    t.setAttribute("aria-expanded", isOpen ? "true" : "false");
    t.closest("tr").classList.toggle("is-open", isOpen);
    t.closest("tr").nextElementSibling.hidden = !isOpen;
  });
  $("[data-rows-more]").addEventListener("click", () => {
    state.all = !state.all;
    if (data) renderRows();
  });

  /* —— Heatmap (local time) —— */
  function renderHeat() {
    const el = $("[data-heat]");
    const grid = Array.from({ length: 7 }, () => new Array(24).fill(0));
    const reqs = Array.from({ length: 7 }, () => new Array(24).fill(0));
    (data.activity || []).forEach(([epoch, tokens, requests]) => {
      const d = new Date(epoch * 1000);
      const day = (d.getDay() + 6) % 7;
      grid[day][d.getHours()] += tokens;
      reqs[day][d.getHours()] += requests;
    });
    const max = Math.max(0, ...grid.flat());
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "local time";
    $("[data-heat-sub]").textContent = `Last ${data.activity_days} days · ${tz}`;
    if (!max) {
      el.innerHTML = '<div class="uz-heat-empty">No activity in this range.</div>';
      return;
    }
    const heat = cssVar("--uz-heat");
    const track = cssVar("--uz-track");
    const shade = (v) => (v ? `color-mix(in srgb, ${heat} ${Math.round(14 + 86 * Math.sqrt(v / max))}%, ${track})` : "");
    let html = '<div class="uz-heat-grid" role="img" aria-label="Token activity by weekday and hour"><span></span>';
    for (let h = 0; h < 24; h += 1) html += `<span class="h">${h % 6 === 0 ? h : ""}</span>`;
    const byDay = grid.map((row) => row.reduce((a, b) => a + b, 0));
    const byHour = Array.from({ length: 24 }, (_, h) => grid.reduce((a, row) => a + row[h], 0));
    grid.forEach((row, d) => {
      html += `<span class="d">${DAYS[d]}</span>`;
      row.forEach((v, h) => {
        const t = `${DAYS[d]} ${String(h).padStart(2, "0")}:00 — ${intFmt.format(v)} tokens, ${plural(reqs[d][h], "request")}`;
        html += `<span class="c" style="${v ? "background:" + shade(v) : ""}" title="${esc(t)}"></span>`;
      });
    });
    html += "</div>";
    const peakDay = byDay.indexOf(Math.max(...byDay));
    const peakHour = byHour.indexOf(Math.max(...byHour));
    const hourText = new Date(2000, 0, 1, peakHour).toLocaleTimeString([], { hour: "numeric" });
    const scale = [0.1, 0.3, 0.55, 0.8, 1].map((f) => `<i style="background:${shade(f * max)}"></i>`).join("");
    html += `<div class="uz-heat-foot"><span>Busiest on <b>${DAYS[peakDay]}</b> around <b>${esc(hourText)}</b></span><span class="uz-scale"><span>Less</span>${scale}<span>More</span></span></div>`;
    el.innerHTML = html;
    // Touch has no title tooltip: a tapped cell reads out in the footer.
    el.onclick = (ev) => {
      const cell = ev.target.closest(".c");
      if (cell) el.querySelector(".uz-heat-foot > span").textContent = cell.title;
    };
  }

  /* —— Errors —— */
  function renderReliability() {
    const el = $("[data-reliability]");
    const sub = $("[data-errors-sub]");
    const s = data.summary;
    if (!s.requests) {
      sub.textContent = "\u00a0";
      el.innerHTML = '<p class="uz-empty">No requests in this range.</p>';
      return;
    }
    sub.textContent = s.failed ? `${intFmt.format(s.failed)} of ${plural(s.requests, "request")} failed` : `All ${plural(s.requests, "request")} went through`;
    const parts = [
      ["Succeeded", s.ok_requests, "var(--uz-good)"],
      ["Failed", s.failed, "var(--uz-bad)"],
      ["Cancelled", s.cancelled, "var(--mute)"],
    ];
    let html = `<p class="uz-rel-big">${pct(s.success_rate)}<small>success rate</small></p>`;
    html += `<div class="uz-rel-bar">${parts.filter((p) => p[1]).map((p) => `<span style="flex:${p[1]} 1 0;background:${p[2]}" title="${p[0]} ${intFmt.format(p[1])}"></span>`).join("")}</div>`;
    html += `<div class="uz-rel-key">${parts.map((p) => `<span><i class="uz-dot" style="background:${p[2]}"></i>${p[0]} <b>${intFmt.format(p[1])}</b></span>`).join("")}</div>`;
    const failing = (data.models || []).filter((m) => m.failed).sort((a, b) => b.failed - a.failed).slice(0, 3);
    if (failing.length) {
      html += `<ul class="uz-rel-list" aria-label="Most failures">${failing
        .map((m) => `<li><span class="uz-name"><strong title="${esc(m.name)}">${esc(m.name)}</strong><small>${esc(m.provider_label)}</small></span><span class="uz-rel-n">${intFmt.format(m.failed)}<small>${pct(m.error_rate)} of ${intFmt.format(m.requests)}</small></span></li>`)
        .join("")}</ul>`;
    }
    el.innerHTML = html;
  }

  /* —— Request log —— */
  function renderRecent() {
    const body = $("[data-recent]");
    const rows = data.recent || [];
    $("[data-recent-sub]").textContent = rows.length ? `Latest ${rows.length}` : "Nothing in this range";
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="7" class="uz-empty">No requests in this range.</td></tr>';
      return;
    }
    body.innerHTML = rows
      .map((r) => {
        const cachedPct = r.prompt && r.cached ? Math.round((r.cached * 100) / r.prompt) : 0;
        const cost = r.cost ? money(r.cost) : r.cost_known ? "$0" : '<span class="uz-quiet">—</span>';
        const time = when(r.at, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
        const via = r.via_kind === "chat" ? "Fuse chat" : r.via;
        return `<tr class="${r.prompt || r.output ? "" : "uz-dim"}">
          <td class="uz-cell-when"><span title="${esc(r.at)}">${esc(time)}</span></td>
          <td class="uz-cell-model"><span class="uz-name"><strong title="${esc(r.model)}">${esc(r.model)}</strong><small>${esc(r.provider_label)}</small></span></td>
          <td class="uz-cell-src">${esc(via)}</td>
          <td class="num uz-cell-in">${intFmt.format(r.prompt)}<small>${cachedPct ? cachedPct + "% cached" : ""}</small></td>
          <td class="num uz-cell-out">${intFmt.format(r.output)}<small>${r.reasoning ? compact(r.reasoning) + " reasoning" : ""}</small></td>
          <td class="num uz-cell-cost">${cost}</td>
          <td class="uz-cell-status"><span class="uz-status ${esc(r.status)}">${esc(r.status)}</span></td>
          <td class="uz-cell-io">${esc(time)} · ${esc(via)} · ${compact(r.prompt)} in${cachedPct ? ` (${cachedPct}% cached)` : ""} · ${compact(r.output)} out · ${cost}</td>
        </tr>`;
      })
      .join("");
  }

  function paint() {
    // Seed model colour slots in chart order so the top model gets slot 1.
    data.series_models.forEach((s) => modelColor(s.id));
    renderCaption();
    renderProviderOptions();
    renderUserOptions();
    renderStats();
    plot.render(true);
    renderFacts();
    renderTabs();
    renderRows();
    renderHeat();
    renderReliability();
    renderRecent();
  }

  function fail() {
    data = null;
    const msg = "Usage data could not be loaded. Try again shortly.";
    $("[data-plot]").innerHTML = `<p class="uz-empty">${msg}</p>`;
    root.querySelectorAll("[data-kpi-value]").forEach((el) => (el.textContent = "—"));
    root.querySelectorAll("[data-kpi-sub]").forEach((el) => (el.textContent = "Load failed"));
    root.querySelectorAll("[data-kpi-delta]").forEach((el) => (el.hidden = true));
    ["[data-heat]", "[data-reliability]"].forEach((s) => ($(s).innerHTML = `<p class="uz-empty">${msg}</p>`));
    $("[data-rows]").innerHTML = `<tr><td colspan="5" class="uz-empty">${msg}</td></tr>`;
    $("[data-rows-more]").hidden = true;
    $("[data-recent]").innerHTML = `<tr><td colspan="7" class="uz-empty">${msg}</td></tr>`;
  }

  async function load() {
    syncControls();
    syncUrl();
    if (controller) controller.abort();
    const own = (controller = new AbortController());
    const q = new URLSearchParams({ range: state.range });
    if (state.provider) q.set("provider", state.provider);
    if (state.scope !== "account") q.set("scope", state.scope);
    const plotEl = $("[data-plot]");
    const key = q.toString();
    const cached = cache.get(key);
    if (cached) {
      data = cached;
      paint();
    }
    plotEl.setAttribute("aria-busy", "true");
    root.classList.toggle("is-loading", !cached);
    try {
      const r = await fetch("/usage.json?" + q, { signal: controller.signal });
      if (r.status === 401) {
        location.assign("/login");
        return;
      }
      if (!r.ok) throw new Error("usage");
      const fresh = await r.json();
      if (own !== controller) return;
      cache.set(key, fresh);
      data = fresh;
      paint();
    } catch (err) {
      if (err.name !== "AbortError") {
        cache.delete(key);
        fail();
      }
    } finally {
      if (own === controller) {
        plotEl.removeAttribute("aria-busy");
        root.classList.remove("is-loading");
      }
    }
  }

  // Drill-down: a provider filters the page to it; an account scopes to it.
  root.addEventListener("click", (e) => {
    const p = e.target.closest("[data-pick-provider]");
    const u = e.target.closest("[data-pick-user]");
    if (p) state.provider = p.dataset.pickProvider;
    else if (u) {
      state.scope = "user:" + u.dataset.pickUser;
      selectTab("providers", false);
    } else return;
    load();
    root.scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  });

  if (typeof ResizeObserver !== "undefined") {
    let t = 0;
    new ResizeObserver(() => {
      if (!data) return;
      clearTimeout(t);
      t = setTimeout(() => plot.render(false), 100);
    }).observe($("[data-plot]"));
  }
  if (state.tab !== "providers") selectTab(state.tab, false);
  load();
})();
