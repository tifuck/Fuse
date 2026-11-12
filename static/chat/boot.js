// Startup sequence: viewport pinning, mobile home, initial thread/artifact
// open, and the view a reload comes back to.
import { S, draftKey } from './state.js';

const RESUME = "fuse.chat.resume.v1";

// What was on screen when this address was last left, if this load is a
// reload of it: the build watcher's (app.js buildWatch), the user's, the
// desktop shell's. Any other navigation starts fresh.
function takeResume() {
  try {
    const raw = sessionStorage.getItem(RESUME);
    sessionStorage.removeItem(RESUME);
    if (!raw || performance.getEntriesByType("navigation")[0]?.type !== "reload") return null;
    const saved = JSON.parse(raw);
    return saved && saved.url === location.pathname + location.search ? saved : null;
  } catch (_) {
    return null;
  }
}

export function createBoot(ctx) {
  const { agents, artifacts, composer, sidebar, sync, transcript } = ctx;
    if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", composer.pinVisualViewport);
    window.visualViewport.addEventListener("scroll", composer.pinVisualViewport);
  }

    window.addEventListener("resize", composer.pinVisualViewport);

    window.addEventListener("orientationchange", () => {
    composer.pinVisualViewport();
    window.setTimeout(composer.pinVisualViewport, 350);
  });

    const resume = takeResume();

    // Opened disclosures, the sidebar and the draft text already survive a
    // reload (sessionStorage, localStorage, the server); this is the rest.
    window.addEventListener("pagehide", () => {
    const agent = S.agentsOpen ? S.agentViewId : "";
    const project = !S.currentId && S.pendingProject;
    const saved = {
      url: location.pathname + location.search,
      list: S.root.classList.contains("is-history"),
      project: project ? [project.host_id, project.project_id] : null,
      // An open subagent takes the transcript's place: S.mainScroll is where
      // the reader was in it.
      view: agent ? null : transcript.captureThreadViewport(S.currentId),
      main: agent ? S.mainScroll : null,
      agent,
      agentTop: agent && !S.agentStick && S.agentLogEl ? S.agentLogEl.scrollTop : null,
      fold: S.agentsFold,
      artifact: (S.artifactOpen && S.currentArtifact?.identifier) || "",
      input: {
        key: draftKey(),
        text: S.input.value,
        range: [S.input.selectionStart, S.input.selectionEnd],
        focused: document.activeElement === S.input,
      },
    };
    try { sessionStorage.setItem(RESUME, JSON.stringify(saved)); } catch (_) {}
  });

    async function comeBack(saved) {
    if (saved.project && !S.currentId) await sidebar.startProjectThread(...saved.project);
    const { input, view } = saved;
    // Only the caret: the text is the synced draft's to bring back. The box
    // can hold text that is not a draft (an unconfirmed send's, which the
    // retry owns), and that must not be pasted back.
    if (input && input.focused && input.key === draftKey() && input.text === S.input.value) {
      S.input.focus({ preventScroll: true });
      S.input.setSelectionRange(...input.range);
    }
    if (view && view.threadId === S.currentId && !view.following) {
      // An unrendered shell is far shorter than its message: render the one
      // the reader was in before measuring from it.
      const anchor = view.anchorId && [...S.log.children].find((el) => el.dataset.messageId === view.anchorId);
      if (anchor?.hasAttribute("data-deferred-message")) transcript.hydrateHistoricalMessage(anchor);
      transcript.pauseThreadFollow();
      window.fuseHold(() => transcript.restoreThreadViewport(view), () => view.threadId === S.currentId);
    }
    if (saved.fold != null && S.currentId) agents.setAgentsFold(saved.fold);
    if (saved.agent && S.currentId) {
      await agents.showAgent(saved.agent);
      if (S.agentViewId === saved.agent) {
        if (saved.main) S.mainScroll = saved.main;
        if (saved.agentTop != null) {
          S.agentStick = false;
          S.agentResumeOnScroll = false;
          window.fuseHold(() => agents.restoreAgentScroll(false, saved.agentTop), () => S.agentViewId === saved.agent && !S.agentStick);
        }
      }
    }
    // Last: opening a thread or a project session closes the phone's list.
    if (saved.list && S.historyMobile.matches) sidebar.setHistoryOpen(true, { home: true });
  }

    // A phone opens on the chat list unless it links a chat; a reload opens on
    // whichever of the two it left.
    if (S.historyMobile.matches && (resume ? resume.list : !new URLSearchParams(window.location.search).get("t"))) {
    sidebar.setHistoryOpen(true, { home: true });
  }

    const initialNavigation = S.navigation;

    sync.connectSync(true)
    .then(() => {
      if (S.navigation !== initialNavigation) return;
      const params = new URLSearchParams(window.location.search);
      const wanted = (params.get("t") || "").trim();
      const art = resume ? resume.artifact : (params.get("a") || "").trim();
      const open = wanted ? sidebar.openThread(wanted) : Promise.resolve();
      return open.then(async () => {
        // The pane opens at once (its page loads after), so the reading
        // position below is measured against the final layout.
        const shown = art && artifacts.artifactsInThread().some((t) => t.identifier === art) ? artifacts.showArtifact(art) : null;
        if (!shown) artifacts.rememberLatestArtifact();
        // Not if the user already went somewhere else. A view that cannot be
        // restored is not a chat that failed to load.
        if (resume && (S.currentId || "") === wanted) await comeBack(resume).catch((error) => console.warn("Could not restore the view", error));
        return shown;
      });
    })
    .catch((error) => sync.showFailure("Chat history could not be loaded", error));

  return {

  };
}
