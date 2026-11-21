---
name: customize-fuse
description: Make custom backdrops for the Fuse chat. Use when the user wants to customize Fuse, change its look, or make or design a backdrop, background or wallpaper for the chat.
metadata:
  category: Design
---

# Customize Fuse: custom backdrops

A backdrop is the animated picture behind the chat transcript. Built-in ones are picked in Appearance > Backdrop. A custom backdrop is one HTML document you write; the user saves it from an artifact with a "Use this" button, and Fuse draws it full-screen behind the chat in a sandboxed iframe (opaque origin, no network). It is saved to the user's account (they keep up to 8; when full, Fuse asks which one the new one replaces), listed under "Your backdrops" in Appearance > Backdrop, and can be picked in every chat theme.

## Compatibility rules (a backdrop that breaks one fails or looks wrong)

- One self-contained HTML document: `<!doctype html>`, inline `<style>` and `<script>` only. Canvas 2D or WebGL is fine.
- No network at all: no external scripts, stylesheets, fonts, images or fetches. Only `data:` URIs for images or fonts. The sandbox blocks everything else, so it would just be missing.
- Pure picture: no text, buttons, links, forms or anything interactive. It never receives clicks or keys.
- Fills the viewport with no scrollbars: `html,body{margin:0;height:100%;overflow:hidden}` and a full-size canvas or element.
- Set `background` on `html,body` to the scene's base colour so it never flashes white while loading.
- Handles `resize` (the chat panel changes size).
- Scales with the viewport: it runs full-window, anywhere from a 390x844 phone to a 2560x1440 monitor. Size art and glyph cells from `Math.min(innerWidth, innerHeight)` (e.g. `cell = Math.max(6, Math.round(Math.min(w, h) / 80))`) and place things at fractions of `w` and `h`, never at fixed pixel sizes or offsets, so it looks the same at every size and in portrait.
- Animate with `requestAnimationFrame`, capped near 30 fps (skip frames under ~33 ms).
- Honour `prefers-reduced-motion: reduce`: draw one still frame and stop.
- Light on CPU/GPU: it runs all day behind the chat. No per-pixel JS loops over a full-resolution canvas; cap `devicePixelRatio` at 1.5; keep particle counts modest; prefer gradients, a few shapes, glyphs, or a small canvas scaled up.
- Keep the middle calm and low-contrast: chat text sits over it. Put detail and brightness toward edges, horizon or corners.
- Pick `scheme`: `"dark"` for a dark backdrop, `"light"` for a light one. Fuse switches its text palette to match.
- At most 128 KB (UTF-8). Name 1-40 characters.
- Deterministic-ish: seed any randomness from a fixed number so it looks the same each load.

## House style

Fuse's built-in scenes are quiet and low-contrast, one hue family each, in four art styles: soft (painted gradients, haze and glows), layered (flat paper-cut silhouettes with atmospheric depth), line (fine strokes on a flat ground) and dots (points, glows or small glyphs in one hue). Most are dark (night blues, violets, ink greens); a few are pale and light. Pick one style and keep to it. Stay tasteful and low-contrast unless the user asks for something loud.

## Workflow

1. Ask nothing unless the request is truly vague; pick a direction from the user's words.
2. Call `create_artifact` with 2-4 options side by side. Preview each live in `<iframe sandbox="allow-scripts">` whose `srcdoc` is the very same HTML string its "Use this" button passes to `window.fuse.setBackdrop({name, scheme, html})`. Keep each option's HTML in one JS array so preview and apply cannot drift apart. Keep the template's preview sizing: the iframe renders at the user's screen size and is scaled down to the card, so the preview matches what they get. A card-sized iframe would show the art several times bigger than it is applied.
3. Tell the user to click "Use this" under the one they like (Fuse asks to confirm, then applies it at once). It only works when the artifact is open inside the Fuse chat.
4. On feedback, change the artifact with `edit_artifact` (do not create a new one), then ask them to try again.

