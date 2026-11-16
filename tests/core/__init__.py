"""Every test module below imports this package first: the sandbox check and
the process guard run here too, so options that skip conftest files
(--noconftest, --confcutdir) cannot skip them (tests/safety.py)."""
import sys
from pathlib import Path

_REPO = str(Path(__file__).resolve().parents[2])
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from tests.safety import install_process_guard, refuse_unless_sandboxed  # noqa: E402

refuse_unless_sandboxed()
install_process_guard()
