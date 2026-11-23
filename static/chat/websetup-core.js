// Web search setup (chat/websetup.js): when to ask, the machine rows and
// what is suggested. No DOM.

// A dismissed prompt stays away this long in this browser.
export const SNOOZE_MS = 24 * 60 * 60 * 1000;

export const snoozeKey = (uid) => "fuse.websetup.snooze:" + uid;

// Ask only while web search is off for want of a machine, and not again
// within a day of the last dismissal.
export function shouldPrompt(web, snoozedUntil, now) {
  return web === false && !(Number(snoozedUntil) > now);
}

// One row per connector, with the others behind the same public IP.
export function setupRows(overview) {
  const machines = (overview?.groups || []).flatMap((group) => group.machines || []);
  return machines.filter((m) => m && Number.isSafeInteger(m.id)).map((m) => {
    const shares = m.network ? machines.filter((o) => o.id !== m.id && o.network === m.network).map((o) => String(o.alias)) : [];
    const usable = Boolean(m.supported);
    return {
      id: m.id,
      alias: String(m.alias || ""),
      ip: String(m.ip || ""),
      network: String(m.network || ""),
      online: Boolean(m.online),
      usable,
      selected: Boolean(m.selected),
      shares,
      status: !usable ? "needs connector " + (overview.min_version || "update") : m.online ? "online" : "offline",
    };
  });
}

// What starts ticked: the user's own choice, or else one online machine per
// public IP (machines behind one IP add nothing but a backup).
export function suggested(rows) {
  const picked = rows.filter((row) => row.selected).map((row) => row.id);
  if (picked.length) return picked;
  const seen = new Set();
  const out = [];
  for (const row of rows) {
    if (!row.usable || !row.online) continue;
    const key = row.network || "#" + row.id;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(row.id);
  }
  return out;
}

// Rows whose public IP is worth reading before the user picks.
export const needsIp = (rows) => rows.some((row) => row.usable && row.online && !row.ip);

export function saveLabel(ticked) {
  return ticked > 0 ? "Use " + ticked + (ticked === 1 ? " machine" : " machines") : "Pick a machine";
}
