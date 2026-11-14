// Chromium grants every permission request by default. The site needs only
// these, and only in its own top frame; artifact frames and any other origin
// get nothing. openExternal is a navigation to a non-web scheme (a subframe
// can start one without will-navigate seeing it): only web links and mailto.
const SITE_PERMISSIONS = new Set(["notifications", "media", "clipboard-sanitized-write", "fullscreen"]);
const EXTERNAL_PROTOCOLS = new Set(["https:", "http:", "mailto:"]);

function originOf(url) {
  try {
    return new URL(url).origin;
  } catch {
    return "";
  }
}

function allowPermission(permission, details, appOrigin) {
  details = details || {};
  if (permission === "openExternal") {
    try {
      return EXTERNAL_PROTOCOLS.has(new URL(details.externalURL).protocol);
    } catch {
      return false;
    }
  }
  if (!SITE_PERMISSIONS.has(permission) || details.isMainFrame === false) return false;
  if (originOf(details.requestingUrl || details.requestingOrigin) !== appOrigin) return false;
  // Dictation needs the microphone; nothing on the site uses the camera.
  const media = details.mediaTypes || (details.mediaType ? [details.mediaType] : []);
  return permission !== "media" || !media.includes("video");
}

module.exports = { allowPermission, EXTERNAL_PROTOCOLS };
