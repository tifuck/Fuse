<div align="center">

<img src="static/icon.svg" alt="Fuse" width="88">

# Fuse

### Every model behind one API, and an agent that works on your machines.

A self-hosted LLM router and workspace for you and your team.<br>
One OpenAI-compatible endpoint, a chat that searches the web and writes code on your servers.

[![License: MIT](https://img.shields.io/badge/license-MIT-1E293B)](LICENSE)
![Self-hosted](https://img.shields.io/badge/self--hosted-yes-C8E27A)
![Python](https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white)
![MariaDB](https://img.shields.io/badge/MariaDB-10.11+-003545?logo=mariadb&logoColor=white)
![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)

[**Install**](#install) ·
[Features](#features) ·
[Configuration](#configuration) ·
[Development](docs/development.md)

<br>

<img src="docs/screenshots/hero.png" alt="Fuse chat: the agent edits a Python file on a connected machine, with inline diffs, file writes and shell commands in the transcript" width="900">

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
or SSH, and asks before it acts.

</td>
</tr>
<tr>
<td valign="top">

**🔎 Web search built in**<br>
Searches and reads pages with no search API key. Add a Brave key for one more
source.

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

### A coding agent where your code lives

<img src="docs/screenshots/machines.png" alt="Machines page with a connected machine and its four projects" width="100%">

- **Connect any machine** with the `fuse-agent` connector (Linux binary or
  Windows EXE). It only makes outbound HTTPS calls, so no ports to open. SSH
  works too.
- **Projects per machine**: open a folder from chat and the agent reads,
  edits and runs commands there, with diffs inline in the transcript.
- **Permission prompts** for commands and files, so nothing runs without
  your say.
- **Subagents, background commands and monitors**, with results delivered
  back into the chat as they land.

### Every model in one place

<img src="docs/screenshots/models.png" alt="Models page listing available models grouped by provider" width="100%">

- **Model sources**: a llama.cpp server, any OpenAI-compatible endpoint (with
  key rotation), Ollama Cloud, and per-user OpenRouter and Experiential Labs
  keys.
- **Subscription pools**: link Grok, ChatGPT, Claude, Z.AI (GLM Coding Plan),
  Kimi Code, Devin, Xiaomi MiMo, Cursor and NanoGPT accounts, or Refuseless
  pay-as-you-go keys. Share each one with everyone, with other contributors,
  or keep it private.
- **New upstream models appear on their own**, and the same ids work in
  chat and through `/v1`.

### Skills, artifacts and tasks

<img src="docs/screenshots/skills.png" alt="Skills page with installed skills and a skills.sh search" width="100%">

- **Skills** are procedures the agent follows, run with `/name`. Install from
  GitHub or skills.sh, upload a zip, or have the agent write one.
- **Artifacts** turn a reply into a live page beside the chat: dashboards,
  docs, tools. Every edit is a new version; share one by link.
- **Tasks** run a prompt on a schedule in a chat or project.
- **Memory** (opt-in) lets chats save notes and recall them later.

### Take it with you

<img src="docs/screenshots/phone.png" alt="Fuse chat on a phone, showing a diff and the composer" width="280" align="right">

- **Installable web app** on phones and tablets, with Web Push when a reply
  finishes or needs your approval.
- **Live sync**: the same chat streams in every tab and device at once.
- **Desktop app** for Windows and Linux (`fuse-desktop/`).
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
account.

- Logs: `docker compose logs -f fuse`
- Update: `git pull && docker compose up -d --build`
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

### After installing

- **The first account to register becomes the admin.** After that,
  registration is invite-only unless `ALLOW_REGISTER=true`.
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
| `PUBLIC_BASE_URL` | `config.env` | Public HTTPS URL (`https://your-domain`), used in connector install lines, invite links and desktop updates. Needs a trusted certificate. |
| `ALLOW_REGISTER` | `config.env` | Open registration (default: invite-only after the first user) |
| `LLAMA_BASE_URL`, `LLAMA_API_KEY` | `config.env`, `.env` | Optional site-wide llama.cpp backend |
| `BRAVE_SEARCH_API_KEY` | `.env` | Optional extra web search source |
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
