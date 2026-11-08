#!/usr/bin/env python3
"""Reset a forgotten password, admin accounts included.

    ./venv/bin/python -m scripts.reset_password user@example.com

Run from the Fuse tree whose database you mean. Prints a new random
password; the user signs in with it and changes it in Settings. The old
password wrapped the account's machine-secret vault, so SSH machines need
their key or password entered again (connector machines keep working).
Every session is signed out and every device's alerts are removed.
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_POOL_SIZE", "2")


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0].startswith("-"):
        print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
        return 2
    from app import accounts

    password = secrets.token_urlsafe(15)
    try:
        ssh_hosts = accounts.reset_password(argv[0], password)
    except ValueError as exc:
        print({"missing": "No account with that email.", "email": "That is not a valid email."}.get(str(exc), str(exc)),
              file=sys.stderr)
        return 1
    print(f"New password for {argv[0]}: {password}")
    if ssh_hosts:
        print("Re-enter the SSH key or password for: " + ", ".join(ssh_hosts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
