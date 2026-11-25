"""The bundled hetzner-cloud skill's script: a root password never reaches the
output, and a hand-edited expiry label does not break reap, create or list.
Its API calls are fakes; nothing reaches Hetzner or runs ssh."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "app" / "bundled_skills" / "hetzner-cloud" / "scripts" / "hcloud.py"


@pytest.fixture
def hcloud(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("hcloud_skill_script", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "CONF_DIR", tmp_path / "conf")
    return mod


def server(sid, expires):
    return {"id": sid, "name": f"s{sid}", "status": "running", "server_type": {"name": "cx22"},
            "location": {"name": "fsn1"}, "public_net": {"ipv4": {"ip": "203.0.113.9"}, "ipv6": None},
            "created": "2026-11-25T00:00:00Z", "labels": {"managed-by": "fuse", "fuse-expires": expires}}


def test_a_non_numeric_expiry_is_skipped_with_a_warning(hcloud, monkeypatch, capsys):
    servers = [server(1, "1"), server(2, "soon"), server(3, "99999999999")]
    deleted = []
    monkeypatch.setattr(hcloud, "get_all", lambda path, key: servers)
    monkeypatch.setattr(hcloud, "delete_server", lambda s: deleted.append(s["id"]))
    assert [s["id"] for s in hcloud.reap(dry_run=False)] == [1]
    assert deleted == [1]
    assert "non-numeric" in capsys.readouterr().err
    assert hcloud.server_row(servers[1])["expires"] == "-"


def test_create_writes_the_root_password_to_a_private_file_not_the_output(hcloud, monkeypatch, capsys):
    created = server(7, "99999999999")

    def api(method, path, body=None, token=None):
        if method == "POST":
            return {"action": {}, "next_actions": [], "server": {"id": 7}, "root_password": "hunter2-root"}
        return {"server": created}

    monkeypatch.setattr(hcloud, "api", api)
    monkeypatch.setattr(hcloud, "wait", lambda action: None)
    monkeypatch.setattr(hcloud, "reap", lambda dry_run: [])
    monkeypatch.setattr(hcloud, "quote", lambda *a: {"hourly_net": "0.01", "currency": "EUR"})
    args = SimpleNamespace(type="cx22", location="fsn1", no_ipv4=False, no_ipv6=False, yes=True, ttl="2h",
                           label=None, no_agent_key=True, ssh_key=None, name="box", image="ubuntu-24.04",
                           user_data=None, setup=None, json=True)
    hcloud.cmd_create(args)

    out = capsys.readouterr().out
    assert "hunter2-root" not in out
    path = Path(json.loads(out)["root_password_file"])
    assert path.read_text().strip() == "hunter2-root"
    assert path.stat().st_mode & 0o777 == 0o600
