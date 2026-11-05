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
      filter.Q.value = o.q || 1.2;
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
    envelope(gain, t, dur, o.g || 0.05, o.a);
    src.start(t);
    src.stop(t + dur + 0.02);
  }

  function envelope(gain, t, dur, peak, attack) {
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(peak, t + (attack || 0.004));
    gain.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  }

  // Soft mallet: the note, a fast-fading 4th partial (marimba-like) and a 4 ms strike.
  function pluck(t, freq, dur, g) {
    tone(t, freq, dur, { g, a: 0.002 });
    tone(t, freq * 4, dur * 0.25, { g: g * 0.12, a: 0.001 });
    noise(t, 0.004, { bp: [freq * 3, freq * 3], g: g * 0.3, a: 0.001 });
  }

  // Muted tap: a quick flick down onto the note through a low-pass, plus a strike.
  function tap(t, freq, g, dur = 0.05) {
    tone(t, freq * 1.5, dur, { type: "triangle", to: freq, lp: freq * 4, g, a: 0.001 });
    noise(t, 0.006, { bp: [freq * 4, freq * 2], g: g * 0.3, a: 0.001 });
  }

  // Muted and a little detuned: the wobble reads as "off" without a buzz.
  function dull(t, freq, dur, g) {
    tone(t, freq, dur, { type: "triangle", lp: freq * 6, g, a: 0.003 });
    tone(t, freq, dur, { type: "triangle", lp: freq * 6, g: g * 0.5, a: 0.003, det: 30 });
  }

  // A knuckle on wood: fast pitch drop, a hollow body and a short strike.
  function knock(t, f = 1) {
    tone(t, 1250 * f, 0.03, { to: 700 * f, g: 0.07, a: 0.001 });
    tone(t, 330 * f, 0.05, { type: "triangle", lp: 1400 * f, g: 0.06, a: 0.001 });
    noise(t, 0.008, { bp: [2400 * f, 1200 * f], g: 0.04, a: 0.001 });
  }

  // name: [tier, priority, vibration, synth]. "key" plays in Key moments and
  // All; "all" only in All. A higher priority swallows a lower one within 300 ms.
  // Every pitch is from E major pentatonic, so the cues sound like one family;
  // failures step a half-step off it. Nothing leans on lows a phone speaker drops.
  const SOUNDS = {
    send: ["key", 1, 0, (t) => { tone(t, 494, 0.04, { to: 1319, g: 0.08, a: 0.001 }); noise(t, 0.005, { hp: 3000, g: 0.02, a: 0.001 }); }],
    queue: ["key", 1, 0, (t) => { tap(t, 1109, 0.05); tap(t + 0.06, 831, 0.04); }],
    stop: ["key", 2, 12, (t) => { tap(t, 415, 0.14, 0.07); noise(t, 0.02, { lp: 900, g: 0.03 }); }],
    stopAll: ["key", 2, [12, 70, 12], (t) => { tap(t, 415, 0.12, 0.07); tap(t + 0.08, 330, 0.1, 0.08); }],
    done: ["key", 3, 0, (t) => { pluck(t, 494, 0.2, 0.09); pluck(t + 0.075, 659, 0.34, 0.085); }],
    doneSoft: ["key", 3, 0, (t) => { pluck(t, 494, 0.14, 0.045); pluck(t + 0.065, 659, 0.22, 0.04); }],
    error: ["key", 5, 20, (t) => { dull(t, 349, 0.1, 0.12); dull(t + 0.1, 330, 0.22, 0.11); }],
    errorSoft: ["key", 5, 0, (t) => dull(t, 349, 0.16, 0.05)],
    agentDone: ["key", 2, 0, (t) => pluck(t, 1661, 0.1, 0.085)],
    agentFail: ["key", 2, 0, (t) => dull(t, 415, 0.14, 0.055)],
    permission: ["key", 4, [10, 90, 10], (t) => { knock(t); knock(t + 0.11); }],
    permWarn: ["key", 4, 10, (t) => knock(t, 1.25)],
    copy: ["key", 1, 0, (t) => { noise(t, 0.006, { hp: 4500, g: 0.04, a: 0.001 }); pluck(t, 2637, 0.03, 0.012); }],
    micOn: ["key", 1, 0, (t) => { pluck(t, 1109, 0.04, 0.04); pluck(t + 0.04, 1319, 0.06, 0.04); }],
    micOff: ["key", 1, 0, (t) => { pluck(t, 1319, 0.04, 0.04); pluck(t + 0.04, 1109, 0.06, 0.04); }],
    delete: ["key", 1, 0, (t) => { noise(t, 0.1, { bp: [2600, 380], q: 1, g: 0.06, a: 0.01 }); tap(t + 0.055, 330, 0.05, 0.05); }],
    artifact: ["all", 1, 0, (t) => [1319, 1661, 1976].forEach((f, i) => pluck(t + i * 0.045, f, i === 2 ? 0.2 : 0.07, 0.03))],
    saved: ["all", 1, 0, (t) => pluck(t, 988, 0.08, 0.06)],
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
