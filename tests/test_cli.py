import json

from ui import cli


def _run(args):
    return cli.main(args)


def test_new_creates_project(tmp_path):
    p = tmp_path / "p.netwright"
    rc = _run(["new", "--project", str(p), "--name", "Lab"])
    assert rc == 0
    assert p.exists()
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert doc["name"] == "Lab"


def test_build_and_validate_clean(tmp_path):
    p = str(tmp_path / "p.netwright")
    _run(["new", "--project", p, "--name", "Lab"])
    _run(["add-device", "--project", p, "--kind", "switch", "--name", "S1", "--id", "s1"])
    _run(["vlan", "create", "--project", p, "--id", "10", "--vlan-name", "Sales"])
    rc = _run(["validate", "--project", p])
    assert rc == 0  # isolated device is a warning, not an error


def test_validate_flags_error(tmp_path):
    p = str(tmp_path / "p.netwright")
    _run(["new", "--project", p])
    _run(["vlan", "create", "--project", p, "--id", "5000", "--vlan-name", "Bad"])
    rc = _run(["validate", "--project", p])
    assert rc == 1  # VLAN_OUT_OF_RANGE


def test_export_formats(tmp_path):
    p = str(tmp_path / "p.netwright")
    _run(["new", "--project", p])
    _run(["vlan", "create", "--project", p, "--id", "10", "--vlan-name", "Sales"])
    for fmt, ext in [("json", "json"), ("svg", "svg"), ("policy", "json")]:
        out = tmp_path / f"out.{ext}"
        rc = _run(["export", "--project", p, "--format", fmt, "--out", str(out)])
        assert rc == 0
        assert out.exists()


def test_propose_without_key_exits_nonzero(tmp_path):
    p = str(tmp_path / "p.netwright")
    _run(["new", "--project", p])
    rc = _run(["propose", "--project", p, "--intent", "add a vlan"])
    assert rc == 1  # no ANTHROPIC_API_KEY
