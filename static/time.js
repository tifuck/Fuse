// The time zone every page shows times in: the device's own unless the user
// picked one under Appearance › Time zone (saved in this browser). Loaded
// first on every page so later scripts and all three chat views follow it.
(() => {
  const STORE = "fuse.timeZone";
  const detected = () => {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch (_) { return "UTC"; }
  };
  const valid = (zone) => {
    try { new Intl.DateTimeFormat("en-US", { timeZone: zone }); return true; } catch (_) { return false; }
  };
  let chosen = "";
  try { chosen = localStorage.getItem(STORE) || ""; } catch (_) {}
  if (chosen && !valid(chosen)) chosen = "";
  const zone = () => chosen || detected();

  // Server-rendered labels (usage limits, resets, dates) read this cookie.
  document.cookie = `fuse_tz=${encodeURIComponent(zone())}; path=/; max-age=31536000; samesite=lax`;

  // Every toLocale*String call without its own timeZone uses the chosen zone.
  for (const name of ["toLocaleString", "toLocaleDateString", "toLocaleTimeString"]) {
    const native = Date.prototype[name];
    Date.prototype[name] = function (locales, opts) {
      return native.call(this, locales, !chosen || (opts && opts.timeZone) ? opts : { ...opts, timeZone: chosen });
    };
  }

  // Calendar fields of a moment in the chosen zone (weekday: 0 = Monday).
  const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  let fmt = null;
  const parts = (when) => {
    // Built once: changing the zone reloads the page.
    if (!fmt) {
      fmt = new Intl.DateTimeFormat("en-US", {
        timeZone: zone(), hourCycle: "h23", weekday: "short",
        year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric",
      });
    }
    const out = {};
    for (const { type, value } of fmt.formatToParts(new Date(when))) out[type] = value;
    return { year: +out.year, month: +out.month, day: +out.day, hour: +out.hour % 24, minute: +out.minute, weekday: WEEKDAYS.indexOf(out.weekday) };
  };
  // Whole days since 1970-01-01 of the calendar date a moment falls on.
  const dayNumber = (when) => {
    const p = parts(when);
    return Date.UTC(p.year, p.month - 1, p.day) / 86400000;
  };

  const set = (value) => {
    try {
      if (value) localStorage.setItem(STORE, value);
      else localStorage.removeItem(STORE);
    } catch (_) {}
  };

  // Every <select data-time-zone> (chat's Appearance sheet in each view and
  // Settings › Appearance): Automatic plus every zone the browser knows.
  const bind = () => document.querySelectorAll("select[data-time-zone]").forEach((select) => {
    const zones = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
    if (!zones.includes("UTC")) zones.push("UTC");
    if (chosen && !zones.includes(chosen)) zones.push(chosen);
    select.replaceChildren(new Option(`Automatic (${detected().replace(/_/g, " ")})`, ""),
      ...zones.map((z) => new Option(z.replace(/_/g, " "), z)));
    select.value = chosen;
    select.addEventListener("change", () => {
      set(select.value);
      location.reload();
    });
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();

  window.fuseTime = { zone, detected, parts, dayNumber };
})();
