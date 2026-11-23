/* Settings page: saves password, chat defaults and coding-agent switches
   through the same JSON endpoints chat and Machines use. Alerts reuse the
   notifications.js toggle; this only mirrors its state into a label. */
(() => {
  const root = document.querySelector("[data-settings-page]");
  if (!root) return;

  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || "";

  async function api(path, method, body) {
    const res = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRF-Token": csrf() },
      body: JSON.stringify(body),
    });
    let data = {};
    try { data = await res.json(); } catch (_) {}
    if (!res.ok) {
      const err = new Error((typeof data.detail === "string" && data.detail) || "Could not save. Try again.");
      err.status = res.status;
      throw err;
    }
    return data;
  }

  function status(el, text, kind) {
    el.textContent = text;
    el.classList.toggle("is-ok", kind === "ok");
    el.classList.toggle("is-fault", kind === "fault");
  }

  async function run(form, out, work) {
    const btn = form.querySelector('button[type="submit"]');
    btn.disabled = true;
    status(out, "Saving…");
    try {
      status(out, await work(), "ok");
    } catch (err) {
      status(out, err.message, "fault");
    } finally {
      btn.disabled = false;
    }
  }

  const pw = root.querySelector("[data-password-form]");
  pw?.addEventListener("submit", (event) => {
    event.preventDefault();
    const out = pw.querySelector("[data-password-status]");
    const f = pw.elements;
    if (f.new_password.value !== f.confirm_password.value) {
      status(out, "New passwords do not match.", "fault");
      return;
    }
    run(pw, out, async () => {
      // Other devices' alerts end with their sessions; this one keeps its own.
      const reg = await navigator.serviceWorker?.getRegistration("/").catch(() => null);
      const sub = reg?.pushManager ? await reg.pushManager.getSubscription().catch(() => null) : null;
      await api("/account/password", "POST", {
        old_password: f.old_password.value,
        new_password: f.new_password.value,
        keep_endpoint: sub ? sub.endpoint : "",
      });
      pw.reset();
      return "Password changed.";
    });
  });

  // Chat defaults carry a revision so a save from here cannot silently
  // overwrite an edit made in the chat Context sheet meanwhile.
  let revision = Number(root.dataset.contextRevision) || 0;
  const ctx = root.querySelector("[data-context-form]");
  ctx?.addEventListener("submit", (event) => {
    event.preventDefault();
    run(ctx, ctx.querySelector("[data-context-status]"), async () => {
      try {
        const data = await api("/chat/context", "PUT", {
          instructions: ctx.elements.instructions.value,
          profile: ctx.elements.profile.value,
          recaps: ctx.elements.recaps.checked,
          ...(ctx.elements.artifacts ? { artifacts: ctx.elements.artifacts.checked } : {}),
          ...(ctx.elements.memory ? { memory: ctx.elements.memory.checked } : {}),
          expected_revision: revision,
          command_id: `settings-${Date.now()}-${Math.random().toString(36).slice(2)}`,
        });
        revision = data.revision;
        ctx.elements.instructions.value = data.instructions;
        ctx.elements.profile.value = data.profile;
        return "Saved.";
      } catch (err) {
        if (err.status === 409) throw new Error("Changed in another window. Reload to see the latest before saving.");
        throw err;
      }
    });
  });

  root.querySelectorAll("[data-memory-form]").forEach((form) => {
    const id = form.closest("[data-memory-id]").getAttribute("data-memory-id");
    const out = form.querySelector(".set-status");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(form, out, async () => {
        await api(`/settings/memories/${id}`, "PUT", {
          type: form.elements.type.value,
          description: form.elements.description.value,
          body: form.elements.body.value,
        });
        return "Saved.";
      });
    });
    form.querySelector("[data-memory-delete]").addEventListener("click", async () => {
      if (!window.confirm("Delete this memory? Chats will no longer see it.")) return;
      try {
        await api(`/settings/memories/${id}`, "DELETE");
        form.closest("[data-memory-id]").remove();
      } catch (err) {
        status(out, err.message, "fault");
      }
    });
  });

  const secrets = root.querySelector("[data-secrets]");
  if (secrets) {
    // Every change redraws the list from the server: reload onto this section.
    const again = () => { location.hash = "secrets"; location.reload(); };
    const act = async (out, work) => {
      try { await work(); again(); } catch (err) { status(out, err.message, "fault"); }
    };
    secrets.querySelectorAll("[data-secrets-switch]").forEach((box) => box.addEventListener("change", () => {
      const out = secrets.querySelector("[data-secrets-status]");
      act(out, () => api("/settings/secrets", "POST", { [box.getAttribute("data-secrets-switch")]: box.checked }));
    }));
    const add = secrets.querySelector("[data-secrets-add]");
    add.addEventListener("submit", (event) => {
      event.preventDefault();
      const out = add.querySelector("[data-secrets-status]");
      act(out, async () => {
        await api("/settings/secrets", "POST", { label: add.elements.label.value, value: add.elements.value.value, env: add.elements.env.checked });
        add.reset();
      });
    });
    secrets.querySelector("[data-secrets-reset]")?.addEventListener("click", (event) => {
      if (!window.confirm("Forget every saved value? Chats that hold a stand-in will no longer get the real value for it.")) return;
      act(event.target, () => api("/settings/secrets", "DELETE"));
    });
    secrets.querySelectorAll("[data-secret-id]").forEach((row) => {
      const id = Number(row.getAttribute("data-secret-id"));
      const out = row.querySelector(".set-status");
      row.querySelector("[data-secret-set]")?.addEventListener("change", (event) => {
        act(out, () => api("/settings/secrets", "POST", { id, protected: event.target.checked }));
      });
      const show = row.querySelector("[data-secret-show]");
      const field = row.querySelector("[data-secret-value]");
      const dots = field?.value;
      show?.addEventListener("click", async () => {
        const label = show.getAttribute("aria-label").replace(/^(Show|Hide)/, "");
        const set = (open, value) => {
          // The field keeps its size whatever it holds; a long value scrolls inside it.
          field.value = value;
          field.scrollLeft = 0;
          show.setAttribute("aria-pressed", String(open));
          show.setAttribute("aria-label", (open ? "Hide" : "Show") + label);
          show.title = open ? "Hide value" : "Show value";
        };
        if (show.getAttribute("aria-pressed") === "true") return set(false, dots); // the value leaves the page
        show.disabled = true;
        try {
          set(true, (await api(`/settings/secrets/${id}/reveal`, "POST", {})).value);
          status(out, "");
        } catch (err) {
          status(out, err.message, "fault");
        } finally {
          show.disabled = false;
        }
      });
      row.querySelector("[data-secret-delete]").addEventListener("click", (event) => {
        const forget = event.target.getAttribute("data-secret-delete") === "forget";
        if (forget && !window.confirm("Delete this value? Chats that hold its stand-in will no longer get the real value.")) return;
        act(out, () => api(`/settings/secrets/${id}`, "DELETE"));
      });
    });
  }

  const agent = root.querySelector("[data-agent-form]");
  agent?.addEventListener("submit", (event) => {
    event.preventDefault();
    run(agent, agent.querySelector("[data-agent-status]"), async () => {
      await api("/hosts/settings", "POST", {
        agent_coding: agent.elements.agent_coding.checked,
        allow_nested_spawn: agent.elements.allow_nested_spawn.checked,
        default_chat_permissions: agent.elements.default_chat_permissions.value,
        default_project_permissions: agent.elements.default_project_permissions.value,
      });
      return "Saved.";
    });
  });

  const web = root.querySelector("[data-web-form]");
  if (web) {
    const out = web.querySelector("[data-web-status]");
    // The page groups machines by network, so it is drawn again from the server.
    const reload = () => { window.location.hash = "web"; window.location.reload(); };
    web.addEventListener("submit", (event) => {
      event.preventDefault();
      run(web, out, async () => {
        const machines = [...web.querySelectorAll('input[name="web_machine"]:checked')].map((el) => Number(el.value));
        await api("/settings/web", "POST", { machines });
        await api("/settings/web/detect", "POST", {}).catch(() => {});
        reload();
        return "Saved.";
      });
    });
    const detect = web.querySelector("[data-web-detect]");
    detect.addEventListener("click", async () => {
      detect.disabled = true;
      status(out, "Reading public IPs…");
      try {
        await api("/settings/web/detect", "POST", {});
        reload();
      } catch (err) {
        status(out, err.message, "fault");
        detect.disabled = false;
      }
    });
  }

  const cleanup = root.querySelector("[data-cleanup-form]");
  if (cleanup) {
    const f = cleanup.elements;
    const sync = () => {
      cleanup.querySelectorAll("[data-cleanup-group]").forEach((el) => {
        const on = f[el.getAttribute("data-cleanup-group") + "_on"].checked;
        el.classList.toggle("is-off", !on);
        el.querySelectorAll("input, select").forEach((input) => { input.disabled = !on; });
      });
    };
    cleanup.querySelectorAll("[data-cleanup-toggle]").forEach((el) => el.addEventListener("change", sync));
    sync();
    cleanup.addEventListener("submit", (event) => {
      event.preventDefault();
      run(cleanup, cleanup.querySelector("[data-cleanup-status]"), async () => {
        const value = Math.max(1, Math.round(Number(f.archive_value.value) || 0));
        const hours = f.archive_on.checked ? value * (f.archive_unit.value === "days" ? 24 : 1) : 0;
        const days = f.delete_on.checked ? Math.max(1, Math.round(Number(f.delete_days.value) || 0)) : 0;
        if (hours > 8760) throw new Error("Auto-archive can wait at most a year.");
        if (days > 3650) throw new Error("Auto-delete can wait at most ten years.");
        await api("/settings/cleanup", "POST", {
          archive_hours: hours,
          archive_chats: f.archive_chats.checked,
          archive_projects: f.archive_projects.checked,
          delete_days: days,
        });
        return hours || days ? "Saved. Cleanup runs every few minutes." : "Saved. Nothing is archived or deleted automatically.";
      });
    });
  }

  // Appearance tab: the same controls as chat's Appearance sheet, bound by
  // chat-appearance.js. Show thinking is chat's own preference (chat/state.js
  // reads the same key), so it is mirrored here.
  const thinking = root.querySelector("[data-thinking-toggle]");
  if (thinking) {
    const STORE_THINKING = "fuse.chat.thinking.v1";
    try { thinking.checked = localStorage.getItem(STORE_THINKING) === "shown"; } catch (_) {}
    thinking.addEventListener("change", () => {
      try { localStorage.setItem(STORE_THINKING, thinking.checked ? "shown" : "hidden"); } catch (_) {}
    });
  }
  const effortWarn = root.querySelector("[data-effort-cache-warn-toggle]");
  if (effortWarn) {
    const STORE_EFFORT_CACHE_WARN = "fuse.chat.effortCacheWarn";
    try { effortWarn.checked = localStorage.getItem(STORE_EFFORT_CACHE_WARN) !== "off"; } catch (_) {}
    effortWarn.addEventListener("change", () => {
      try { localStorage.setItem(STORE_EFFORT_CACHE_WARN, effortWarn.checked ? "on" : "off"); } catch (_) {}
    });
  }

  const bell = root.querySelector("[data-notif-toggle]");
  const state = root.querySelector("[data-notif-state]");
  if (bell && state) {
    const paint = () => {
      if (bell.hidden) {
        state.textContent = "Not available on this server";
        return;
      }
      state.textContent = bell.getAttribute("aria-pressed") === "true" ? "On" : "Off";
    };
    new MutationObserver(paint).observe(bell, { attributes: true, attributeFilter: ["aria-pressed", "hidden"] });
    if (!("serviceWorker" in navigator) || !("Notification" in window)) {
      bell.hidden = true;
      state.textContent = "Not supported in this browser";
    } else {
      paint();
    }
  }
})();
