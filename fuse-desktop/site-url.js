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

module.exports = { checkSiteUrl };
