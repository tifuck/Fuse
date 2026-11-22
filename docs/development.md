# Developing Fuse

Fuse is an OpenAI-compatible router (`/v1`) plus a dashboard (chat, coding
agent, admin). Python package: `app/` (entry `app.main:app`). No frontend
build: Jinja templates and plain JS/CSS are served as-is.

## Layout

| Path | What lives there |
| --- | --- |
| `app/` | The server. Large modules keep stable facades (`app/chats.py`, `app/chat_store.py` re-export their owner modules); new code imports the owner. |
| `app/web/` | Dashboard HTTP routes (`auth`, `hosts`, `skills`, `schedules`, `dashboard`, `models`, `chat`, `notifications`, `artifacts`, `sources`, `settings`); `deps.py` holds shared auth/render helpers. No domain SQL here. |
| `app/providers/` | The provider registry: one `ProviderSpec` per provider (`builtin.py`, API-key presets in `presets.py`), access, sign-in flows, catalogs, model choices, credential helpers. Every per-provider list is derived from it. |
| `app/sources/` | Model backends (`Source` classes), one module per provider; `app/sources/__init__.py` lists, routes (`pick`) and caches models. |
| `app/chat_generation/` | Dashboard generation loop pieces (parsing, streaming, resume policy, tool rounds, run inbox). |
| `app/search/` | Fuse-hosted web search: one file per provider under `providers/`. |
| `app/workspace_ops/` | Workspace tool families used by `app/workspace.py`. |
| `app/bundled_skills/` | Skills shipped with Fuse, one folder per skill (`SKILL.md` plus its files). |
| `agent/` | `fuse-agent` connector: C binary (`src/`, `make`) and Windows Go EXE (`windows/`). |
| `templates/`, `static/` | Pages and assets. Chat UI: runtime in `static/chat.js` + `static/chat/`; each theme (`console`, `tail`, `desktop`) owns all /chat markup and styles in `static/themes/<t>/` + `templates/themes/<t>/`. Vendored code: `static/vendor/`, `static/dict/`. Wallpaper scenes: `static/backdrops/` (`docs/wallpapers.md`). |
| `scripts/` | Operational scripts (database backup, schema dump/init, tool-failure review, usage rollup rebuild) and the test runners (`test.sh`, `test_sandbox.sh`). |
| `scripts/qa/` | Dev-only QA harness, never released: the QA browser (`shot.mjs`), harness evals and the QA login's seeded chats (`dev_qa_account.py`). |
| `scripts/sql/` | `schema.sql` (the empty schema fresh installs load) and dated migrations not yet applied everywhere. |
| `tests/` | `tests/core` (pytest, one folder per area), `tests/js` (Node unit suites, `*.test.cjs`); see `tests/README.md`. The UI has no automated tests: it is checked with screenshots. |
| `docs/` | This guide, `wallpapers.md` (custom backdrops), `themes/` (Tail and Desktop view specs) and `screenshots/` (README images). |
| `fuse-desktop/` | Electron wrapper around the site (no app logic). One build for every server: it asks for the server on first launch (Change server on the sign-in page) and updates from GitHub Releases. A new `package.json` version on `main` makes `.github/workflows/desktop.yml` build and publish release `vX.Y.Z`. `npm run dist` builds locally; `FUSE_URL` (or `site.json` `url`) opens a server first, `FUSE_APP_ID` (repository variable in CI) must stay the released app id. It renders on the GPU on every platform; Use hardware acceleration (Appearance, or right-click an empty spot; restart) turns that off in `<userData>/rendering.json`, as do repeated GPU process crashes, and `FUSE_DISABLE_GPU=1` or `--disable-gpu` for one launch. |

## Configuration

`app/config.py` lists every setting with its default. It reads `config.env`
(settings), then `.env` (secrets, which win), then the process environment.
Templates: `config.env.example` and `.env.example`. Keep secrets out of
`config.env`. `start.sh` owns the uvicorn flags; the systemd unit and the
Docker entrypoint both run it.

## Model sources

Every provider is a `ProviderSpec` in `app/providers/` (`spec.py` documents
each field). Source types, labels, pool kinds, claim order, efforts, access,
catalog schedules, admin and Sources panes, probes and display names all come
from the specs; `tests/core/providers/test_registry.py` checks the specs are
complete and pins routing with a golden table.

