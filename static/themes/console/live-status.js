// live-status presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function formatActivityClock(secondsF) {
    const total = Math.max(1, Math.round(secondsF));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h) return `${h}h ${m}m ${s}s`;
    if (m) return `${m}m ${s}s`;
    return `${s}s`;
  }

function formatActivityElapsed(secondsF, live) {
    if (secondsF == null || (live && secondsF < 1)) return "";
    return ` for ${formatActivityClock(secondsF)}`;
  }

function ccSpinnerVerb(msg) {
    const key = String((msg && msg.id) || "");
    let hash = 0;
    for (let i = 0; i < key.length; i += 1) hash = (Math.imul(hash, 31) + key.charCodeAt(i)) | 0;
    return CC_SPINNER_VERBS[(hash >>> 0) % CC_SPINNER_VERBS.length];
  }

function ccThinkingState(msg, thinking) {
    const now = Date.now();
    const clock = (msg._spinThink ||= { since: 0, flash: null });
    if (thinking) {
      if (!clock.since) {
        // The thought's own duration, streamed on its segment. Anchoring at
        // page paint restarted "still thinking" on every reload.
        const segs = Array.isArray(msg.segments) ? msg.segments : [];
        const last = [...segs].reverse().find((seg) => seg && seg.type === "reasoning");
        const ms = Number(last && last.ms);
        clock.since = Number.isFinite(ms) && ms > 0 ? now - ms : now;
      }
      clock.flash = null;
      const ms = now - clock.since;
      return ms >= 45000 ? "deep in thought" : ms >= 30000 ? "thinking some more" : ms >= 20000 ? "thinking more"
        : ms >= 10000 ? "still thinking" : "thinking";
    }
    if (clock.since) {
      clock.flash = { ms: now - clock.since, until: now + 2000 };
      clock.since = 0;
    }
    if (clock.flash && now < clock.flash.until) return `thought for ${Math.max(1, Math.round(clock.flash.ms / 1000))}s`;
    clock.flash = null;
    return "";
  }

function ccSpinnerState(status) {
    const extra = String(status.label || "").split(" · ").slice(1);
    let state = "";
    if (status.phase === "queued") state = "queued";
    else if (status.phase === "stopping") state = "stopping";
    else if (status.current) state = deps.executionState(status.current) === "cancelling" ? "cancelling"
      : deps.isSearchTool(status.current.name) ? "searching the web" : CC_SPINNER_STATES[status.current.name] || "running tool";
    else if (status.phase === "calling") state = !status.tool ? "calling tool"
      : deps.isSearchTool(status.tool) ? "searching the web" : CC_SPINNER_STATES[status.tool] || `calling ${status.tool}`;
    else if (status.phase === "waiting" || status.phase === "starting") state = "waiting";
    else if (status.phase === "working") state = "working";
    return [state, ...extra].filter(Boolean).join(" · ");
  }

function formatConsoleLiveStatus(msg, groups, opts) {
    const status = deps.currentActivity(msg, groups, true);
    const elapsed = opts && opts.seconds != null ? opts.seconds : deps.liveElapsedSeconds(msg, true);
    const seconds = Math.max(0, Math.floor(elapsed || 0));
    // The turn's average over its finished calls; before the first one is
    // measured, an estimate from the call still streaming.
    const usage = (msg && msg.usage) || {};
    const speed = deps.ccGenSpeed(usage.gen_out, usage.gen_ms, usage.gen_est > 0)
      || deps.ccGenSpeed(msg && msg.streamOut, msg && msg.streamMs, true);
    const thought = ccThinkingState(msg, status.phase === "thinking");
    // A tool call being written or run, a stop request or a requeue replaces
    // the "thought for Ns" flash at once: that is the newer activity.
    const state = status.phase === "thinking" ? thought
      : ["calling", "stopping", "queued"].includes(status.phase) || status.current ? ccSpinnerState(status) : thought || ccSpinnerState(status);
    // Claude Code shows the clock once there is something else to show, or
    // after 16s.
    const clock = speed || state || seconds > 16 ? deps.ccDuration(seconds * 1000) : "";
    const parts = [clock, speed && speed.text, state].filter(Boolean);
    const verb = ccSpinnerVerb(msg);
    return { verb: /(…|\.\.\.)$/.test(verb) ? verb : `${verb}…`, meta: parts.length ? `(${parts.join(" · ")})` : "" };
  }

function syncActivityElapsed(article, msg, live) {
    const elapsedEls = article.querySelectorAll("[data-activity-elapsed]");
    if (!elapsedEls.length) return;
    const text = formatActivityElapsed(deps.liveElapsedSeconds(msg, live), live);
    elapsedEls.forEach((el) => {
      if (el.closest("[data-live-status]")) {
        if (el.textContent !== text) el.textContent = text;
        return;
      }
      const next = el.closest("[data-activity]")?.classList.contains("is-waiting") ? "" : text;
      if (el.textContent !== next) el.textContent = next;
    });
  }

