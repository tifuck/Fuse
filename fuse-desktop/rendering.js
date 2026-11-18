// GPU or software rendering, from <userData>/rendering.json: {gpu, fellBack}.
// No Electron here, so tests/js/desktop.test.cjs runs it under node.

// This many GPU process crashes within GPU_CRASH_WINDOW_MS (Chromium restarts
// the process each time) mean the driver cannot carry it. Isolated ones, such
// as a resume from suspend, do not count.
const GPU_CRASH_LIMIT = 3;
const GPU_CRASH_WINDOW_MS = 2 * 60 * 1000;

// Anything unreadable is the default: the GPU, no notice.
function parseRendering(text) {
  try {
    const saved = JSON.parse(text);
    return { gpu: saved.gpu !== false, fellBack: saved.fellBack === true };
  } catch {
    return { gpu: true, fellBack: false };
  }
}

// FUSE_DISABLE_GPU=1 or Chromium's own --disable-gpu, for one launch.
const gpuForcedOff = (env, hasSwitch) => env.FUSE_DISABLE_GPU === "1" || hasSwitch("disable-gpu");

// The crash times still in the window, and whether this crash turns the GPU
// off. Only a GPU process that did not exit cleanly counts.
function countGpuCrash(crashes, details, now) {
  if (details.type !== "GPU" || details.reason === "clean-exit") return { crashes, fallBack: false };
  const recent = crashes.filter((at) => now - at < GPU_CRASH_WINDOW_MS).concat(now);
  return { crashes: recent, fallBack: recent.length >= GPU_CRASH_LIMIT };
}

// What a fresh launch says: the fallback notice once, then the flag is gone.
function launchRendering(state) {
  return { state: { ...state, fellBack: false }, notice: state.fellBack && !state.gpu };
}

module.exports = { GPU_CRASH_LIMIT, GPU_CRASH_WINDOW_MS, countGpuCrash, gpuForcedOff, launchRendering, parseRendering };
