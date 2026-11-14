const test = require("node:test");
const assert = require("node:assert/strict");
const { checkSiteUrl } = require("../fuse-desktop/site-url.js");

test("a full https URL passes", () => {
  assert.equal(checkSiteUrl("https://fuse.example.com").origin, "https://fuse.example.com");
});

test("a scheme-less, empty or missing URL fails with a readable message", () => {
  for (const value of ["fuse.example.com", "", undefined]) {
    assert.throws(() => checkSiteUrl(value), /is not a URL; use https:\/\/your-host/);
  }
});

test("protocols outside the allowed list fail", () => {
  assert.throws(() => checkSiteUrl("file:///etc/passwd"), /must use https:\/\/ or http:\/\//);
  assert.throws(() => checkSiteUrl("http://fuse.example.com", ["https:"]), /must use https:\/\//);
});
