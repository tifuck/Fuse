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

// The logo is monochrome: the theme's text colour outlined in its background
// colour on transparency, never the accent.
function applyTheme(theme) {
  const root = document.documentElement;
  for (const name of TOKENS) {
    if (theme[name]) root.style.setProperty(`--${name}`, theme[name]);
  }
  root.style.colorScheme = theme.scheme === "light" ? "light" : "dark";
  root.dataset.scheme = theme.scheme === "light" ? "light" : "dark";
  const css = getComputedStyle(root);
  sendIcon(css.getPropertyValue("--ink").trim(), css.getPropertyValue("--mist").trim());
}

// build/icon.png redrawn in the theme's colours (same geometry as static/icon.svg).
const MARK = new Path2D("M295 0H565C617 0 650 32 650 70C650 110 619 137 580 137H430C375 137 340 164 340 195C340 220 356 237 380 237H530C563 237 585 261 585 293C585 327 561 353 521 353H347C328 353 315 366 315 386V450C315 530 270 582 212 582C177 582 155 554 157 516C159 476 186 452 188 415C190 383 147 364 143 332C140 304 165 286 191 268C225 244 247 216 245 182C243 148 223 125 199 125C173 125 155 146 148 180C140 218 142 260 125 282C110 298 90 304 65 304C27 304 0 274 0 238C0 200 30 176 70 168C113 160 137 140 157 108C188 56 225 0 295 0ZM118 332C107 350 110 372 111 398C112 422 93 440 70 440C47 440 30 422 30 401C30 377 50 360 75 356C95 353 109 343 118 332Z");
let lastIcon = "";
function sendIcon(ink, mist) {
  const key = [ink, mist].join();
  if (key === lastIcon) return;
  lastIcon = key;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 256;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = mist;
  ctx.translate(10, 22);
  ctx.scale(0.363, 0.363);
  ctx.strokeStyle = ink;
  ctx.lineWidth = 27;
  ctx.lineJoin = "round";
  ctx.stroke(MARK);
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
