// The site a build wraps. A scheme-less or missing URL used to reach
// `new URL()` at startup and kill every launch of the installed app.
// No Electron here, so tests/desktop_site_url.test.cjs runs it under node.
function checkSiteUrl(value, protocols = ["https:", "http:"]) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`Fuse site URL ${JSON.stringify(value)} is not a URL; use https://your-host`);
  }
  if (!protocols.includes(url.protocol)) {
    throw new Error(`Fuse site URL ${value} must use ${protocols.map((p) => `${p}//`).join(" or ")}`);
  }
  return url;
}

// What someone types on the connect page: a bare host gets https://, and only
// the origin is kept, so a pasted /chat link still connects.
function serverOrigin(input) {
  const value = String(input || "").trim();
  return checkSiteUrl(/^[a-z][a-z0-9+.-]*:\/\//i.test(value) ? value : `https://${value}`).origin;
}

// /v1/health on every Fuse server, healthy (200) or not (503), names its
// database and background tasks; another site at that address does not.
function isFuseHealth(body) {
  return Boolean(body && typeof body === "object" && body.database && body.background);
}

module.exports = { checkSiteUrl, isFuseHealth, serverOrigin };
