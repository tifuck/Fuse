// The phone key bar above the keyboard ([data-terminal-keys]) and line mode
// ([data-terminal-line-form]). The theme gives the bar its place and style;
// the keys are filled in here. The bottom row (Tab, Ctrl, Alt, arrows, the
// keyboard key) stays; the top row has two pages.
import { keySequence, modifierOff, spendModifier, tapModifier, withModifiers } from './core.js';

const TOP = {
  a: [["esc", "Esc"], ["|", "|"], ["~", "~"], ["/", "/"], ["-", "-"], ["up", "↑", "Up"], ["page", "›", "More keys"]],
  b: [["home", "Home"], ["end", "End"], ["pgup", "PgUp", "Page up"], ["pgdn", "PgDn", "Page down"], ["paste", "Paste"], ["up", "↑", "Up"], ["page", "‹", "Back"]],
};
const BOTTOM = [["tab", "Tab"], ["ctrl", "Ctrl"], ["alt", "Alt"], ["left", "←", "Left"], ["down", "↓", "Down"], ["right", "→", "Right"], ["keyboard", "⌨", "Hide keyboard"]];

export function createKeyBar(bar, deps) {
  const mods = { ctrl: modifierOff(), alt: modifierOff() };
  let page = "a";

  function key([name, text, label]) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "term-key";
    btn.tabIndex = -1;
    btn.textContent = text;
    if (label) btn.setAttribute("aria-label", label);
    if (name === "ctrl" || name === "alt") {
      btn.dataset.termMod = name;
      btn.setAttribute("aria-pressed", "false");
    } else btn.dataset.termKey = name;
    return btn;
  }

  function row(keys, kind) {
    const el = document.createElement("div");
    el.className = "term-keys-row";
    el.dataset.termRow = kind;
    el.append(...keys.map(key));
    return el;
  }

  function render() {
    bar.replaceChildren(row(TOP[page], "top"), row(BOTTOM, "bottom"));
    bar.dataset.page = page;
    paintMods();
  }

  function paintMods() {
    for (const btn of bar.querySelectorAll("[data-term-mod]")) {
      const state = mods[btn.dataset.termMod].state;
      btn.dataset.termState = state;
      btn.setAttribute("aria-pressed", String(state !== "off"));
      btn.title = state === "lock" ? "Locked: tap to release" : state === "once" ? "On for the next key: tap twice to lock" : "";
    }
  }

  const armed = () => ({ ctrl: mods.ctrl.state !== "off", alt: mods.alt.state !== "off" });

  function spend() {
    mods.ctrl = spendModifier(mods.ctrl);
    mods.alt = spendModifier(mods.alt);
    paintMods();
  }

  // Keys must not take the focus from the terminal, or the keyboard drops.
  bar.addEventListener("pointerdown", (event) => { if (event.target.closest("button")) event.preventDefault(); });
  bar.addEventListener("mousedown", (event) => event.preventDefault());
  bar.addEventListener("click", (event) => {
    const btn = event.target.closest("button");
    if (!btn) return;
    const mod = btn.dataset.termMod;
    if (mod) {
      mods[mod] = tapModifier(mods[mod], performance.now());
      paintMods();
      return;
    }
    const name = btn.dataset.termKey;
    if (name === "page") { page = page === "a" ? "b" : "a"; render(); return; }
    if (name === "keyboard") { deps.toggleKeyboard(); return; }
    if (name === "paste") { deps.paste(); return; }
    const session = deps.session();
    if (!session) return;
    session.input(keySequence(name, { ...armed(), appCursor: session.term.modes.applicationCursorKeysMode }));
    spend();
  });

  render();

  return {
    // Typed text takes the armed modifiers once (or every time while locked).
    transform(data) {
      const on = armed();
      if (!on.ctrl && !on.alt) return data;
      spend();
      return withModifiers(data, on);
    },
    reset() {
      mods.ctrl = modifierOff();
      mods.alt = modifierOff();
      paintMods();
    },
  };
}

// Line mode: a plain field for keyboards that fight xterm's own (Android
// IMEs that rewrite the whole field per keystroke). Enter sends the line.
export function bindLineMode(form, input, deps) {
  if (!form || !input) return;
  for (const [key, value] of Object.entries({ autocapitalize: "off", autocorrect: "off", autocomplete: "off", spellcheck: "false", enterkeyhint: "send" })) input.setAttribute(key, value);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const session = deps.session();
    if (!session) return;
    session.input(deps.transform(input.value) + "\r");
    input.value = "";
  });
}
