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

  // Two sines a few cents apart: the slow beat reads as "wrong" without a buzz.
  function sour(t, freq, dur, g, o = {}) {
    tone(t, freq, dur, { ...o, g, a: 0.006 });
    tone(t, freq, dur, { ...o, g: g * 0.5, a: 0.006, det: 14 });
  }

  function knock(t) {
    tone(t, 740, 0.04, { type: "triangle", g: 0.1, lp: 3000, a: 0.002 });
    tone(t, 185, 0.05, { g: 0.07, a: 0.002 });
  }

  function thock(t, from) {
    tone(t, from, 0.1, { to: from / 2, g: 0.2, a: 0.003 });
    noise(t, 0.02, { lp: 500, g: 0.04 });
  }

  // name: [tier, priority, vibration, synth]. "key" plays in Key moments and
  // All; "all" only in All. A higher priority swallows a lower one within 300 ms.
  // Every pitch is from E major pentatonic, so the cues sound like one family.
  const SOUNDS = {
    send: ["key", 1, 0, (t) => { tone(t, 659, 0.075, { to: 1319, g: 0.08, a: 0.005 }); bell(t + 0.055, 1319, 0.09, 0.035); }],
    queue: ["key", 1, 0, (t) => { bell(t, 988, 0.07, 0.05); bell(t + 0.06, 740, 0.1, 0.045); }],
    stop: ["key", 2, 12, (t) => thock(t, 196)],
    stopAll: ["key", 2, [12, 70, 12], (t) => { thock(t, 196); thock(t + 0.09, 165); }],
    done: ["key", 3, 0, (t) => { bell(t, 659, 0.2, 0.1); bell(t + 0.085, 988, 0.38, 0.09); }],
    doneSoft: ["key", 3, 0, (t) => { bell(t, 659, 0.14, 0.045); bell(t + 0.08, 988, 0.24, 0.04); }],
    error: ["key", 5, 20, (t) => { sour(t, 494, 0.12, 0.08); sour(t + 0.11, 392, 0.26, 0.08); }],
    errorSoft: ["key", 5, 0, (t) => sour(t, 392, 0.16, 0.05)],
    agentDone: ["key", 2, 0, (t) => { bell(t, 1319, 0.07, 0.045); bell(t + 0.045, 1661, 0.12, 0.045); }],
    agentFail: ["key", 2, 0, (t) => sour(t, 415, 0.16, 0.06, { to: 330 })],
    permission: ["key", 4, [10, 90, 10], (t) => { knock(t); knock(t + 0.12); }],
    permWarn: ["key", 4, 10, (t) => knock(t)],
    copy: ["key", 1, 0, (t) => { noise(t, 0.01, { hp: 5000, g: 0.035 }); bell(t, 1976, 0.03, 0.025); }],
    micOn: ["key", 1, 0, (t) => tone(t, 659, 0.08, { to: 988, g: 0.08, a: 0.005 })],
    micOff: ["key", 1, 0, (t) => tone(t, 988, 0.08, { to: 659, g: 0.08, a: 0.005 })],
    delete: ["key", 1, 0, (t) => { noise(t, 0.11, { bp: [1400, 300], g: 0.06 }); tone(t, 165, 0.1, { to: 110, g: 0.09, a: 0.003 }); }],
    artifact: ["all", 1, 0, (t) => [1319, 1661, 1976].forEach((f, i) => bell(t + i * 0.04, f, 0.08, 0.035))],
    saved: ["all", 1, 0, (t) => bell(t, 988, 0.09, 0.055)],
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
      // A gentle top-end roll-off and a soft compressor keep every cue round
      // and stop overlapping ones from peaking.
      const air = ctx.createBiquadFilter();
      air.type = "lowpass";
      air.frequency.value = 8000;
      const glue = ctx.createDynamicsCompressor();
      glue.threshold.value = -18;
      glue.knee.value = 12;
      glue.ratio.value = 3;
      master.connect(air);
      air.connect(glue);
      glue.connect(ctx.destination);
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