function activitySpinSet() {
    return deps.currentRunSpin();
  }

function syncActivitySpin(article, msg, live) {
    const els = article.querySelectorAll("[data-activity-spin]");
    if (!els.length) return;
    if (!live) {
      els.forEach((el) => {
        el.replaceChildren();
        delete el.dataset.spinSet;
      });
      return;
    }
    const el = article.querySelector("[data-live-status] [data-activity-spin]") || [...els].find((node) => node.closest(".chat-activity.is-live, .chat-activity.is-waiting"));
    if (!el) return;
    deps.applySpinEl(el, activitySpinSet(msg), "live", true, msg._spinOrigin);
  }

const CC_SPINNER_VERBS = [
    "Accomplishing", "Actioning", "Actualizing", "Architecting", "Baking", "Beaming", "Beboppin'", "Befuddling",
    "Billowing", "Blanching", "Bloviating", "Boogieing", "Boondoggling", "Booping", "Bootstrapping", "Brewing", "Bunning",
    "Burrowing", "Calculating", "Canoodling", "Caramelizing", "Cascading", "Catapulting", "Cerebrating", "Channeling",
    "Choreographing", "Churning", "Clauding", "Coalescing", "Cogitating", "Combobulating", "Composing", "Computing",
    "Concocting", "Considering", "Contemplating", "Cooking", "Crafting", "Creating", "Crunching", "Crystallizing",
    "Cultivating", "Deciphering", "Deliberating", "Determining", "Dilly-dallying", "Discombobulating", "Doing", "Doodling",
    "Drizzling", "Ebbing", "Effecting", "Elucidating", "Embellishing", "Enchanting", "Envisioning", "Fermenting",
    "Fiddle-faddling", "Finagling", "Flambéing", "Flibbertigibbeting", "Flowing", "Flummoxing", "Fluttering", "Forging",
    "Forming", "Frolicking", "Frosting", "Gallivanting", "Galloping", "Garnishing", "Generating", "Gesticulating",
    "Germinating", "Gitifying", "Grooving", "Gusting", "Harmonizing", "Hashing", "Hatching", "Herding", "Honking",
    "Hullaballooing", "Hyperspacing", "Ideating", "Imagining", "Improvising", "Incubating", "Inferring", "Infusing",
    "Ionizing", "Jitterbugging", "Julienning", "Kerfuffling", "Kneading", "Leavening", "Levitating", "Lollygagging",
    "Manifesting", "Marinating", "Meandering", "Metamorphosing", "Misting", "Moonwalking", "Moseying", "Mulling",
    "Mustering", "Musing", "Nebulizing", "Nesting", "Newspapering", "Noodling", "Nucleating", "Onioning", "Orbiting",
    "Orchestrating", "Osmosing", "Perambulating", "Percolating", "Perusing", "Philosophizing", "Photosynthesizing",
    "Polishing", "Pollinating", "Pondering", "Pontificating", "Pouncing", "Precipitating", "Prestidigitating",
    "Processing", "Proofing", "Propagating", "Puttering", "Puzzling", "Quantumizing", "Razzle-dazzling", "Razzmatazzing",
    "Recombobulating", "Reticulating", "Roosting", "Ruminating", "Sautéing", "Scampering", "Schlepping", "Scurrying",
    "Seasoning", "Shenaniganing", "Shimmying", "Simmering", "Skedaddling", "Sketching", "Slithering", "Smooshing",
    "Sock-hopping", "Spelunking", "Spinning", "Sprouting", "Stewing", "Sublimating", "Swirling", "Swooping", "Symbioting",
    "Synthesizing", "Tempering", "Thinking", "Thundering", "Tinkering", "Tomfoolering", "Topsy-turvying", "Transfiguring",
    "Transmogrifying", "Transmuting", "Twisting", "Undulating", "Unfurling", "Unraveling", "Vibing", "Waddling",
    "Wandering", "Warping", "Whatchamacalliting", "Whirlpooling", "Whirring", "Whisking", "Wibbling", "Working",
    "Wrangling", "Zesting", "Zigzagging",
  ];

const CC_SPINNER_STATES = {
    run_terminal_command: "running command", get_command_output: "waiting for command", kill_command: "stopping command",
    read_file: "reading", grep: "searching", glob: "searching", list_dir: "listing", search_replace: "editing",
    write_file: "writing file", read_url: "fetching", spawn_subagent: "running agent", get_subagent_output: "waiting for agent",
    kill_subagent: "stopping agent", list_subagents: "listing agents", monitor: "watching",
    create_artifact: "building artifact", update_artifact: "building artifact", edit_artifact: "editing artifact",
    read_artifact: "reading artifact", search_models: "finding models",
  };

  return { formatActivityClock, formatActivityElapsed, ccSpinnerVerb, ccThinkingState, ccSpinnerState, formatConsoleLiveStatus, syncActivityElapsed, activitySpinSet, syncActivitySpin, CC_SPINNER_VERBS, CC_SPINNER_STATES };
}