- Access: `providers.access.Access(user_id)` asks each provider's
  `spec.access` once per request; pass it (or `user_id=`) to `pick`,
  `models_union` and `models_by_source`. Pools decide which donated
  accounts a request may use in `pool_policy.filter_credentials`; a healthy
  session pin always wins over load balancing.
- Model ids: pools keep their own names (`grok-*`, `gpt-*`, `devin/…`,
  `claude/…`); OpenRouter `openrouter/{author}/{slug}`; API-key providers
  `<prefix>/<model>` (a soft claim: without access the id routes as before).
- Catalogs (`app/providers/catalogs.py`): one shared list per source, saved in
  `provider_catalogs` and refreshed by the refresh loop only when due
  (`spec.catalog_ttl`, two fetches at a time, backoff on failure, ETag for
  API-key providers). `spec.catalog_state` sources (llama, Ollama, pools)
  learn per-model facts while listing and are fetched at every boot;
  `spec.user_catalog` lists per user (per-key presets, Ollama personal keys).
- Model on/off (`app/providers/model_prefs.py`): per user and provider,
  group defaults (`spec.model_groups`) plus per-model overrides in
  `user_model_prefs`; every listing and `pick` apply them.
- Sign-in (`app/providers/logins.py`, `auth_flows.py`): pending sign-ins live
  in `provider_logins` (encrypted, 15 minutes); `/sources/p/{pid}/…` reaches
  each provider's handlers and one revoke route removes accounts
  (`spec.revoke_upstream` ends the grant upstream where the provider has a call).
- Probes never run completions on pooled subscriptions: they check quota
  and/or catalog only.
- Permission auto-approve (`app/jev.py`) asks OpenRouter's decisions API and
  is available only to users with their own OpenRouter key. It fails closed.

### Adding a provider

