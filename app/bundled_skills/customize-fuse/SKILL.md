---
name: customize-fuse
description: Offer changes to how Fuse looks for the user, as one-click options in the chat. Use when the user asks for a backdrop, background or wallpaper; a project's logo or icon, folder color or name; or this chat's title.
metadata:
  category: Design
---

# Customize Fuse

The user can change five things from a set of options you offer:

- `backdrop`: the animated picture behind the chat transcript.
- `project_logo`: the image shown in place of a project's folder icon. A project "icon" or "logo" means this.
- `folder_color`: the color of a project's folder icon (shown while it has no logo).
- `project_name`: the project's name.
- `chat_title`: this chat's title.

You offer them with `create_artifact` and `choices`. Fuse draws the options inline in your reply. Each option gets its own "Use this" button, and Fuse asks the user to confirm before it changes anything. You never change these settings yourself, and nothing has changed until the user clicks.

## When

- Only when the user asks to change one of these, in this turn. Never on your own initiative and never alongside other work.
- Only the fields they named or clearly meant:
  - "Rename the project" means `project_name`.
  - "Rename this chat" means `chat_title`.
  - "A logo", "an icon" or "a new look for the project" means `project_logo`, and also `folder_color` for "a new look".
  - "A wallpaper" or "a background" means `backdrop`.
- Never anything else, such as the machine, folder, permissions or model. These choices cannot change those.
- When the project is unclear, ask which one. The project fields default to this chat's project. To name another, pass `project` with its exact name.

## The call

```json
{"title": "Logo ideas for api", "choices": {
  "project_logo": [{"name": "Bracket", "svg": "<svg ...>...</svg>"}, {"name": "Node", "svg": "<svg ...>...</svg>"}],
  "folder_color": [{"name": "Moss", "color": "#46A758"}, {"name": "Ocean", "color": "#0090FF"}]
}}
```

- Every field is a list of 1-4 options. Offer 2-4 when the user wants to compare, and 1 when they asked for one specific value.
- `project_name` and `chat_title` options are plain strings: `["Release checklist", "Ship 2.4"]`.
- After the call, say in one short sentence what you offered and that they can pick one. Never say it was applied.
- On feedback, change the same artifact with `edit_artifact` or `update_artifact`. Do not create a new one. Then ask them to look again.

## Project logos

Each option is `{name, svg}`, one SVG document of at most 16 KB. Fuse draws it into a 128x128 PNG and shows it at 16 px in lists, so:

- Use a square `viewBox` (e.g. `0 0 64 64`) and fill most of it.
- Use plain shapes only: paths, circles, rects, gradients. No text over two letters, no scripts, no `foreignObject` and no outside links or images. The check refuses them.
- One bold idea per logo, with thick strokes. A detail under 4 units of a 64-unit box disappears at 16 px.
- Leave the background transparent, or make it a full shape (a rounded square or circle). It must read on both light and dark lists, so avoid pure black or white on its own.
- Make the options really different (shape, metaphor, color), not four tints of one.

## Folder colors

Each option is `{name, color}`, with a `#RRGGBB` color and a short name. Fuse makes the folder's back panel from it, lighter on light themes and darker on dark ones, so pick clear mid-tones. The house set is `#E5484D` red, `#F76B15` orange, `#FFC53D` amber, `#46A758` green, `#12A594` teal, `#0090FF` blue, `#3E63DD` indigo, `#8E4EC6` violet, `#D6409F` pink and `#A18072` brown. Use it unless the user wants something else.

## Names and titles

- A project name is 1-80 characters and a chat title is 1-80 characters. Keep both short and plain, like the user's own words.
- No quotes around them and no emoji unless asked.

## Backdrops

Each option is `{name, scheme, html}`. `scheme` is `"dark"` for a dark backdrop or `"light"` for a light one, and Fuse switches its text palette to match. The name is 1-40 characters. Fuse previews the chosen option at the user's screen shape. When used, it saves the backdrop to the user's account (they keep up to 8, and Fuse asks which one to replace when full) and turns it on in the current layout on this device. Other backdrops are picked in Appearance > Backdrop.

The HTML rules (a backdrop that breaks one fails or looks wrong):

- One self-contained HTML document: `<!doctype html>`, inline `<style>` and `<script>` only. Canvas 2D or WebGL is fine. Workers and WebRTC are blocked.
- No network at all: no external scripts, stylesheets, fonts, images or fetches. Only `data:` URIs for images or fonts. The preview blocks them just like the real backdrop does.
- A pure picture: no text, buttons, links, forms or anything interactive. It never receives clicks or keys.
- Fill the viewport with no scrollbars: `html,body{margin:0;height:100%;overflow:hidden}` and a full-size canvas or element.
- Set `background` on `html,body` to the scene's base color, so it never flashes white while loading.
- Handle `resize`, because the chat panel changes size.
- Scale with the viewport. It runs full-window, anywhere from a 390x844 phone to a 2560x1440 monitor.
  - Size art and glyph cells from `Math.min(innerWidth, innerHeight)`, e.g. `cell = Math.max(6, Math.round(Math.min(w, h) / 80))`.
  - Place things at fractions of `w` and `h`, never at fixed pixel sizes or offsets.
- Animate with `requestAnimationFrame`, capped near 30 fps (skip frames under ~33 ms).
- Honour `prefers-reduced-motion: reduce`: draw one still frame and stop.
- Keep CPU and GPU use light, because it runs all day behind the chat.
  - No per-pixel JS loops over a full-resolution canvas.
  - Cap `devicePixelRatio` at 1.5 and keep particle counts modest.
  - Prefer gradients, a few shapes, glyphs, or a small canvas scaled up.
- Keep the middle calm and low-contrast, because chat text sits over it. Put detail and brightness toward the edges, horizon or corners.
- At most 128 KB (UTF-8).
- Seed any randomness from a fixed number, so it looks the same on each load.

House style: Fuse's built-in scenes are quiet and low-contrast, one hue family each. They use four art styles: soft (painted gradients, haze and glows), layered (flat paper-cut silhouettes with atmospheric depth), line (fine strokes on a flat ground) and dots (points, glows or small glyphs in one hue). Most are dark (night blues, violets, ink greens) and a few are pale and light. Pick one style per backdrop. Stay tasteful and low-contrast unless the user asks for something loud.

A minimal backdrop:

```html
<!doctype html><html><head><meta charset="utf-8">
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
</script></body></html>
```

Reverting a backdrop: choose another one, or None, in Appearance > Backdrop. Custom backdrops are deleted from the "Yours" group there. If a backdrop hangs the page, open `/chat?nobackdrop`: it loads no custom backdrop, so Appearance still works.
