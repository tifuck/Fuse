// Build installers for this instance without committing its URL or app id.
// Values come from FUSE_URL / FUSE_APP_ID or site.json (gitignored):
//   {"url": "https://fuse.example.com", "appId": "com.example.fuse"}
// Keep appId stable once released: installed apps update only within it.
const { spawnSync } = require("child_process");
const { readFileSync } = require("fs");
const path = require("path");
const { checkSiteUrl } = require("../site-url");

function fail(message) {
  console.error(message);
  process.exit(1);
}

const root = path.join(__dirname, "..");
let site = {};
try {
  site = JSON.parse(readFileSync(path.join(root, "site.json"), "utf8")) || {};
} catch (err) {
  if (err.code !== "ENOENT") fail(`fuse-desktop/site.json: ${err.message}`);
}
const url = String(process.env.FUSE_URL || site.url || "").replace(/\/+$/, "");
const appId = process.env.FUSE_APP_ID || site.appId || "";
if (!url || !appId) fail("Set FUSE_URL and FUSE_APP_ID, or create fuse-desktop/site.json with url and appId.");
// https only: the app refuses redirects off its origin, so an http URL the
// server upgrades would open a blank window.
try {
  checkSiteUrl(url, ["https:"]);
} catch (err) {
  fail(err.message);
}
const bin = path.join(root, "node_modules", ".bin", process.platform === "win32" ? "electron-builder.cmd" : "electron-builder");
const args = [
  ...process.argv.slice(2),
  `-c.appId=${appId}`,
  `-c.publish.provider=generic`,
  `-c.publish.url=${url}/desktop/`,
  `-c.extraMetadata.fuseUrl=${url}`,
  `-c.extraMetadata.fuseAppId=${appId}`,
];
const result = spawnSync(bin, args, { stdio: "inherit", cwd: root, shell: process.platform === "win32" });
if (result.error) fail(`Cannot run ${bin}: ${result.error.message}`);
if (result.signal) fail(`electron-builder was killed by ${result.signal}`);
process.exit(result.status);