**An API-key provider** is one `KeyedPreset` in `app/providers/presets.py`:
`id`, `label`, `base_url` (or `regions`), `protocol` (`openai_chat` or
`anthropic_messages`), `auth` (`bearer` or `x-api-key`, extra `headers`),
`prefix` (default the id with hyphens), `catalog` (path or URL; it also
validates a pasted key unless `validate` is set), `hide` (non-chat ids),
`efforts` (regex to levels; none: no effort is sent), `methods` (key types),
`strip` (fields never sent), `per_key` (keys list account-specific models),
`group`, `vendor` (sits on that provider's Sources pane) and `key_url`.
Users then add personal keys on Sources; admins enable it and add site keys
on /admin/sources. A logo is `static/logos/<id>.svg` (else the Fuse icon).
Pricing for API-key providers is not wired yet. Tests: run
`scripts/test.sh providers`; add a wire test in
`tests/core/providers/test_keyed.py` only for a new protocol, header or field
rule.

**A subscription or OAuth provider** is a Source module in `app/sources/` plus
its spec in `app/providers/builtin.py` (`_pool(...)` for a shared pool).
Use `app/providers/creds.py` for credential helpers, `auth_flows` for token
checks and device-code answers, `logins` for the pending sign-in, and set
`revoke_upstream` only if the provider documents a revoke call. Set
`catalog_state` if `models()` learns windows or capabilities. Its Sources
panel is `templates/sources/_pool_<id>.html` (`panel=True`), its sign-in
routes go in `app/web/source_routes/<id>.py` (mounted in `app/web/sources.py`,
which adds `/sources/p/<id>/…`). Tests: sign-in, refresh and routing in
`tests/core/subscriptions/`; the registry test lists what a spec must set.

## Web conventions

- Starlette needs `templates.TemplateResponse(request, name, context)`.
- Static URLs are content-versioned by `app/static_assets.py`: templates use
  `{{ asset('file.js') }}` and imports are plain relative URLs. Never add
  `?v=` by hand.
- Open pages reload themselves after a deploy changes the UI (`buildWatch`
  in `static/app.js`, build id from `static_assets.build_id()`): unprompted,
  once the page is hidden, just brought back, or idle. Never over unsaved
  work: a page that keeps such work outside a form declares it with
  `data-no-reload` on content shown once, `window.fuseUnsaved()` for state
  in JS, `data-reload-safe` around a field the page saves itself.
- A reload must come back to what was on screen. Chat saves its view on
  `pagehide` and restores it on a reload of the same address
  (`static/chat/boot.js`); view state added later belongs in that snapshot,
  the URL, or storage.
- `static/app.css` is an ordered import list; keep its order (cascade).
  Design tokens live in `static/styles/tokens/root.css`.
- /chat is drawn only by its theme. The runtime (`static/chat.js`,
  `static/chat/**`) owns behaviour and data, finds elements by `data-*`
  hooks, and asks the theme for markup (`static/themes/<t>/index.js` returns
  one surface for every theme). `chat.html` includes
  `templates/themes/<t>/_chat.html`. Shared chat stylesheets are gated off
  /chat with `:where(html:not(.is-chat-root))` (they still style settings
  and other pages); each theme's copy lives in `themes/<t>/styles/base/`.
  Palettes, backdrop and text size stay shared in `chat-design.css`; each
  theme saves its own palette, backdrop and strength (`chat-appearance.js`,
  `fuse.chat.views.v1`, first filled from the old global keys) and its font.
  A new theme is its two folders plus a `registry.js` entry: its
  `static/themes/<t>/theme.json` (id, order, label, description, default
  font, wallpaper and scheme) is the one list the `fuse_chat_view` cookie
  check (`app/chat_themes.py`), the Appearance › Layout buttons,
  `chat-appearance.js` and `registry.js` read. The themes are `console` (default), `tail`
  (a modern console: tick lines, an output pane, a status bar) and `desktop`
  (a native coding-app window: top bar, sidebar, inspector).
- `/theme` (every theme) opens a strip of wallpaper cards above the
  composer: `static/chat/composer/wallpapers.js` owns the data, keys
  (arrows, Enter, Esc) and the choice; each theme draws the strip with
  `wallpaperPickerHtml` and keeps the `data-wallpaper-*` hooks. Desktop
  also opens it from Sidebar › Customize.

## run_code (code mode)

Coding agents get `run_code`: JavaScript whose workspace calls are real tool
calls. `app/code_mode.py` starts one `app/code_worker.py` process per run (a
`mini-racer` V8 isolate, jitless, empty environment, CPU/data/file/fd limits,
no new processes, no_new_privs, killed at its deadline); each helper call
comes back over the pipe and `tool_runner.run_tools` runs it as a child
`ToolJob` of the same round (`parent` set, same `call_id`), so the gate,
confinement and rows apply as to a direct call. Only the parent's
text reaches the model (`tool_jobs._call_result`); child traces carry `parent`
and sit under their run_code row; a run's reads past the first 40 draw no row.
The deadline stops while a permission prompt waits. A result over 24,000
characters reaches the model cut, with an id; `code_mode.keep` holds the whole
text in memory for 30 minutes, per user and chat, and a later
run reads the rest with `read_result(id, {offset, limit})`. A server running as root
also gives each worker its own empty network namespace and uid 65534; without
root the worker keeps the server's uid. `CODE_MODE=false`, or a missing
`mini-racer`, turns it off.

## Secret protection

`app/secret_guard.py` keeps a user's protected values (table `user_secrets`,
encrypted with `APP_SECRET`, one stand-in each from `app/secret_fakes.py`) and
is the only code that swaps them. Nothing is detected in free text: a value
is replaced only after the user accepted it, by exact match. Three doors:

- **Provider:** `api._run_chat` masks every request (also `jev.evaluate`, a
  run_code call's nested results, and the tool-failure log). On by default;
  only `/v1` opts out, so a new caller cannot skip it.
- **Tool:** `workspace._execute` restores whole stand-ins in command text,
  file content, edit strings and grep patterns, before the denylist and the
  permission review. Paths and other tools are never touched. A cut
  stand-in, or a value with shell characters bound for a command, refuses
  the call with the reason.
- **User:** `chat_generation.consume.consume_round` restores reply text as
  it streams (`StreamRestorer`) and before it is stored. Reasoning and the
  arguments of tools that never reach a machine keep their stand-ins.

So Fuse's own records hold real values; stand-ins exist on the wire to a
provider. Both directions are pure functions of the user's registry: there
is no per-chat state. `app/secret_scan.py` reads a project's env files when a
turn starts and after a tool round changed the machine, and offers what is
clearly a secret. The owner is asked once per secret: a known value is never
offered again, and a new value under a name already decided in that file
follows the decision unasked (`secret_guard.offer`). For a new one the turn
waits (`secret_guard.wait_for_decision`, the
`secrets` run event, `/chat/threads/{id}/secrets/offer`) until the owner
decides. `tests/core/secrets/` holds the invariants: with a realistic
registry no file of this repository changes through either door.

### Environment secrets

A protected value with a variable name (`origin='env'`, the name in `label`)
is an environment secret: a way to give a command a credential that is kept
on Fuse and not on the machine. A shell call asks for it by name
(`env: ["HCLOUD_TOKEN"]` on `run_terminal_command` or `monitor`) and
`workspace._execute` puts the value in that one process's environment: a
connector takes it as `env` beside `cmd` (connector 1.10.0 or newer; an
older one is refused, never given the value another way), an SSH host reads
it from the job script's stdin. The command text, the job row, the card and
the transcript hold the name only, and whatever the command prints is masked
by the provider door like any protected value (always, whatever the Protect
switch says).

Its owner allows each use on a permission card (`permissions.review_env`,
`perm_kind` and grant kind `env`): once, for the chat, or for that machine
for good. Bypass skips the card; Jev never answers it. An unattended run
(schedules) gets only what was allowed for good on that machine. Deleting the
secret deletes its grants. The system prompt lists the names; a skill that
needs one names it in its body.

## Stored transcripts

- Large inline images in `chat_runs.provider_messages`/`request` and
  `chat_threads.retained_history` are kept once per thread in `chat_images`
  (`app/chat_images.py`); the stored JSON carries `fuse-image:<sha256>`.
  Writers go through `chat_images.store`/`store_request`; `api._invoke_chat`
  puts the original bytes back before a request reaches a provider. Code that
  hashes or compares provider rows uses the canonical form
  (`chat_images.canonical_row`/`canonical_content`).
- Those rows can be tens of MB: never `SELECT *` from `chat_runs` or
  `chat_threads` on a hot path; name the columns.
- A live run writes its row several times per tool round, and MariaDB
  rewrites a changed `LONGTEXT` whole. Keep small, frequently changing state
  out of columns that hold large data: once a leg's `provider_messages` is
  saved, `save_turn_progress` drops `request.messages` (readers take
  `provider_messages` first; requeues rebuild `request.messages` from it),
  and it sends only the columns that changed. Read `provider_messages` only
  when a check actually needs it.
- `chat_runs.provider_messages` is a virtual column over the invisible
  `provider_base` and `provider_tail`: a save that extends the transcript
  writes only the rows after the base (`chat_checkpoint._provider_changes`).
  Read `provider_messages` as before, but never write it: store a whole
  transcript with `db.set_provider_sql()` (UPDATE) or `db.provider_column()`
  (INSERT), which also work on the legacy single-column layout.
- A live run's activity is the small `chat_runs.activity` column, not
  `request.agent.activity`; `chat_views.run_view`/`with_activity` fold it
  back for readers. Checkpoints write only the columns that changed.

## Tests

The tests live on the `dev` branch only: releases on `main` leave out
`tests/`, the test scripts and the connector's tests.

Install the test tooling: `pip install pytest pytest-xdist pytest-timeout`.

- **`scripts/test.sh`** runs the core suite (`tests/core`); `scripts/test.sh
  <area>…` runs areas and `scripts/test.sh changed` the areas your change
  touches. What gets a test and how to write one: `tests/README.md`.
- Core tests run on MariaDB only: a persistent `fuse_fixture_core_*` schema
  with the source schema's structure, emptied before each test
  (`tests/mariadb_fixture.py`), using a dedicated MariaDB account
  (`FUSE_FIXTURE_DB_USER`, default `fuse_fixture`; `FUSE_FIXTURE_DB_PASSWORD`;
  `FUSE_FIXTURE_DB_SOCKET`). The fixture prints the provisioning SQL when the
  account is missing. `tests/conftest.py` refuses connections to real schemas.
  Tests mock httpx: never hit search providers or pooled models.
- **Connector:** `make -C agent test`; Windows: `make -C agent/windows test`.

## Schema changes

Write a dated, idempotent migration in `scripts/sql/`, apply it to each
database, then regenerate `scripts/sql/schema.sql` with
`scripts/dump_schema.sh` (`tests/core/schema/test_fresh_install.py` fails while
it is stale). Delete the dated file once every deployment has it.

## Connector releases

Bump `agent/VERSION`, then `make -C agent dist` builds every SKU into
`agent/dist`, which Fuse serves: Linux amd64, Linux arm64 (needs
`gcc-aarch64-linux-gnu` and a static aarch64 OpenSSL via `OPENSSL_ARM64`) and
Windows (needs Go 1.25+). Agents update when the served version is newer than
theirs.