Reverting: choose another backdrop or None in Appearance > Backdrop. Custom backdrops are deleted from the "Yours" group there.

## Template artifact

Inside the JS template literal write `<\/script>` for closing script tags, and do not use backticks or `${` in backdrop code (use `+` to join strings). The preview iframe allows more than the real backdrop does (it inherits the artifact page's rules), so never rely on a CDN even if the preview seems to work.

```html
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{margin:0;padding:16px;background:var(--fuse-bg);color:var(--fuse-text);font:16px/1.5 system-ui,sans-serif}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}
figure{margin:0}
.view{position:relative;overflow:hidden;border:1px solid var(--fuse-line);border-radius:8px}
.view iframe{position:absolute;left:0;top:0;border:0;transform-origin:0 0}
figcaption{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:8px 0}
button{min-height:44px;padding:0 16px;border:0;border-radius:8px;background:var(--fuse-accent);color:var(--fuse-on-accent);font:inherit;cursor:pointer}
</style></head><body>
<div class="grid" id="grid"></div>
<script>
var OPTIONS = [
  {name: "Night glyphs", scheme: "dark", html: `<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0;height:100%;overflow:hidden;background:#0b0f17}canvas{display:block;width:100%;height:100%}</style>
</head><body><canvas id="c"></canvas><script>
(function(){
var c=document.getElementById("c"),x=c.getContext("2d"),d=Math.min(window.devicePixelRatio||1,1.5),w=0,h=0,u=0,last=0;
var still=matchMedia("(prefers-reduced-motion: reduce)").matches;
function draw(t){
  var g=x.createLinearGradient(0,0,0,h);g.addColorStop(0,"#0b0f17");g.addColorStop(1,"#1b1233");
  x.fillStyle=g;x.fillRect(0,0,w,h);x.font=Math.round(u*1.4)+"px monospace";
  for(var i=0;i<60;i++){
    var px=((i*0.137)%1)*w,py=(((i*0.071)%1)*h+t*0.0004*u*(1+i%3))%h;
    if(Math.abs(px-w/2)<w*0.25)continue;
    x.fillStyle="rgba(150,180,255,"+(0.06+0.05*(i%4))+")";x.fillText("*+.:"[i%4],px,py);
  }
}
function size(){w=innerWidth;h=innerHeight;u=Math.max(6,Math.min(w,h)/80);c.width=w*d;c.height=h*d;x.setTransform(d,0,0,d,0,0);draw(last);}
function loop(t){requestAnimationFrame(loop);if(t-last<33)return;last=t;draw(t);}
addEventListener("resize",size);size();if(!still)requestAnimationFrame(loop);
})();
<\/script></body></html>`}
  // , {name: "...", scheme: "dark", html: `...`}
];
// Previews render at the user's screen size, scaled down to the card.
var W = Math.max(screen.availWidth || 1440, 320), H = Math.max(screen.availHeight || 900, 320);
var grid = document.getElementById("grid"), views = [];
function fit() { views.forEach(function (v) { v.frame.style.transform = "scale(" + v.box.clientWidth / W + ")"; }); }
addEventListener("resize", fit);
OPTIONS.forEach(function (o) {
  var fig = document.createElement("figure"), box = document.createElement("div"), frame = document.createElement("iframe");
  box.className = "view";
  box.style.aspectRatio = W + "/" + H;
  frame.style.width = W + "px";
  frame.style.height = H + "px";
  frame.setAttribute("sandbox", "allow-scripts");
  frame.title = o.name;
  frame.srcdoc = o.html;
  var cap = document.createElement("figcaption"), label = document.createElement("span"), use = document.createElement("button");
  label.textContent = o.name;
  use.textContent = "Use this";
  use.onclick = function () { window.fuse.setBackdrop({name: o.name, scheme: o.scheme, html: o.html}); };
  cap.append(label, use);
  box.append(frame);
  fig.append(box, cap);
  grid.append(fig);
  views.push({box: box, frame: frame});
});
fit();
</script></body></html>
```
