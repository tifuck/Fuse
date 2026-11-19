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

  let dry = null;
  let room = null;

  // One voice: soft linear attack, smooth exponential tail, optional pitch
  // glide, low-pass, pan and a send into the short room.
  function tone(t, freq, dur, peak, o = {}) {
    const osc = ctx.createOscillator();
    osc.type = o.type || "sine";
    if (o.from) {
      osc.frequency.setValueAtTime(o.from, t);
      osc.frequency.exponentialRampToValueAtTime(freq, t + (o.glide || dur * 0.6));
    } else {
      osc.frequency.setValueAtTime(freq, t);
    }
    let node = osc;
    if (o.lp) {
      const filter = ctx.createBiquadFilter();
      filter.type = "lowpass";
      filter.frequency.value = o.lp;
      filter.Q.value = 0.4;
      osc.connect(filter);
      node = filter;
    }
    const attack = o.a ?? 0.004;
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0, t);
    gain.gain.linearRampToValueAtTime(peak, t + attack);
    gain.gain.setTargetAtTime(0, t + attack, Math.max(0.004, dur / 4.5));
    node.connect(gain);
    let out = gain;
    if (o.pan && ctx.createStereoPanner) {
      const pan = ctx.createStereoPanner();
      pan.pan.value = o.pan;
      gain.connect(pan);
      out = pan;
    }
    out.connect(dry);
    if (o.send) {
      const send = ctx.createGain();
      send.gain.value = o.send;
      out.connect(send);
      send.connect(room);
    }
    osc.start(t);
    osc.stop(t + attack + dur + 0.15);
  }

  // Muffled: a low-passed triangle over a quieter sine.
  function felt(t, freq, dur, peak) {
    tone(t, freq, dur, peak, { type: "triangle", lp: freq * 1.8, a: 0.008 });
    tone(t, freq, dur * 0.8, peak * 0.5, { a: 0.01 });
  }

  // Bright: the note and a faint, fast-fading third partial.
  function glint(t, freq, dur, peak) {
    tone(t, freq, dur, peak, { a: 0.002, send: 0.08 });
    tone(t, freq * 3.01, dur * 0.3, peak * 0.12, { a: 0.001 });
  }

  // A short, dark stereo room for the few cues that bloom.
  function impulse(sec) {
    const length = Math.floor(ctx.sampleRate * sec);
    const pre = Math.floor(ctx.sampleRate * 0.012);
    const buffer = ctx.createBuffer(2, length, ctx.sampleRate);
    for (let ch = 0; ch < 2; ch += 1) {
      const data = buffer.getChannelData(ch);
      let lp = 0;
      for (let i = pre; i < length; i += 1) {
        lp += 0.35 * ((Math.random() * 2 - 1) * Math.pow(1 - (i - pre) / (length - pre), 3.2) - lp);
        data[i] = lp;
      }
    }
    return buffer;
  }

  const N = { C4: 261.63, E4: 329.63, G4: 392, A4: 440, B4: 493.88, D5: 587.33, E5: 659.26, F5: 698.46, G5: 783.99, A5: 880, B5: 987.77, C6: 1046.5, D6: 1174.66, E6: 1318.51, G6: 1567.98, C7: 2093 };
  const FLAT = 0.944; // a little under a half-step: "off", not a melody

  // name: [tier, priority, vibration, synth]. "key" plays in Key moments and
  // All; "all" only in All. A higher priority swallows a lower one within 300 ms.
  // Short, quiet and mostly dry. Rising means start or success, falling means
  // stop or failure, a flattened note means wrong, a repeat asks for attention.
  // Routine acks (copy, delete, save, a subagent finishing) stay silent.
  const SOUNDS = {
    send: ["key", 1, 0, (t) => tone(t, 640, 0.06, 0.1, { from: 420, glide: 0.04 })],
    queue: ["key", 1, 0, (t) => tone(t, N.D5, 0.09, 0.08, { from: N.F5, glide: 0.05 })],
    stop: ["key", 2, 12, (t) => tone(t, N.E4, 0.08, 0.11, { from: N.A4, glide: 0.05 })],
    stopAll: ["key", 2, [12, 70, 12], (t) => { felt(t, N.G4, 0.06, 0.15); felt(t + 0.09, N.E4, 0.08, 0.15); }],
    done: ["key", 3, 0, (t) => { tone(t, N.G5, 0.12, 0.08, { a: 0.003 }); tone(t + 0.07, N.C6, 0.22, 0.08, { a: 0.003, send: 0.1 }); }],
    doneSoft: ["key", 3, 0, (t) => { tone(t, N.G5, 0.08, 0.04); tone(t + 0.06, N.C6, 0.14, 0.04); }],
    error: ["key", 5, 20, (t) => { felt(t, N.B4, 0.07, 0.12); felt(t + 0.09, N.B4 * FLAT, 0.16, 0.12); }],
    errorSoft: ["key", 5, 0, (t) => felt(t, N.B4 * FLAT, 0.12, 0.09)],
    agentFail: ["key", 2, 0, (t) => { tone(t, N.B5, 0.025, 0.05, { a: 0.001 }); tone(t + 0.035, N.B5 * FLAT, 0.04, 0.05, { a: 0.001 }); }],
    permission: ["key", 4, [10, 90, 10], (t) => { glint(t, N.D6, 0.12, 0.07); glint(t + 0.14, N.D6, 0.18, 0.07); }],
    permWarn: ["key", 4, 10, (t) => [0, 0.09, 0.18, 0.27].forEach((d) => tone(t + d, N.D6, 0.03, 0.05, { a: 0.002 }))],
    micOn: ["key", 1, 0, (t) => tone(t, N.A5, 0.18, 0.05, { a: 0.07, send: 0.15 })],
    micOff: ["key", 1, 0, (t) => tone(t, N.D5, 0.12, 0.05, { a: 0.005, send: 0.15 })],
    artifact: ["all", 1, 0, (t) => {
      for (let i = 0; i < 6; i += 1) tone(t + i * 0.03, [N.G6, N.C7, N.E6][i % 3], 0.05, 0.025, { a: 0.002, pan: Math.random() - 0.5, send: 0.2 });
    }],
  };
  // Bursts merge: five subagents finishing together are one sound.
  const GAP_MS = { agentFail: 400 };

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
      // A soft compressor stops overlapping cues from peaking.
      const glue = ctx.createDynamicsCompressor();
      glue.threshold.value = -10;
      glue.knee.value = 8;
      glue.ratio.value = 3;
      glue.attack.value = 0.003;
      glue.release.value = 0.15;
      const trim = ctx.createGain();
      trim.gain.value = 0.7;
      glue.connect(trim);
      trim.connect(master);
      dry = ctx.createGain();
      dry.connect(glue);
      room = ctx.createConvolver();
      room.buffer = impulse(0.9);
      room.connect(glue);
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
