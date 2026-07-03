import pytest

from core.export import export_device_cli
from core.model import Device, Port, Topology, Vlan


def _device():
    return Device(
        "sw1", "Core SW", "switch", mgmt_ip="10.0.0.1",
        ports=[
            Port("Gi0/1", "Gi0/1", mode="access", access_vlan=10),
            Port("Gi0/2", "Gi0/2", mode="trunk", native_vlan=1,
                 allowed_vlans=[10, 20, 30]),
        ],
    )


def test_ios_export_substrings():
    t = Topology(vlans={10: Vlan(10, "Sales"), 20: Vlan(20, "Eng"),
                        30: Vlan(30, "Guest")})
    t.add_device(_device())
    cfg = export_device_cli(t, "sw1")
    assert "SIMULATED" in cfg  # honesty banner
    assert "hostname Core-SW" in cfg
    assert "vlan 10" in cfg and "name Sales" in cfg
    assert "interface Gi0/1" in cfg
    assert "switchport access vlan 10" in cfg
    assert "switchport mode trunk" in cfg
    assert "switchport trunk allowed vlan 10,20,30" in cfg
    assert "ip address 10.0.0.1" in cfg


def test_ios_export_unknown_device_raises():
    with pytest.raises(KeyError):
        export_device_cli(Topology(), "nope")


def test_ios_export_rejects_unknown_dialect():
    t = Topology()
    t.add_device(_device())
    with pytest.raises(ValueError):
        export_device_cli(t, "sw1", dialect="junos")
