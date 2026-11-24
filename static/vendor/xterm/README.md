# xterm.js (vendored)

The browser terminal (`static/chat/terminal.js`) loads these with `import()` on
first open. Only the ESM builds and the stylesheet are kept; the source-map
comments are removed because the maps are not vendored.

| File | Package | Version |
| --- | --- | --- |
| `xterm.mjs`, `xterm.css` | `@xterm/xterm` | 6.0.0 |
| `addon-fit.mjs` | `@xterm/addon-fit` | 0.11.0 |
| `addon-webgl.mjs` | `@xterm/addon-webgl` | 0.19.0 |
| `addon-unicode11.mjs` | `@xterm/addon-unicode11` | 0.9.0 |
| `addon-web-links.mjs` | `@xterm/addon-web-links` | 0.12.0 |
| `addon-search.mjs` | `@xterm/addon-search` | 0.16.0 |
| `addon-clipboard.mjs` | `@xterm/addon-clipboard` | 0.2.0 |

All are MIT licensed by the xterm.js authors; `LICENSE` is the one from
`@xterm/xterm` (the addons carry the same text with their own years).

To update: `npm pack @xterm/xterm@X @xterm/addon-…@Y` in a temp dir, copy
`package/lib/*.mjs` and `package/css/xterm.css`, drop the trailing
`//# sourceMappingURL=` line, and update this table.
