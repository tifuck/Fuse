// The choices kit: draws window.FUSE_CHOICES (a spec checked by
// app/artifacts.py _choices_text) into #choices as options for the user's
// own Fuse settings, each with its own Use this button. Inlined into every
// choices artifact page by _CHOICES_SHELL; no libraries, no network.
//
// Spec: {project: {id, host_id, name}, chat, backdrop: [{name, scheme, html}],
//   project_logo: [{name, svg}], folder_color: [{name, color}],
//   project_name: [text], chat_title: [text]}
// A button only says which option was clicked ({type: "choose", field,
// index}); the chat (static/chat/choices.js) loads the options from the
// server, confirms and applies. The chat answers "choices-hello" and every
// choice with "choices-state": the project's and chat's current values and
// what this page applied, so a card shows what is in use. Without an answer
// (the page opened outside a chat) the buttons stay off and say why.
(function () {
  "use strict";
  var spec = window.FUSE_CHOICES || {};
  var root = document.getElementById("choices");
  var P = window.parent !== window ? window.parent : null;
  var view = (/[?&]view=(console|tail|desktop)(?:&|$)/.exec(location.search) || [])[1] || "";
  var FIELDS = [
    ["backdrop", "Backdrop"],
    ["project_logo", "Logo"],
    ["folder_color", "Folder color"],
    ["project_name", "Project name"],
    ["chat_title", "Chat title"],
  ].filter(function (f) { return Array.isArray(spec[f[0]]) && spec[f[0]].length; });
  var FOLDER_BACK = "M1.75 4.25C1.75 3.56 2.31 3 3 3h3.1c.33 0 .64.13.88.37L8.1 4.5H13c.69 0 1.25.56 1.25 1.25v6.5c0 .69-.56 1.25-1.25 1.25H3c-.69 0-1.25-.56-1.25-1.25z";
  var FOLDER_FRONT = "M1.75 6.75c0-.55.45-1 1-1h10.5c.55 0 1 .45 1 1v5.5c0 .69-.56 1.25-1.25 1.25H3c-.69 0-1.25-.56-1.25-1.25z";
  // What a real backdrop may do (app/backdrops.py CSP): previews keep to it.
  var BACKDROP_CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'";
  var state = null;
  var waited = false;
  var pending = "";
  var tab = 0;
  var preview = 0;

  // Each view's own shape: Console square and bold, Tail small radii,
  // Desktop rounded with a light primary button.
  var shape = {
    console: "--cx-r:0;--cx-card-r:0;--cx-card:var(--fuse-raise);--cx-card-line:var(--fuse-line);--cx-pri:var(--fuse-accent);--cx-on-pri:var(--fuse-on-accent);--cx-pri-w:700;--cx-ghost:var(--fuse-line)",
    tail: "--cx-r:6px;--cx-card-r:6px;--cx-card:var(--fuse-surface);--cx-card-line:transparent;--cx-pri:var(--fuse-accent);--cx-on-pri:var(--fuse-on-accent);--cx-pri-w:400;--cx-ghost:color-mix(in srgb,var(--fuse-text) 24%,transparent)",
    desktop: "--cx-r:8px;--cx-card-r:10px;--cx-card:color-mix(in srgb,var(--fuse-text) 5%,transparent);--cx-card-line:transparent;--cx-pri:var(--fuse-text);--cx-on-pri:var(--fuse-bg);--cx-pri-w:500;--cx-ghost:var(--fuse-line)",
  }[view] || "--cx-r:8px;--cx-card-r:8px;--cx-card:var(--fuse-surface);--cx-card-line:var(--fuse-line);--cx-pri:var(--fuse-accent);--cx-on-pri:var(--fuse-on-accent);--cx-pri-w:600;--cx-ghost:var(--fuse-line)";

  var style = document.createElement("style");
  style.textContent =
    ":root{" + shape + "}" +
    "#choices{display:grid;gap:12px}" +
    ".cx-tabs{display:flex;flex-wrap:wrap;gap:4px}" +
    ".cx-tab{min-height:30px;padding:0 12px;border:1px solid var(--cx-ghost);border-radius:var(--cx-r);background:transparent;color:var(--fuse-muted);font:inherit;font-size:calc(13px * var(--fuse-scale,1));cursor:pointer}" +
    ".cx-tab[aria-selected=true]{border-color:var(--fuse-text);color:var(--fuse-text)}" +
    ".cx-head h2{margin:0;font-size:calc(15px * var(--fuse-scale,1));font-weight:600;line-height:1.35;display:flex;align-items:center;gap:6px;flex-wrap:wrap}" +
    ".cx-head p{margin:2px 0 0;color:var(--fuse-muted);font-size:calc(12.5px * var(--fuse-scale,1))}" +
    ".cx-head img,.cx-head svg{width:16px;height:16px;flex:none}" +
    ".cx-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin-top:10px}" +
    ".cx-opt{display:flex;flex-direction:column;border:1px solid var(--cx-card-line);border-radius:var(--cx-card-r);background:var(--cx-card);overflow:hidden}" +
    ".cx-opt.is-on{box-shadow:inset 0 0 0 1px var(--fuse-accent)}" +
    ".cx-stage{display:grid;place-items:center;gap:10px;padding:16px 10px 12px}" +
    ".cx-big{width:48px;height:48px}" +
    ".cx-mock{display:flex;align-items:center;gap:6px;max-width:100%;color:var(--fuse-muted);font-size:calc(12px * var(--fuse-scale,1))}" +
    ".cx-mock img,.cx-mock svg{width:16px;height:16px;flex:none}" +
    ".cx-mock span,.cx-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}" +
    ".cx-foot{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 10px;border-top:1px solid var(--fuse-line)}" +
    ".cx-label{min-width:0;font-size:calc(13px * var(--fuse-scale,1))}" +
    ".cx-label small{display:block;color:var(--fuse-muted);font-size:calc(11.5px * var(--fuse-scale,1))}" +
    ".cx-use{flex:none;min-height:30px;padding:0 12px;border:1px solid var(--cx-pri);border-radius:var(--cx-r);background:var(--cx-pri);color:var(--cx-on-pri);font:inherit;font-size:calc(12.8px * var(--fuse-scale,1));font-weight:var(--cx-pri-w);cursor:pointer;white-space:nowrap}" +
    ".cx-use:disabled{cursor:default;opacity:.45}" +
    ".cx-use[aria-busy=true]{opacity:.7;cursor:progress}" +
    ".cx-inuse{flex:none;display:inline-flex;align-items:center;gap:4px;min-height:30px;color:var(--fuse-ok);font-size:calc(12.8px * var(--fuse-scale,1));white-space:nowrap}" +
    ".cx-lines{margin-top:10px;border:1px solid var(--cx-card-line);border-radius:var(--cx-card-r);background:var(--cx-card)}" +
    ".cx-line{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:8px 10px 8px 12px}" +
    ".cx-line+.cx-line{border-top:1px solid var(--fuse-line)}" +
    ".cx-text{min-width:0;overflow-wrap:anywhere;font-size:calc(14px * var(--fuse-scale,1))}" +
    ".cx-bd{display:grid;gap:8px;margin-top:10px}" +
    "@media (min-width:640px){.cx-bd{grid-template-columns:minmax(0,3fr) minmax(0,2fr);align-items:start}}" +
    ".cx-screen{position:relative;overflow:hidden;max-width:100%;border:1px solid var(--fuse-line);border-radius:var(--cx-card-r);background:var(--fuse-bg)}" +
    ".cx-screen iframe{position:absolute;left:0;top:0;border:0;transform-origin:0 0;pointer-events:none}" +
    ".cx-bd .cx-lines{margin-top:0}" +
    ".cx-pick{display:flex;flex-direction:column;align-items:flex-start;min-width:0;flex:1;padding:4px 0;border:0;background:transparent;color:inherit;font:inherit;text-align:left;cursor:pointer}" +
    ".cx-line.is-shown{box-shadow:inset 2px 0 0 var(--fuse-accent)}" +
    ".cx-note{margin:0;color:var(--fuse-muted);font-size:calc(12.5px * var(--fuse-scale,1))}" +
    ".cx-tab:focus-visible,.cx-use:focus-visible,.cx-pick:focus-visible{outline:2px solid var(--fuse-accent);outline-offset:2px}" +
    "@media (pointer:coarse){.cx-use,.cx-tab,.cx-inuse{min-height:44px}.cx-pick{min-height:44px;justify-content:center}}";
  document.head.appendChild(style);

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function svgUrl(svg) {
    return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  }

  function img(src, cls) {
    var node = el("img", cls);
    node.src = src;
    node.alt = "";
    return node;
  }

  // The project's folder, tinted as the chat tints it (static/projects-ui.js).
  function folder(color, cls) {
    var NS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("aria-hidden", "true");
    if (cls) svg.setAttribute("class", cls);
    svg.style.setProperty("--fp-color", color);
    [[FOLDER_BACK, "light-dark(color-mix(in oklch, var(--fp-color) 72%, #fff), color-mix(in oklch, var(--fp-color) 50%, #000))"],
     [FOLDER_FRONT, "var(--fp-color)"]].forEach(function (part) {
      var path = document.createElementNS(NS, "path");
      path.setAttribute("d", part[0]);
      path.style.fill = part[1];
      svg.appendChild(path);
    });
    return svg;
  }

  function project() {
    return (state && state.project) || { name: (spec.project && spec.project.name) || "this project" };
  }

  function currentIcon() {
    var p = project();
    if (p.logo) return img(p.logo);
    return folder(p.color || "#8F978F");
  }

  // Why a field cannot be used here, or "".
  function blocked(field) {
    if (!state) return "";
    if (state.unavailable) return "These choices can only be used from the chat they were offered in.";
    if (field !== "backdrop" && field !== "chat_title" && state.project && state.project.missing) return "This project is no longer in your projects.";
    if (field === "chat_title" && state.chat && state.chat.missing) return "This chat is no longer in your chats.";
    return "";
  }

  function inUse(field, index, option) {
    if (!state) return false;
    if (state.applied && state.applied[field] === index) return true;
    var p = state.project || {};
    if (field === "folder_color") return !p.logo && String(p.color || "").toUpperCase() === option.color;
    if (field === "project_name") return p.name === option;
    if (field === "chat_title") return Boolean(state.chat) && state.chat.title === option;
    return false;
  }

  function action(field, index, option, what) {
    if (inUse(field, index, option)) {
      var on = el("span", "cx-inuse", "In use");
      on.insertBefore(check(), on.firstChild);
      return on;
    }
    var button = el("button", "cx-use", "Use this");
    button.type = "button";
    button.setAttribute("aria-label", "Use " + what);
    var key = field + ":" + index;
    if (pending === key) button.setAttribute("aria-busy", "true");
    button.disabled = !state || Boolean(pending) || Boolean(blocked(field));
    button.addEventListener("click", function () {
      if (!P || button.disabled) return;
      pending = key;
      draw();
      P.postMessage({ fuseArtifact: 1, type: "choose", field: field, index: index }, "*");
    });
    return button;
  }

  function check() {
    var NS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("width", "14");
    svg.setAttribute("height", "14");
    svg.setAttribute("aria-hidden", "true");
    var path = document.createElementNS(NS, "path");
    path.setAttribute("d", "M3.5 8.5l3 3 6-7");
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "1.6");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    svg.appendChild(path);
    return svg;
  }

  function head(title, sub, icon) {
    var box = el("header", "cx-head");
    var h = el("h2");
    if (icon) h.appendChild(icon);
    h.appendChild(document.createTextNode(title));
    box.appendChild(h);
    if (sub) box.appendChild(el("p", null, sub));
    return box;
  }

  function tile(stage, label, sub, button, on) {
    var box = el("div", "cx-opt" + (on ? " is-on" : ""));
    box.appendChild(stage);
    var foot = el("div", "cx-foot");
    var name = el("span", "cx-label", label);
    if (sub) name.appendChild(el("small", null, sub));
    foot.appendChild(name);
    foot.appendChild(button);
    box.appendChild(foot);
    return box;
  }

  function mock(icon) {
    var row = el("span", "cx-mock");
    row.appendChild(icon);
    row.appendChild(el("span", null, project().name));
    return row;
  }

  function logos(section) {
    var p = project();
    section.appendChild(head("Logo for " + p.name, "Shown in place of the folder icon, on every device.", currentIcon()));
    var grid = el("div", "cx-grid");
    spec.project_logo.forEach(function (option, i) {
      var stage = el("div", "cx-stage");
      stage.appendChild(img(svgUrl(option.svg), "cx-big"));
      stage.appendChild(mock(img(svgUrl(option.svg))));
      var button = action("project_logo", i, option, option.name + " as the logo");
      grid.appendChild(tile(stage, option.name, "", button, button.tagName !== "BUTTON"));
    });
    section.appendChild(grid);
  }

  function colors(section) {
    var p = project();
    var sub = p.logo ? "Shows only while the project has no logo." : "Shown on every device.";
    section.appendChild(head("Folder color for " + p.name, sub, currentIcon()));
    var grid = el("div", "cx-grid");
    spec.folder_color.forEach(function (option, i) {
      var stage = el("div", "cx-stage");
      stage.appendChild(folder(option.color, "cx-big"));
      stage.appendChild(mock(folder(option.color)));
      var button = action("folder_color", i, option, option.name + " as the folder color");
      grid.appendChild(tile(stage, option.name, option.color, button, button.tagName !== "BUTTON"));
    });
    section.appendChild(grid);
  }

  function texts(section, field, title, now, what) {
    section.appendChild(head(title, now));
    var list = el("div", "cx-lines");
    spec[field].forEach(function (option, i) {
      var line = el("div", "cx-line");
      line.appendChild(el("span", "cx-text", option));
      line.appendChild(action(field, i, option, "\u201c" + option + "\u201d as the " + what));
      list.appendChild(line);
    });
    section.appendChild(list);
  }

  // One live preview, the shown option, at the user's screen size scaled
  // down to the box; the list switches it and has each option's button.
  function backdrops(section) {
    section.appendChild(head("Backdrop", "Drawn behind the chat in this layout, on this device."));
    var W = Math.max(screen.availWidth || 1440, 320);
    var H = Math.max(screen.availHeight || 900, 320);
    var wrap = el("div", "cx-bd");
    var screenBox = el("div", "cx-screen");
    screenBox.dataset.index = String(preview);
    screenBox.style.aspectRatio = W + "/" + H;
    // At most 300px tall, whatever the screen's shape.
    screenBox.style.width = Math.round(300 * W / H) + "px";
    var frame = document.createElement("iframe");
    frame.setAttribute("sandbox", "allow-scripts");
    frame.setAttribute("tabindex", "-1");
    frame.setAttribute("aria-hidden", "true");
    frame.style.width = W + "px";
    frame.style.height = H + "px";
    var option = spec.backdrop[Math.min(preview, spec.backdrop.length - 1)];
    var meta = '<meta http-equiv="Content-Security-Policy" content="' + BACKDROP_CSP + '">';
    frame.srcdoc = /<head[^>]*>/i.test(option.html) ? option.html.replace(/<head[^>]*>/i, function (m) { return m + meta; }) : meta + option.html;
    screenBox.appendChild(frame);
    var fit = function () {
      var scale = Math.min(screenBox.clientWidth / W, screenBox.clientHeight / H) || 0;
      frame.style.transform = "scale(" + scale + ")";
    };
    new ResizeObserver(fit).observe(screenBox);
    wrap.appendChild(screenBox);
    wrap.appendChild(backdropList());
    section.appendChild(wrap);
  }

  function backdropList() {
    var list = el("div", "cx-lines");
    spec.backdrop.forEach(function (item, i) {
      var line = el("div", "cx-line" + (i === preview ? " is-shown" : ""));
      var pick = el("button", "cx-pick");
      pick.type = "button";
      pick.setAttribute("aria-pressed", String(i === preview));
      pick.setAttribute("aria-label", "Preview " + item.name);
      pick.appendChild(el("span", "cx-label", item.name));
      pick.lastChild.appendChild(el("small", null, item.scheme === "light" ? "Light" : "Dark"));
      pick.addEventListener("click", function () { preview = i; draw(); });
      line.appendChild(pick);
      line.appendChild(action("backdrop", i, item, "\u201c" + item.name + "\u201d as the backdrop"));
      list.appendChild(line);
    });
    return list;
  }

  function note(field) {
    var why = !state ? (waited ? "Open this in a Fuse chat to use it." : "") : blocked(field);
    var old = root.querySelector(".cx-note");
    if (old) old.remove();
    if (why) root.appendChild(el("p", "cx-note", why));
  }

  function draw() {
    var focused = document.activeElement && document.activeElement.getAttribute("aria-label");
    var field = FIELDS[tab] && FIELDS[tab][0];
    // A redraw that keeps the shown backdrop swaps only its list: moving
    // the live preview's frame would start it over.
    var live = root.querySelector(".cx-screen");
    if (field === "backdrop" && live && live.dataset.index === String(preview)) {
      root.querySelector(".cx-bd .cx-lines").replaceWith(backdropList());
      note(field);
      refocus(focused);
      return;
    }
    root.textContent = "";
    if (FIELDS.length > 1) {
      var tabs = el("div", "cx-tabs");
      tabs.setAttribute("role", "tablist");
      FIELDS.forEach(function (f, i) {
        var t = el("button", "cx-tab", f[1]);
        t.type = "button";
        t.setAttribute("role", "tab");
        t.setAttribute("aria-selected", String(i === tab));
        t.tabIndex = i === tab ? 0 : -1;
        t.addEventListener("click", function () { tab = i; draw(); root.querySelectorAll(".cx-tab")[i].focus(); });
        t.addEventListener("keydown", function (e) {
          if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
          e.preventDefault();
          tab = (tab + (e.key === "ArrowRight" ? 1 : FIELDS.length - 1)) % FIELDS.length;
          draw();
          root.querySelectorAll(".cx-tab")[tab].focus();
        });
        tabs.appendChild(t);
      });
      root.appendChild(tabs);
    }
    var section = el("section", "cx-sec");
    if (FIELDS.length > 1) section.setAttribute("role", "tabpanel");
    if (field === "backdrop") backdrops(section);
    else if (field === "project_logo") logos(section);
    else if (field === "folder_color") colors(section);
    else if (field === "project_name") texts(section, field, "Name for " + project().name, "Shown wherever the project is listed.", "project name");
    else if (field === "chat_title") texts(section, field, "Title for " + (state && state.chat && !state.chat.current ? "\u201c" + (state.chat.title || "that chat") + "\u201d" : "this chat"),
      state && state.chat && state.chat.title ? "Now \u201c" + state.chat.title + "\u201d." : "", "chat title");
    root.appendChild(section);
    note(field);
    refocus(focused);
  }

  // A redraw keeps keyboard focus on the same control.
  function refocus(label) {
    if (!label) return;
    var found = Array.prototype.find.call(root.querySelectorAll("[aria-label]"), function (node) {
      return node.getAttribute("aria-label") === label;
    });
    if (found) found.focus();
  }

  window.addEventListener("message", function (event) {
    var data = event.data;
    if (event.source !== P || !data || data.fuseArtifact !== 1 || data.type !== "choices-state") return;
    state = data.state && typeof data.state === "object" ? data.state : { unavailable: true };
    pending = "";
    draw();
  });
  draw();
  if (P) P.postMessage({ fuseArtifact: 1, type: "choices-hello" }, "*");
  setTimeout(function () { waited = true; if (!state) draw(); }, 2500);
})();
