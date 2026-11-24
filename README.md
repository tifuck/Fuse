<div align="center">

<img src="static/icon.svg" alt="Fuse" width="88">

# Fuse

### Every model behind one API, and an agent that works on your machines.

A self-hosted LLM router and workspace for you and your team.<br>
One OpenAI-compatible endpoint, a chat that searches the web and writes code on your servers.

[![License: MIT](https://img.shields.io/badge/license-MIT-1E293B)](LICENSE)
![Self-hosted](https://img.shields.io/badge/self--hosted-yes-A5D8FF)
![Python](https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white)
![MariaDB](https://img.shields.io/badge/MariaDB-10.11+-003545?logo=mariadb&logoColor=white)
![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)

[**Install**](#install) ·
[Features](#features) ·
[Configuration](#configuration) ·
[Development](docs/development.md)

<br>

<img src="docs/screenshots/hero.png" alt="Fuse chat in the Desktop view: a sessions sidebar, and a reply whose 15 opened steps read, search, edit, create files and run commands, two of them failed" width="900">

</div>

<br>

**Fuse** gives every user an OpenAI-compatible API (`/v1/chat/completions`,
`/v1/models`) that routes to whichever model backends your site has, plus a
web dashboard with a chat that uses tools, searches the web and runs as a
coding agent on your own machines.

Install it once and the first account becomes the admin. Invite your team, add
model sources, and everyone gets their own keys, chats and machines.

<table>
<tr>
<td width="33%" valign="top">

**🔀 One API for every model**<br>
OpenAI-compatible `/v1` with per-user keys, usage tracking and per-model
pricing.

</td>
<td width="33%" valign="top">

**🤝 Shared subscriptions**<br>
Pool Grok, ChatGPT, Claude, GLM, Kimi, Cursor and more. Each owner decides
who may use their account.

</td>
<td width="33%" valign="top">

**🖥️ Coding agent**<br>
Works on your Linux and Windows machines through an outbound-only connector
or SSH, and asks before it acts. Bulk work runs as sandboxed JavaScript
(`run_code`), so only the results reach the model.

</td>
</tr>
<tr>
<td valign="top">

**🔎 Web search built in**<br>
Searches and reads pages with no search API key, out through the machines
each user picks, so the server's own IP stays out of it. Add a Brave key for
one more source.

</td>
<td valign="top">

**🧵 Subagents and jobs**<br>
Delegates work to subagents, runs background commands and monitors, and
compacts long chats on its own.

</td>
<td valign="top">

**📄 Artifacts**<br>
Live HTML or Markdown pages beside the chat, versioned and shareable by
link.

</td>
</tr>
<tr>
<td valign="top">

**🧩 Skills and memory**<br>
Install skills from GitHub or skills.sh, or write your own. Opt-in memory
across chats.

</td>
<td valign="top">

**⏰ Scheduled tasks**<br>
Prompts that run on a clock in a chat or project, with push notifications
when they finish.

</td>
<td valign="top">

**📱 Phone and desktop**<br>
Installable web app, a desktop app, and live sync across tabs and devices.

</td>
</tr>
</table>

---

## Features

### Three views, one agent

<img src="docs/screenshots/view-console.png" alt="The same chat in the Console view: Update, Bash and Web Search rows opened, with a red and green diff and a failed command's output" width="49%"> <img src="docs/screenshots/view-tail.png" alt="The same chat in the Tail view: one line per call, and the edit's diff in the side pane" width="49%">

Pick how the chat looks under Appearance › Layout: **Console**, dense and
terminal-like (left); **Tail**, one line per call with its output in a side
pane (right); or **Desktop**, a native coding-app window with a sessions
sidebar and an inspector (top).

### Edits your code, shows every diff

Open a project from chat and the agent reads, edits and runs commands in it.
Every change lands in the transcript as a real diff, tagged with the machine
it ran on, and nothing runs without a permission prompt.

### One prompt, many machines

Connect Linux and Windows machines with the `fuse-agent` connector (outbound
HTTPS only, no ports to open) or over SSH. One command runs on a whole list of
hosts at once, with a row per host so a failing box stands out.

### Subagents that fan out

<img src="docs/screenshots/subagents.png" alt="Desktop view with the Activity inspector: one subagent running with its context and time, three finished, one failed and one stopped" width="100%">

Hand work to parallel subagents and get their reports back in the chat.
Background commands and log monitors report in the same way, even while the
main reply is still going.

### A terminal on every machine

<img src="docs/screenshots/terminal.png" alt="A terminal docked under the chat running top on qa-box, with live CPU, memory, disk and network figures above it" width="100%">

Open a shell on the chat's machine, Linux or Windows, right under the
conversation, with live CPU, memory, disk and network figures beside the
prompt.

Each machine keeps its own projects. Mark one read-only, archive it, or start
a new chat in it with one click.

### Every model in one place

<img src="docs/screenshots/models.png" alt="Models page, Locked tab: the models that linking a Grok, Claude, ChatGPT, Cursor or NanoGPT account would unlock" width="100%">

- **Model sources**: a llama.cpp server, any OpenAI-compatible endpoint (with
  key rotation), Ollama Cloud, and per-user OpenRouter and Experiential Labs
  keys.
- **Subscription pools**: link Grok, ChatGPT, Claude, Z.AI (GLM Coding Plan),
  Kimi Code, Devin, Xiaomi MiMo, Cursor and NanoGPT accounts, or Refuseless
  pay-as-you-go keys. Share each one with everyone, with other contributors,
  or keep it private.
- **The same ids work in chat and through `/v1`**, and new upstream models
  appear on their own.

### Artifacts beside the chat

<img src="docs/screenshots/artifacts.png" alt="Replies with inline artifacts: four key numbers compared with last week, and a bar chart of requests by model" width="100%">

Replies can publish live HTML or Markdown pages: dashboards, docs, small
tools. Every edit is a new version, and any one can be shared by link.

### Memory you control

Opt-in notes that chats save and recall later, per project or global. Fuse
refuses to remember anything that looks like a secret.

### Skills

Procedures the agent follows, run with `/name`. Install from GitHub or
skills.sh, upload a zip, or have the agent write one. Skills that are off cost
no tokens.

### Make it yours

Sixteen themes plus your own colors, animated backdrops and a choice of
reading fonts. The default is black with an ice-blue accent.

### Take it with you

<img src="docs/screenshots/mobile.png" alt="Fuse chat on a phone in the Desktop view: a reply's opened steps and the composer" width="280" align="right">

- **Installable web app** on phones and tablets, with Web Push when a reply
  finishes or needs your approval.
- **Live sync**: the same chat streams in every tab and device at once.
- **Desktop app** for Windows, macOS and Linux (`fuse-desktop/`): one build
  for any Fuse server, updated from GitHub Releases.
- **Scheduled tasks** run a prompt on a clock in a chat or project.
- **Context meter** with automatic compaction, so long sessions keep going.

### Built to be trusted

- **Self-hosted.** Your chats, keys and machines stay on your server.
- **API keys are stored hashed.** Provider credentials are encrypted at rest.
- **Machine secrets are locked to your password.** SSH passwords and keys are
  encrypted with a key derived from your account password, so a stolen
  database dump does not reveal them.
- **Invite-only by default** after the first (admin) account.

<br clear="right">

---

## Install

Fuse needs **MariaDB 10.11+** (MySQL is not supported) and **Python 3.11+**
(Debian 12, Ubuntu 24.04 or newer), and serves HTTPS itself. Both installers
below create the database tables for you.

> [!TIP]
> **Use a domain.** Both installers ask for an optional domain (or take
> `--domain fuse.example.com`). It becomes `PUBLIC_BASE_URL`, the address in
> invite links and in the connector install line on the Machines page.
> Connectors verify Fuse's certificate, so the domain needs a trusted one: by
> default the installer gets a free Let's Encrypt certificate with
> [Caddy](https://caddyserver.com) (DNS pointing at the server, ports 80 and
> 443 open). Already running nginx or another proxy? Pass `--no-caddy` and
> point it at `https://127.0.0.1:8088`. Without a domain, the dashboard works
> by IP with a self-signed certificate, but machines can't install the
> connector.

### Option 1: Docker (simplest)

Requires Docker with the Compose plugin (2.24 or newer).

```bash
git clone https://github.com/tifuck/fuse.git && cd fuse
./install.sh --docker            # or: ./install.sh --docker --domain fuse.example.com
```

This writes `.env` with a random `APP_SECRET` and database password, asks for
your domain, builds the image (including the connector binaries), and starts
Fuse, a bundled MariaDB, and Caddy if you chose a domain
(`docker-compose.caddy.yml`, enabled through `COMPOSE_FILE` in `.env`). Open
`https://your-domain` (or `https://localhost:8088`) and register the admin
account with the one-time setup token (`docker compose exec fuse cat data/setup-token`,
or the `register?setup=` link in `docker compose logs fuse`).

- Logs: `docker compose logs -f fuse`
- Update: `git pull && docker compose up -d --build`. The container reads the
  checkout's `.git` (read-only) to show users what changed after an update.
- Data lives in the `db`, `data`, and `certs` volumes.

<details>
<summary><b>Without the installer</b></summary>

Create `.env` with `APP_SECRET=` (48+ random characters) and `DB_PASSWORD=`
(hex), then run `docker compose up -d --build`. Optional settings go in
`config.env` (see `config.env.example`).

</details>

### Option 2: Installer on a Linux host

On Debian or Ubuntu, as root:

```bash
git clone https://github.com/tifuck/fuse.git && cd fuse
./install.sh                     # or: ./install.sh -y --domain fuse.example.com
```

<details>
<summary><b>What the installer does</b></summary>

It asks its questions first, then runs eight steps:

1. Writes `.env` with a random `APP_SECRET` and asks for your domain. An
   existing `.env` is kept.
2. Creates a Python virtual environment in `venv/`.
3. Installs the Python dependencies.
4. Sets up the database: installs `mariadb-server` if missing, creates a
   `fuse` database and user with a random password, and creates Fuse's tables
   (`scripts/init_db.py`).
5. Generates a self-signed TLS certificate in `certs/` for direct access on
   port 8088.
6. Builds the `fuse-agent` connector binaries. This needs `gcc` and
   `libssl-dev`, plus Go 1.25+ for the Windows connector.
7. Installs and starts a `fuse.service` systemd unit.
8. With a domain, installs Caddy and serves `https://your-domain`. An existing
   Caddy config is never touched: the installer prints the site block to add
   instead.

</details>

Flags: `-y` (no prompts), `--domain NAME`, `--no-caddy`, `--no-service`,
`--database-url URL`, `--help`. Re-running it is safe: existing config, certs
and tables are kept. To add a domain later, re-run it with `--domain`.

<details>
<summary><b>Bring your own database</b></summary>

Create an empty MariaDB database and a user with full rights on it, then:

```bash
./install.sh --database-url 'mysql://fuse:PASSWORD@db-host:3306/fuse'
```

Without root, this is the only way to set up the database. The service is
then installed as a systemd user unit.

</details>

<details>
<summary><b>Database tuning</b></summary>

MariaDB's defaults are sized for a tiny database. The installer (when it
installs MariaDB itself) and the Docker setup apply the settings below; on a
server you brought yourself, add them to its config (for example
`/etc/mysql/mariadb.conf.d/90-fuse.cnf`) and restart MariaDB:

```ini
[mariadb]
innodb_flush_log_at_trx_commit = 2
innodb_buffer_pool_size = 1G
innodb_log_file_size = 512M
innodb_log_buffer_size = 64M
```

| Setting | Default | Why |
| --- | --- | --- |
| `innodb_flush_log_at_trx_commit` | `1` | `1` syncs the disk on every commit, and a live chat commits several times a second. `2` syncs once a second instead: commits measured 17 ms → 0.06 ms on slow-sync storage. An OS crash or power loss can lose up to the last second of commits; a MariaDB crash loses nothing. Keep `1` if you cannot accept that. |
| `innodb_buffer_pool_size` | `128M` | The cache for table data. Chat transcripts outgrow the default quickly; use about a quarter of the RAM when MariaDB shares the host with Fuse. |
| `innodb_log_file_size`, `innodb_log_buffer_size` | `96M`, `16M` | Room for the multi-megabyte transcript rows a run rewrites. |

</details>

### After installing

- **The first account to register becomes the admin.** Until it exists,
  `/register` asks for a one-time setup token: Fuse writes it to
  `data/setup-token` (mode 0600) at startup and logs a `register?setup=` link,
  and removes it once the admin account is created. After that,
  registration is invite-only unless `ALLOW_REGISTER=true`; invite links
  expire after `INVITE_TTL_DAYS` (14).
- Add a model source under **Admin › Sources** (for example a llama.cpp or
  OpenAI-compatible URL and key), or link a subscription under **Sources**.
- Connect machines from **Machines**: the install line there uses your domain
  (`PUBLIC_BASE_URL`). If you skipped the domain, re-run the installer with
  `--domain`.
- Behind your own reverse proxy, have it reach Fuse from `127.0.0.1` or set
  `FORWARDED_ALLOW_IPS` to its address. Otherwise every user shares the
  proxy's IP for rate limits. Optionally set `TRUSTED_HOSTS` to your domain.

<details>
<summary><b>Forgot a password</b></summary>

`./venv/bin/python -m scripts.reset_password user@example.com` prints a new
one (Docker: `docker compose exec fuse python -m scripts.reset_password
user@example.com`). Saved SSH machine keys must then be entered again;
connector machines keep working.

</details>

### Updating

- Bare metal: `git pull && ./venv/bin/pip install -r requirements.txt && sudo systemctl restart fuse`
- Docker: `git pull && docker compose up -d --build`

---

## Configuration

Settings live in `config.env` and secrets in `.env` (`.env` wins if both set a
key; the environment wins over both). `config.env.example` and `.env.example`
document them; the full list, with defaults, is in `app/config.py`. The common
ones:

| Variable | File | Purpose |
| --- | --- | --- |
| `APP_SECRET` | `.env` | Required. Signs sessions and encrypts stored provider credentials. Changing it invalidates both. |
| `DATABASE_URL` | `.env` | `mysql://user:password@host:3306/database` |
| `PUBLIC_BASE_URL` | `config.env` | Public HTTPS URL (`https://your-domain`), used in connector install lines and invite links. Needs a trusted certificate. |
| `ALLOW_REGISTER` | `config.env` | Open registration (default: invite-only after the first user) |
| `LLAMA_BASE_URL`, `LLAMA_API_KEY` | `config.env`, `.env` | Optional site-wide llama.cpp backend |
| `BRAVE_SEARCH_API_KEY` | `.env` | Optional extra web search source |
| `WEB_SEARCH_SERVER_FALLBACK` | `config.env` | Web searches and page reads leave through the connectors a user picks under Settings › Web search. `true` lets a user who picked none go out from this server instead (default: off, that user has no web search) |
| `GITHUB_TOKEN` | `.env` | Optional, raises the GitHub rate limit for skill installs |

> [!IMPORTANT]
> Back up the database with `./backup-db.sh`. Keep `.env`, `config.env` and
> `data/vapid.json` too (or set `VAPID_PRIVATE_KEY`/`VAPID_PUBLIC_KEY` in
> `.env`): regenerating the VAPID keys breaks existing push subscriptions.

## Development

[`docs/development.md`](docs/development.md) covers the code layout, model
backends, conventions, and how to run the tests (pytest against disposable
MariaDB schemas, Node unit suites, Playwright browser suites).

## A note on subscription pools

Subscription pools sign in through each provider's own CLI login flow. Using
them through a shared router may conflict with a provider's terms of service;
that is between you and the provider.

## License

MIT, see [LICENSE](LICENSE). Vendored code under `agent/third_party/`,
`static/vendor/`, and `static/dict/` keeps its own license.
