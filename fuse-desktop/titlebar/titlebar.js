const frame = window.fuseFrame;
document.documentElement.dataset.platform = frame.platform;

for (const id of ["minimize", "maximize", "close"]) {
  document.getElementById(id).addEventListener("click", () => frame.send(id));
}
document.querySelector(".bar").addEventListener("dblclick", (event) => {
  if (!event.target.closest("button")) frame.send("maximize");
});

const maximize = document.getElementById("maximize");
const TOKENS = ["ink", "mist", "mute", "line"];

// The logo is monochrome: the theme's text colour on transparency, never the accent.
function applyTheme(theme) {
  const root = document.documentElement;
  for (const name of TOKENS) {
    if (theme[name]) root.style.setProperty(`--${name}`, theme[name]);
  }
  root.style.colorScheme = theme.scheme === "light" ? "light" : "dark";
  root.dataset.scheme = theme.scheme === "light" ? "light" : "dark";
  const css = getComputedStyle(root);
  sendIcon(css.getPropertyValue("--mist").trim());
}

// build/icon.png redrawn in the theme's colours (same geometry as static/icon.svg).
const MARK = new Path2D("M38.95 40.77A12 12 0 0 0 38.95 59.23A14 14 0 0 1 40.77 61.05A12 12 0 0 0 59.23 61.05A14 14 0 1 1 59.23 78.95A12 12 0 0 0 40.77 78.95A14 14 0 1 1 21.05 59.23A12 12 0 0 0 21.05 40.77A14 14 0 1 1 38.95 40.77ZM56 30a14 14 0 1 0 28 0a14 14 0 1 0 -28 0Z");
let lastIcon = "";
function sendIcon(mist) {
  if (mist === lastIcon) return;
  lastIcon = mist;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 256;
  const ctx = canvas.getContext("2d");
  ctx.scale(0.5, 0.5);
  ctx.fillStyle = mist;
  ctx.translate(-69, -69);
  ctx.scale(6.5, 6.5);
  ctx.fill(MARK);
  frame.icon(canvas.toDataURL("image/png"));
}

frame.onState(({ maximized, focused, theme }) => {
  if (theme) applyTheme(theme);
  document.body.classList.toggle("is-maximized", maximized);
  document.body.classList.toggle("is-blurred", !focused);
  maximize.title = maximized ? "Restore" : "Maximize";
  maximize.setAttribute("aria-label", maximize.title);
});
