---
name: hetzner-cloud
description: "Spawn and drive Hetzner Cloud VPS servers: hourly, auto-expiring, instant SSH, jobs, file copy, resize, delete, live prices. Use when a task needs a VM."
argument-hint: create, run, list or delete
metadata:
  category: Ops
---
Script: `scripts/hcloud.py` (Python 3.8+, stdlib, plus the system `ssh`/`scp`/`ssh-keygen`). Run it from the folder `load_skill` names; below, `H` = `python3 <folder>/scripts/hcloud.py`. No Python? Use the curl recipes in `references/api.md` (load with file=).

## Token
- The user runs `H login` once, in their own terminal. It hides input, checks the token, and saves it to `~/.config/hcloud-fuse/token` (mode 0600). `HCLOUD_TOKEN` in the env overrides the file.
- Never ask for the token in chat. If the user pastes one anyway, use it only as an env var for that command, never write it to disk, and tell them to rotate it.
- Never `cat` the token file or echo `$HCLOUD_TOKEN`. Use `H check` to test the token.

## Spawn
1. `H prices [--location L] [--arch x86|arm] [--type T]` lists type/location pairs available now, cheapest first. Locations (Nov 2026): fsn1, nbg1, hel1, ash, hil, sin.
2. `H quote --type T --location L` returns the exact hourly price and monthly cap, IPv4 included. Tell the user the price.
3. `H --json create --name N --type T --location L [--ttl 4h] [--setup setup.sh] [--image ubuntu-24.04] --yes`
   - Every server gets an expiry, 2h by default. Set `--ttl` to fit the task. Use `--ttl none` only if the user asks for no expiry.
   - The script creates a dedicated agent key (`~/.ssh/hcloud-fuse_ed25519`) on this machine, uploads it to Hetzner, and injects it. It returns after SSH works, with `ssh_ready: true`.
   - `--setup FILE` runs a bash hook once cloud-init has finished. A non-zero `setup_exit` makes create exit non-zero.
   - Without `--yes`, create prints the quote, exits 2, and changes nothing.

## Use the VM
- Foreground: `H run N -- 'apt-get update && make test'`, or `H run N --script job.sh`, or pipe with `--script -`. Scripts of any size go over stdin. Exit code and output pass through. `--timeout S` kills the remote side too and exits 124.
- Background (survives disconnects): `H --json run N --bg --script job.sh` returns `{"job": ID}`. Then:
  - `H logs N ID [--follow | --tail 200]`: `--follow` streams output until the job ends.
  - `H wait N ID [--timeout 3600]`: blocks, then exits with the job's exit code. Exit 124 means still running, 125 means the job was lost.
  - `H jobs N` lists jobs as running, `exit=N` or lost. `H kill N ID` stops the job and its children (exit 143).
  - For long jobs in Fuse: start the job with `--bg`, then run `H wait N ID` as a `background: true` terminal command, so you get notified when it ends. To act on log lines, `monitor` the `H logs N ID --follow` output.
- Files: `H cp ./local N:/remote` and `H cp N:/remote ./local` (`-r` for directories).
- Raw shell: `H ssh N -- cmd`. Repeat calls share one connection, which stays open 10 min.
- Jobs live in `~/.fuse-jobs/<id>/{cmd,log,exit,pid}` on the server.

## Manage
- `H list` shows servers this tool created (`--all` for every server). `H show N` shows one.
- `H extend N --ttl 4h` resets the expiry to now + 4h. Extend before long work, not after.
- `H power N on|off|shutdown|reboot|reset`.
- `H resize N --type T [--upgrade-disk] --yes` shuts down, changes the type, and powers back on if it was running.
- `H delete N --yes` deletes the server; ask the user first unless they asked for it. Its auto-created IPs go with it.
- Expired servers are deleted automatically whenever `create` or `list` runs, or when you run `H reap --yes`. For deletion even when no agent is active, the user can add a cron line: `*/10 * * * * python3 <folder>/scripts/hcloud.py reap --yes`.

## Billing (Hetzner billing FAQ)
- Billed per hour, capped at the monthly price. A partial hour counts as a full hour.
- Servers that are off still bill. Only deleting stops the cost.
- IPv4 is extra and IPv6 is free. `--no-ipv4` saves the fee, but SSH then needs IPv6 on this machine.
- Prices and availability change; types can be sold out. Never quote from memory: run `quote`.

## Gotchas
- Server names must be unique per project and valid RFC 1123 (letters, digits, `-`, `.`).
- `--upgrade-disk` is permanent: disks never shrink, so the server cannot move back to a type with a smaller disk.
- Each agent machine has its own key. A server created from another machine needs `--ssh-key` with that machine's key, or SSH will fail.
- `run --script -` takes over stdin, so the remote script cannot read from stdin itself.
