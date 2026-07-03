"""Discovery from LLDP/CDP neighbor output (offline, text-based)."""
from core.discovery import (
    detect_format,
    discover_from_texts,
    kind_from,
    parse_neighbors,
)

HUAWEI_LLDP = """\
GigabitEthernet0/0/1 has 1 neighbor(s):

Neighbor index :1
Chassis type   :MAC address
Chassis ID     :00e0-fc12-3456
Port ID type   :Interface name
Port ID        :GigabitEthernet0/0/2
System name    :SwitchB
System description :
Huawei Versatile Routing Platform Software VRP (R) software, Version 5.170 (S5720)
System capabilities supported :Bridge Router
System capabilities enabled   :Bridge
Management address type :ipv4
Management address     :192.168.1.2

GigabitEthernet0/0/2 has 1 neighbor(s):

Neighbor index :1
Port ID        :GigabitEthernet0/0/0
System name    :RouterA
System description :
Huawei AR2220 Router
System capabilities enabled   :Router
Management address     :192.168.1.1
"""

CISCO_CDP = """\
-------------------------
Device ID: SwitchB.example.com
Entry address(es):
  IP address: 192.168.1.2
Platform: cisco WS-C2960-24TT-L,  Capabilities: Switch IGMP
Interface: GigabitEthernet0/1,  Port ID (outgoing port): GigabitEthernet0/2
Holdtime : 145 sec
-------------------------
Device ID: RouterA
Entry address(es):
  IP address: 192.168.1.1
Platform: cisco ISR4331,  Capabilities: Router
Interface: GigabitEthernet0/2,  Port ID (outgoing port): GigabitEthernet0/0/0
"""


def test_detect_format():
    assert detect_format(HUAWEI_LLDP) == "huawei_lldp"
    assert detect_format(CISCO_CDP) == "cdp"


def test_kind_from_capabilities():
    assert kind_from("Bridge Router") == "switch"   # L3 switch
    assert kind_from("Router") == "router"
    assert kind_from("Switch IGMP") == "switch"
    assert kind_from("", "cisco Server blade") == "server"


def test_parse_huawei_lldp_neighbors():
    nbrs = parse_neighbors(HUAWEI_LLDP)
    names = {n.neighbor_name for n in nbrs}
    assert names == {"SwitchB", "RouterA"}
    b = next(n for n in nbrs if n.neighbor_name == "SwitchB")
    assert b.local_intf == "GigabitEthernet0/0/1"
    assert b.neighbor_intf == "GigabitEthernet0/0/2"
    assert b.mgmt_ip == "192.168.1.2"
    assert "S5720" in b.platform


def test_parse_cisco_cdp_neighbors():
    nbrs = parse_neighbors(CISCO_CDP)
    r = next(n for n in nbrs if n.neighbor_name.startswith("RouterA"))
    assert r.kind == "router"
    assert "ISR4331" in r.platform
    assert r.mgmt_ip == "192.168.1.1"


def test_discover_builds_topology_with_models():
    t = discover_from_texts([("SwitchA", HUAWEI_LLDP)])
    # SwitchA (local) + SwitchB + RouterA
    assert len(t.devices) == 3
    assert len(t.links) == 2
    routera = next(d for d in t.devices.values() if d.name == "RouterA")
    assert routera.kind == "router"
    assert routera.mgmt_ip == "192.168.1.1"
    switchb = next(d for d in t.devices.values() if d.name == "SwitchB")
    assert "model" in switchb.metadata


def test_merge_dedups_reciprocal_links():
    # SwitchA sees SwitchB on Gi0/0/1<->Gi0/0/2; SwitchB sees SwitchA reciprocally.
    a_view = """\
GigabitEthernet0/0/1 has 1 neighbor(s):
Port ID        :GigabitEthernet0/0/2
System name    :SwitchB
System capabilities enabled :Bridge
"""
    b_view = """\
GigabitEthernet0/0/2 has 1 neighbor(s):
Port ID        :GigabitEthernet0/0/1
System name    :SwitchA
System capabilities enabled :Bridge
"""
    t = discover_from_texts([("SwitchA", a_view), ("SwitchB", b_view)])
    assert len(t.devices) == 2          # SwitchA + SwitchB, not 4
    assert len(t.links) == 1            # the reciprocal entries collapse to one


def test_discovered_topology_validates_clean_enough():
    from core.validate import validate

    t = discover_from_texts([("SwitchA", CISCO_CDP)])
    # discovery shouldn't emit hard structural errors (dangling/double-linked)
    codes = {i.code for i in validate(t)}
    assert "DANGLING_LINK" not in codes
    assert "PORT_DOUBLE_LINKED" not in codes
