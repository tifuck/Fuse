// Choices artifacts (app/artifact_choices.js): options for the user's own
// Fuse settings, each option with its own Use this button. The page only
// says which option was clicked: the options themselves come from the server
// (GET /a/<id>/choices, checked when the model wrote them), Fuse's confirm
// dialog shows what will change, and the change goes through the same route
// the setting's own form uses. A page can never pick what gets changed.
import { S, newSession } from './state.js';

const FIELDS = new Set(["backdrop", "project_logo", "folder_color", "project_name", "chat_title"]);
const FAILED = {
  backdrop: "Could not save the backdrop",
  project_logo: "Could not change the logo",
  folder_color: "Could not change the folder color",
  project_name: "Could not rename the project",
  chat_title: "Could not rename the chat",
};
const ARROW = '<svg class="confirm-media-to" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M3 8h9M8.5 4.5 12 8l-3.5 3.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';

function svgUrl(svg) {
  return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}

// Fuse's confirm media: what the project shows now, an arrow, what it would show.
function swap(fromHtml, toHtml) {
  const holder = document.createElement("div");
  holder.innerHTML = fromHtml + ARROW + toHtml;
  const out = document.createDocumentFragment();
  out.append(...holder.childNodes);
  return out;
}

export function createChoices(ctx) {
  const { net, hint, showFailure, saveBackdrop, upsertLocal, acceptProjects } = ctx;
  const specs = new Map();
  // "id:field" -> the option applied from that artifact in this page.
  const applied = new Map();
  const logos = new Map();
  // Frames that said hello, so a change refreshes every card showing it.
  const frames = new Map();
  let busy = false;

  function load(id, version) {
    const key = `${id}:${version || ""}`;
    if (!specs.has(key)) {
      const query = version ? `?v=${encodeURIComponent(version)}` : "";
      const pending = net.api(`/a/${encodeURIComponent(id)}/choices${query}`);
      pending.catch(() => specs.delete(key));
      specs.set(key, pending);
    }
    return specs.get(key);
  }

  function projectOf(spec) {
    const ref = spec && spec.project;
    if (!ref) return null;
    return (S.projects || []).find((item) => Number(item.id) === Number(ref.id)) || null;
  }

  function threadOf(spec) {
    if (!spec || !spec.chat) return null;
    return S.threads.concat(S.archivedThreads || []).find((item) => item.id === spec.chat) || null;
  }

  // The card is an opaque page with no access to /projects: it gets the
  // current logo as a data: URL.
  function logoData(url) {
    if (!url) return Promise.resolve("");
    if (!logos.has(url)) {
      logos.set(url, fetch(url, { credentials: "same-origin" })
        .then((res) => (res.ok ? res.blob() : null))
        .then((blob) => blob && new Promise((resolve) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result || ""));
          reader.onerror = () => resolve("");
          reader.readAsDataURL(blob);
        }))
        .catch(() => ""));
    }
    return logos.get(url);
  }

  async function stateFor(id, spec) {
    const state = { applied: {} };
    FIELDS.forEach((field) => {
      const index = applied.get(`${id}:${field}`);
      if (index != null) state.applied[field] = index;
    });
    if (spec.project) {
      const project = projectOf(spec);
      state.project = project
        ? { name: project.name, color: window.FuseProjects?.colorFor(project) || "", logo: (await logoData(project.logo_url)) || "" }
        : { name: spec.project.name, missing: true };
    }
    if (spec.chat) {
      const thread = threadOf(spec);
      state.chat = thread ? { title: thread.title || "", current: thread.id === S.currentId } : { missing: true };
    }
    return state;
  }

  async function post(source, id, version) {
    try {
      const spec = await load(id, version);
      const state = await stateFor(id, spec);
      source.postMessage({ fuseArtifact: 1, type: "choices-state", state }, "*");
    } catch (_) {
      source.postMessage({ fuseArtifact: 1, type: "choices-state", state: { unavailable: true } }, "*");
    }
  }

  function refresh() {
    frames.forEach((info, source) => {
      if (source.closed) frames.delete(source);
      else post(source, info.id, info.version);
    });
  }

  function needProject(spec) {
    const project = projectOf(spec);
    if (!project) throw new Error("That project is no longer in your projects.");
    return project;
  }

  // The saved project, into the list and every row showing it at once;
  // projects.updated brings the same.
  function keep(project, saved) {
    if (!saved || Number(saved.id) !== Number(project.id)) return;
    Object.assign(project, { name: saved.name, color: saved.color, logo_url: saved.logo_url });
    acceptProjects(S.projects);
  }

  async function patchProject(project, body) {
    keep(project, await net.api(`/hosts/${project.host_id}/projects/${project.id}`, { method: "PATCH", body: JSON.stringify(body) }));
  }

  const apply = {
    async backdrop(spec, option) {
      return Boolean(await saveBackdrop({ name: option.name, scheme: option.scheme, html: option.html }));
    },
    async project_logo(spec, option) {
      const project = needProject(spec);
      const src = svgUrl(option.svg);
      const media = swap(window.FuseProjects.icon(project, { size: 48 }), `<img src="${src}" width="48" height="48" alt="">`);
      if (!(await window.fuseConfirm(`Use “${option.name}” as the logo for ${project.name}?`, { confirmLabel: "Use logo", media }))) return false;
      const data = await window.FuseProjects.rasterLogoUrl(src);
      keep(project, await net.api(`/hosts/${project.host_id}/projects/${project.id}/logo`, { method: "PUT", body: JSON.stringify({ data }) }));
      hint(`${project.name} now uses the “${option.name}” logo.`);
      return true;
    },
    async folder_color(spec, option) {
      const project = needProject(spec);
      const icon = (color) => window.FuseProjects.icon({ color }, { size: 48 });
      const media = swap(icon(window.FuseProjects.colorFor(project)), icon(option.color));
      const note = project.logo_url ? "\n\nIts logo shows in place of the folder until you remove it." : "";
      if (!(await window.fuseConfirm(`Use “${option.name}” as the folder color for ${project.name}?${note}`, { confirmLabel: "Use color", media }))) return false;
      await patchProject(project, { color: option.color });
      hint(`${project.name}'s folder is now ${option.name}.`);
      return true;
    },
    async project_name(spec, option) {
      const project = needProject(spec);
      if (!(await window.fuseConfirm(`Rename the project “${project.name}” to “${option}”?`, { confirmLabel: "Rename project" }))) return false;
      await patchProject(project, { name: option });
      hint(`Renamed the project to “${option}”.`);
      return true;
    },
    async chat_title(spec, option) {
      const thread = threadOf(spec);
      if (!thread) throw new Error("That chat is no longer in your chats.");
      const question = thread.id === S.currentId ? `Rename this chat to “${option}”?` : `Rename the chat “${thread.title}” to “${option}”?`;
      if (!(await window.fuseConfirm(question, { confirmLabel: "Rename chat" }))) return false;
      const updated = await net.api("/chat/threads/" + encodeURIComponent(thread.id), {
        method: "PATCH",
        body: JSON.stringify({ title: option, command_id: newSession() }),
      });
      if (thread.id === S.currentId) S.serverRevision = Math.max(S.serverRevision, updated.revision);
      upsertLocal(updated);
      hint(`Renamed the chat to “${option}”.`);
      return true;
    },
  };

  async function choose(id, version, field, index) {
    if (busy || !FIELDS.has(field) || !Number.isInteger(index)) return;
    if (net.requireLive()) {
      busy = true;
      try {
        const spec = await load(id, version);
        const option = Array.isArray(spec[field]) ? spec[field][index] : null;
        if (option != null && (await apply[field](spec, option))) applied.set(`${id}:${field}`, index);
      } catch (error) {
        showFailure(FAILED[field], error);
      } finally {
        busy = false;
      }
    }
    // Every card, also the one clicked, learns the new state (and that the
    // click is settled) whatever happened.
    refresh();
  }

  // From artifacts.js, for a message from an inline or pane frame showing
  // artifact id at version. clicked: the page had a real click.
  function onMessage(source, id, version, data, clicked) {
    if (!source || !id) return;
    if (data.type === "choices-hello") {
      frames.set(source, { id, version });
      post(source, id, version);
    } else if (data.type === "choose" && clicked) {
      frames.set(source, { id, version });
      choose(id, version, String(data.field || ""), Number(data.index));
    } else if (data.type === "choose") {
      post(source, id, version);
    }
  }

  return { onMessage, refresh };
}
