// Short interface sounds, synthesized with Web Audio (no audio files).
// window.fuseSound.play(name, { event }) is the whole surface. Callers hook
// the action, not the button, so an action sounds the same from any control.
// Events (a reply finishing, a permission ask) pass { event: true }: they play
// only in the focused, visible window. Web Push covers hidden ones, so a sound
// never doubles a notification and two windows never both play.
(function () {
  const STORE = "fuse.sounds.v1";
  const DEFAULTS = { mode: "key", volume: 0.7 };
  let ctx = null;
  let master = null;

  function tone(t, freq, dur, o = {}) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = o.type || "sine";
    osc.frequency.setValueAtTime(freq, t);
    if (o.to) osc.frequency.exponentialRampToValueAtTime(o.to, t + dur);
    if (o.det) osc.detune.value = o.det;
    let node = osc;
    if (o.lp) {
      const filter = ctx.createBiquadFilter();
      filter.type = "lowpass";
      filter.frequency.value = o.lp;
      osc.connect(filter);
      node = filter;
    }
    node.connect(gain);
    gain.connect(master);
    envelope(gain, t, dur, o.g || 0.1, o.a);
    osc.start(t);
    osc.stop(t + dur + 0.02);
  }

  function noise(t, dur, o = {}) {
    const length = Math.ceil(ctx.sampleRate * dur);
    const buffer = ctx.createBuffer(1, length, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < length; i += 1) data[i] = Math.random() * 2 - 1;
    const src = ctx.createBufferSource();
    src.buffer = buffer;
    const filter = ctx.createBiquadFilter();
    if (o.hp) {
      filter.type = "highpass";
      filter.frequency.value = o.hp;
    } else if (o.bp) {
      filter.type = "bandpass";
      filter.Q.value = 1.2;
      filter.frequency.setValueAtTime(o.bp[0], t);
      filter.frequency.exponentialRampToValueAtTime(o.bp[1], t + dur);
    } else {
      filter.type = "lowpass";
      filter.frequency.value = o.lp || 1000;
    }
    const gain = ctx.createGain();
    src.connect(filter);
    filter.connect(gain);
    gain.connect(master);
    envelope(gain, t, dur, o.g || 0.05);
    src.start(t);
    src.stop(t + dur + 0.02);
  }

  function envelope(gain, t, dur, peak, attack) {
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(peak, t + (attack || 0.004));
    gain.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  }

  // A soft bell: the note plus a faint octave and twelfth, so chimes are not bare sines.
  function bell(t, freq, dur, g) {
    tone(t, freq, dur, { g, a: 0.006 });
    tone(t, freq * 2, dur * 0.7, { g: g * 0.22, a: 0.004 });
    tone(t, freq * 3, dur * 0.4, { g: g * 0.08, a: 0.003 });
  }

  function knock(t) {
    tone(t, 784, 0.045, { type: "triangle", g: 0.14 });
    tone(t, 196, 0.05, { g: 0.08 });
    noise(t, 0.015, { lp: 2500, g: 0.05 });
  }

  function stopThud(t) {
    tone(t, 230, 0.09, { to: 105, g: 0.24 });
    noise(t, 0.03, { lp: 700, g: 0.08 });
  }

  // name: [tier, priority, vibration, synth]. "key" plays in Key moments and
  // All; "all" only in All. A higher priority swallows a lower one within 300 ms.
  const SOUNDS = {
    send: ["key", 1, 0, (t) => { tone(t, 520, 0.09, { to: 1040, g: 0.14 }); tone(t, 260, 0.08, { to: 520, g: 0.05 }); noise(t, 0.018, { hp: 3000, g: 0.03 }); }],
    queue: ["key", 1, 0, (t) => { tone(t, 660, 0.045, { type: "triangle", g: 0.1 }); tone(t + 0.05, 495, 0.06, { type: "triangle", g: 0.09 }); }],
    stop: ["key", 2, 12, (t) => stopThud(t)],
    stopAll: ["key", 2, [12, 70, 12], (t) => { stopThud(t); stopThud(t + 0.1); }],
    done: ["key", 3, 0, (t) => { bell(t, 659, 0.18, 0.12); bell(t + 0.09, 988, 0.3, 0.11); }],
    doneSoft: ["key", 3, 0, (t) => { bell(t, 659, 0.14, 0.05); bell(t + 0.08, 988, 0.22, 0.05); }],
    error: ["key", 5, 20, (t) => { tone(t, 440, 0.13, { type: "triangle", g: 0.11 }); tone(t + 0.11, 349, 0.22, { type: "triangle", g: 0.11, det: 9 }); tone(t + 0.11, 175, 0.2, { g: 0.05 }); }],
    errorSoft: ["key", 5, 0, (t) => tone(t, 349, 0.12, { type: "triangle", g: 0.08, det: 7 })],
    agentDone: ["key", 2, 0, (t) => { bell(t, 1175, 0.08, 0.06); bell(t + 0.05, 1568, 0.12, 0.06); }],
    agentFail: ["key", 2, 0, (t) => tone(t, 392, 0.15, { to: 311, type: "triangle", g: 0.09 })],
    permission: ["key", 4, [10, 90, 10], (t) => { knock(t); knock(t + 0.12); }],
    permWarn: ["key", 4, 10, (t) => knock(t)],
    allow: ["key", 1, 0, (t) => tone(t, 700, 0.06, { to: 940, g: 0.1 })],
    deny: ["key", 1, 0, (t) => tone(t, 520, 0.07, { to: 340, g: 0.1 })],
    copy: ["key", 1, 0, (t) => { noise(t, 0.012, { hp: 4500, g: 0.05 }); tone(t, 2100, 0.022, { g: 0.035 }); }],
    attach: ["key", 1, 0, (t) => { tone(t, 1250, 0.025, { type: "triangle", g: 0.07 }); tone(t + 0.035, 1650, 0.03, { type: "triangle", g: 0.06 }); }],
    micOn: ["key", 1, 0, (t) => tone(t, 587, 0.09, { to: 880, g: 0.1 })],
    micOff: ["key", 1, 0, (t) => tone(t, 880, 0.09, { to: 587, g: 0.1 })],
    newChat: ["key", 1, 0, (t) => { tone(t, 300, 0.07, { to: 620, g: 0.11 }); noise(t, 0.02, { hp: 2500, g: 0.02 }); }],
    undo: ["key", 1, 0, (t) => tone(t, 880, 0.11, { to: 470, g: 0.08 })],
    redo: ["key", 1, 0, (t) => tone(t, 470, 0.11, { to: 880, g: 0.08 })],
    rewind: ["key", 1, 0, (t) => tone(t, 620, 0.19, { to: 190, type: "sawtooth", g: 0.035, lp: 1400 })],
    archive: ["key", 1, 0, (t) => noise(t, 0.15, { bp: [1900, 500], g: 0.07 })],
    delete: ["key", 1, 0, (t) => { noise(t, 0.12, { lp: 900, g: 0.1 }); tone(t, 165, 0.09, { g: 0.12 }); }],
    compacted: ["key", 1, 0, (t) => [880, 659, 523].forEach((f, i) => bell(t + i * 0.06, f, 0.1, 0.05))],
    offline: ["key", 1, 0, (t) => tone(t, 330, 0.16, { g: 0.08, type: "triangle" })],
    online: ["key", 1, 0, (t) => tone(t, 440, 0.1, { to: 660, g: 0.07 })],
    artifact: ["all", 1, 0, (t) => [1047, 1319, 1568].forEach((f, i) => bell(t + i * 0.04, f, 0.08, 0.045))],
    saved: ["all", 1, 0, (t) => bell(t, 880, 0.08, 0.07)],
  };
  // Bursts merge: five subagents finishing together are one sound.
  const GAP_MS = { agentDone: 400, agentFail: 400 };

  const lastAt = new Map();
  let recent = [];
  let lastHigh = { at: 0, priority: 0 };

  function prefs() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORE) || "null") || {};
      return {
        mode: ["off", "key", "all"].includes(saved.mode) ? saved.mode : DEFAULTS.mode,
        volume: Number.isFinite(saved.volume) ? Math.min(1, Math.max(0, saved.volume)) : DEFAULTS.volume,
      };
    } catch (_) {
      return { ...DEFAULTS };
    }
  }

  function setPrefs(next) {
    const value = { ...prefs(), ...next };
    try { localStorage.setItem(STORE, JSON.stringify(value)); } catch (_) {}
    return value;
  }

  function audio() {
    if (!ctx) {
      const Ctor = window.AudioContext || window.webkitAudioContext;
      if (!Ctor) return null;
      ctx = new Ctor();
      master = ctx.createGain();
      master.connect(ctx.destination);
    }
    if (ctx.state !== "running") ctx.resume().catch(() => {});
    return ctx;
  }

  function play(name, opts) {
    const sound = SOUNDS[name];
    const p = prefs();
    if (!sound || p.mode === "off" || (sound[0] === "all" && p.mode !== "all") || p.volume <= 0) return false;
    if (opts && opts.event && (document.visibilityState !== "visible" || !document.hasFocus())) return false;
    const now = Date.now();
    if (now - (lastAt.get(name) || 0) < (GAP_MS[name] || 120)) return false;
    if (now - lastHigh.at < 300 && sound[1] < lastHigh.priority) return false;
    recent = recent.filter((at) => now - at < 1000);
    if (recent.length >= 4) return false;
    const c = audio();
    if (!c) return false;
    // Suspended until a tap or key (autoplay rules). Outside a gesture the
    // resume would wait for the next one and play this sound late.
    const running = c.state === "running";
    if (!running && !(navigator.userActivation && navigator.userActivation.isActive)) return false;
    lastAt.set(name, now);
    recent.push(now);
    lastHigh = { at: now, priority: sound[1] };
    master.gain.value = p.volume;
    const start = () => sound[3](c.currentTime + 0.01);
    if (running) start();
    else c.resume().then(start, () => {});
    if (sound[2] && navigator.vibrate) {
      try { navigator.vibrate(sound[2]); } catch (_) {}
    }
    return true;
  }

  function unlock() {
    if (prefs().mode !== "off") audio();
  }
  for (const type of ["pointerdown", "keydown", "touchend"]) {
    window.addEventListener(type, unlock, { capture: true, passive: true });
  }

  // Every copy on the site goes through the clipboard API: one hook covers them all.
  const clip = navigator.clipboard;
  if (clip && typeof clip.writeText === "function") {
    const write = clip.writeText.bind(clip);
    clip.writeText = (text) => write(text).then((value) => {
      play("copy");
      return value;
    });
  }

  // Settings › Appearance and the chat Appearance sheet (_appearance_body.html).
  function bindControls() {
    const paint = () => {
      const p = prefs();
      document.querySelectorAll("[data-sound-mode]").forEach((btn) => {
        btn.setAttribute("aria-pressed", String(btn.dataset.soundMode === p.mode));
      });
      document.querySelectorAll("[data-sound-volume]").forEach((input) => {
        input.value = String(Math.round(p.volume * 100));
        input.disabled = p.mode === "off";
      });
      document.querySelectorAll("[data-sound-volume-value]").forEach((out) => {
        out.textContent = Math.round(p.volume * 100) + "%";
      });
    };
    document.querySelectorAll("[data-sound-mode]").forEach((btn) => {
      btn.addEventListener("click", () => {
        setPrefs({ mode: btn.dataset.soundMode });
        paint();
        play("done");
      });
    });
    document.querySelectorAll("[data-sound-volume]").forEach((input) => {
      input.addEventListener("input", () => {
        setPrefs({ volume: Number(input.value) / 100 });
        paint();
      });
      input.addEventListener("change", () => play("send"));
    });
    paint();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bindControls);
  else bindControls();

  window.fuseSound = { play, prefs, setPrefs, SOUNDS };
})();
