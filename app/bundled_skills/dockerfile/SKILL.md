---
name: dockerfile
description: Write lean, secure Dockerfiles and compose files with pinned bases, multi-stage builds, a non-root user, good layer caching, healthchecks and no baked-in secrets. Use when containerising an app or reviewing a Dockerfile or compose file.
metadata:
  category: Ops
---
# Dockerfiles and compose

Read the app first: language, runtime version, build command, start command, port, env vars, files written at runtime. Match any existing Docker setup and CI.

## Base image
- Pin a specific tag (`python:3.12-slim-bookworm`, `node:22-bookworm-slim`), never `latest`. For reproducible production builds, also pin the digest (`@sha256:...`) and note how to update it.
- Prefer slim or distroless runtime images. Alpine is small but uses musl; native wheels or binaries may break or build slowly.

## Multi-stage builds
- Build stage: compilers, dev dependencies, the build itself.
- Runtime stage: only the runtime, production dependencies and build output, copied with `COPY --from=build`.
- Compiled languages (Go, Rust): build a static binary and run it on `distroless` or `scratch` when possible.

## Layer caching
- Copy dependency manifests first, install, then copy source:
  ```dockerfile
  COPY package.json package-lock.json ./
  RUN npm ci --omit=dev
  COPY . .
  ```
- Use lockfile installs (`npm ci`, `pip install --no-cache-dir -r requirements.txt`, `uv sync --frozen`, `go mod download`).
- BuildKit cache mounts speed rebuilds: `RUN --mount=type=cache,target=/root/.cache/pip pip install ...`.
- Combine `apt-get update && apt-get install -y --no-install-recommends ... && rm -rf /var/lib/apt/lists/*` in one `RUN`.
- Add a `.dockerignore`: `.git`, `node_modules`, virtualenvs, build output, `.env*`, local data, logs.

## Security
- Run as non-root: create a user (`useradd -r -u 10001 app` or the image's built-in user such as `node`) and set `USER`. Files the app writes need matching ownership (`COPY --chown`).
- No secrets in the image: no `ENV API_KEY=...`, no `COPY .env`, no tokens in `ARG` (visible in history). For build-time secrets use `RUN --mount=type=secret`; supply runtime secrets via env or secret files at run time.
- Install only what is needed; remove build tools in the runtime stage.
- Optionally scan with what is available (`docker scout`, `trivy`), and report findings rather than auto-upgrading.

## Runtime
- Exec form for the process: `CMD ["node", "server.js"]` so signals reach it and shutdown is graceful. Use an init (`--init` or `tini`) if the app spawns children.
- `EXPOSE` the real port; bind to `0.0.0.0` inside the container.
- `HEALTHCHECK` that exercises the app cheaply, using a tool present in the image (curl may not be installed in slim images).
- Set `PYTHONUNBUFFERED=1` / `NODE_ENV=production` as appropriate; log to stdout.

## Compose
- Pin image tags; use `build:` for local services.
- `env_file` or `environment` with `${VAR}` substitution; never commit real secret values.
- `depends_on` with `condition: service_healthy` plus healthchecks for databases.
- Named volumes for persistent data; restart policy `unless-stopped` for long-running services.
- Publish ports only where needed; bind admin ports to `127.0.0.1`.

## Verify
`docker build -t app:test .`, check size with `docker image ls app:test`, run it, hit the health endpoint, and confirm `docker run --rm app:test id` is not root. Do not push images or remove existing images, volumes or containers without asking.
