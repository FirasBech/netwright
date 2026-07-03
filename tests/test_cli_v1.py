import json

from ui import cli


def _run(args):
    return cli.main(args)


def _seed(tmp_path):
    p = str(tmp_path / "p.netwright")
    _run(["new", "--project", p])
    _run(["add-device", "--project", p, "--kind", "switch", "--name", "S1", "--id", "s1"])
    _run(["vlan", "create", "--project", p, "--id", "10", "--vlan-name", "Sales"])
    _run(["set-port", "--project", p, "--device", "s1", "--port", "Gi0/1",
          "--access-vlan", "10"])
    return p


def test_export_ios(tmp_path):
    p = _seed(tmp_path)
    out = tmp_path / "s1.cfg"
    rc = _run(["export", "--project", p, "--format", "ios", "--device", "s1",
               "--out", str(out)])
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert "hostname S1" in text
    assert "switchport access vlan 10" in text
    assert "SIMULATED" in text


def test_export_ios_requires_device(tmp_path):
    p = _seed(tmp_path)
    rc = _run(["export", "--project", p, "--format", "ios", "--out",
               str(tmp_path / "x.cfg")])
    assert rc == 1


def test_import_policy(tmp_path):
    p = _seed(tmp_path)
    policy = tmp_path / "vlan_policy.json"
    policy.write_text(json.dumps({"10": [10, 20]}), encoding="utf-8")
    rc = _run(["import-policy", "--project", p, "--file", str(policy)])
    assert rc == 0
    from core.project import NetwrightProject

    proj = NetwrightProject.load(p)
    pairs = {(a.src_vlan, a.dst_vlan) for a in proj.topology.acls}
    assert (10, 20) in pairs


def test_reach_same_vlan(tmp_path):
    p = str(tmp_path / "r.netwright")
    _run(["new", "--project", p])
    _run(["add-device", "--project", p, "--kind", "host", "--name", "H1", "--id", "h1"])
    _run(["add-device", "--project", p, "--kind", "host", "--name", "H2", "--id", "h2"])
    _run(["vlan", "create", "--project", p, "--id", "10", "--vlan-name", "Sales"])
    _run(["set-port", "--project", p, "--device", "h1", "--port", "Gi0/1",
          "--access-vlan", "10"])
    _run(["set-port", "--project", p, "--device", "h2", "--port", "Gi0/1",
          "--access-vlan", "10"])
    _run(["link", "--project", p, "--a", "h1:Gi0/1", "--b", "h2:Gi0/1"])
    rc = _run(["reach", "--project", p, "--src", "h1", "--dst", "h2"])
    assert rc == 0  # same VLAN, direct link


def test_discover_from_neighbor_file(tmp_path):
    import os
    p = str(tmp_path / "disc.netwright")
    dump = tmp_path / "CoreSW.txt"
    dump.write_text(
        "GigabitEthernet0/0/1 has 1 neighbor(s):\n"
        "Port ID        :GigabitEthernet0/0/24\n"
        "System name    :Access-1\n"
        "System capabilities enabled   :Bridge\n"
        "Management address     :10.0.0.11\n"
        "\nGigabitEthernet0/0/2 has 1 neighbor(s):\n"
        "Port ID        :GigabitEthernet0/0/0\n"
        "System name    :Edge-RTR\n"
        "System description :\nHuawei AR2220E Router\n"
        "System capabilities enabled   :Router\n"
        "Management address     :10.0.0.254\n",
        encoding="utf-8",
    )
    rc = _run(["discover", "--project", p, str(dump)])
    assert rc == 0
    from core.project import NetwrightProject

    proj = NetwrightProject.load(p)
    names = {d.name for d in proj.topology.devices.values()}
    assert {"CoreSW", "Access-1", "Edge-RTR"} <= names
    rtr = next(d for d in proj.topology.devices.values() if d.name == "Edge-RTR")
    assert rtr.kind == "router"
    assert rtr.metadata.get("model", "").strip().startswith("Huawei AR2220E")
    assert rtr.mgmt_ip == "10.0.0.254"
