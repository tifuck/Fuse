const test = require("node:test");
const assert = require("node:assert/strict");
const { allowPermission } = require("../fuse-desktop/permissions.js");

const APP = "https://fuse.example.com";
const top = (extra) => ({ requestingUrl: `${APP}/chat`, isMainFrame: true, ...extra });

test("the site's own top frame gets what it uses", () => {
  assert.equal(allowPermission("notifications", top(), APP), true);
  assert.equal(allowPermission("media", top({ mediaTypes: ["audio"] }), APP), true);
  assert.equal(allowPermission("clipboard-sanitized-write", top(), APP), true);
});

test("everything else is denied: camera, other permissions, subframes, other origins", () => {
  assert.equal(allowPermission("media", top({ mediaTypes: ["audio", "video"] }), APP), false);
  assert.equal(allowPermission("geolocation", top(), APP), false);
  assert.equal(allowPermission("clipboard-read", top(), APP), false);
  // An artifact frame is served from the site's URL space but is not the top frame.
  assert.equal(allowPermission("media", top({ isMainFrame: false, mediaTypes: ["audio"] }), APP), false);
  assert.equal(allowPermission("notifications", { requestingUrl: "https://evil.example/", isMainFrame: true }, APP), false);
  assert.equal(allowPermission("notifications", undefined, APP), false);
});

test("external protocol launches are only web links and mailto", () => {
  assert.equal(allowPermission("openExternal", { externalURL: "mailto:a@b.c" }, APP), true);
  for (const url of ["ms-msdt:/id", "file:///C:/x.exe", "search-ms:query=x", "smb://host/share", "not a url"]) {
    assert.equal(allowPermission("openExternal", { externalURL: url }, APP), false, url);
  }
});

test("the permission check handler form (requestingOrigin) is judged the same way", () => {
  assert.equal(allowPermission("notifications", { requestingOrigin: APP, isMainFrame: true }, APP), true);
  assert.equal(allowPermission("notifications", { requestingOrigin: "null" }, APP), false);
});
