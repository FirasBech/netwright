from core.model import Device, Link, Port, Topology, Vlan
from core.policy import VlanPolicyEngine
from core.reachability import trace


def _host(dev_id, vlan):
    return Device(dev_id, dev_id.upper(), "host",
                  ports=[Port("e0", "e0", mode="access", access_vlan=vlan)])


def _switch_trunked(dev_id, access_vlan, trunk_allowed):
    return Device(
        dev_id, dev_id.upper(), "switch",
        ports=[
            Port("a", "a", mode="access", access_vlan=access_vlan),
            Port("t", "t", mode="trunk", allowed_vlans=list(trunk_allowed)),
        ],
    )


def _same_vlan_topology(carry: bool):
    """h1 - sw1 =trunk= sw2 - h2, all VLAN 10. carry toggles the trunk VLAN."""
    t = Topology(vlans={10: Vlan(10, "Sales")})
    allowed = [10] if carry else [20]
    for d in [
        _host("h1", 10),
        _switch_trunked("sw1", 10, allowed),
        _switch_trunked("sw2", 10, allowed),
        _host("h2", 10),
    ]:
        t.add_device(d)
    t.add_link(Link("l1", "h1", "e0", "sw1", "a"))
    t.add_link(Link("l2", "sw1", "t", "sw2", "t", kind="trunk"))
    t.add_link(Link("l3", "sw2", "a", "h2", "e0"))
    return t


def test_same_vlan_reachable_with_path():
    r = trace(_same_vlan_topology(carry=True), "h1", "h2")
    assert r.reachable is True
    assert r.path[0] == "h1" and r.path[-1] == "h2"


def test_same_vlan_unreachable_when_trunk_drops_vlan():
    r = trace(_same_vlan_topology(carry=False), "h1", "h2")
    assert r.reachable is False


def test_inter_vlan_denied_by_default():
    t = Topology(vlans={10: Vlan(10, "Sales"), 20: Vlan(20, "Eng")})
    t.add_device(_host("h1", 10))
    t.add_device(_host("h2", 20))
    r = trace(t, "h1", "h2")
    assert r.reachable is False
    assert "denied" in r.reason


def test_inter_vlan_permitted_by_policy():
    t = Topology(vlans={10: Vlan(10, "Sales"), 20: Vlan(20, "Eng")})
    t.add_device(_host("h1", 10))
    t.add_device(_host("h2", 20))
    policy = VlanPolicyEngine({10: [20]})
    r = trace(t, "h1", "h2", policy)
    assert r.reachable is True
