from ai.assistant import propose_change
from ai.client import NetwrightAI
from core.model import Device, Port, Topology, Vlan

from _fakes import propose_response


def _topology_with_sw1():
    t = Topology(name="T")
    t.add_device(
        Device("sw1", "SW1", "switch", ports=[Port("g0/3", "g0/3")])
    )
    return t


def test_intent_yields_ordered_ops():
    ops = [
        {"op": "create_vlan", "id": 40, "name": "Guest"},
        {"op": "set_port_vlan", "device": "sw1", "port": "g0/3", "vlan": 40},
    ]
    ai = NetwrightAI(client=propose_response("Add guest VLAN 40", ops))
    change = propose_change(_topology_with_sw1(), "add a guest VLAN 40 on sw1 g0/3", ai)

    assert [o["op"] for o in change.ops] == ["create_vlan", "set_port_vlan"]
    assert change.applicable is True
    assert change.has_errors is False


def test_hallucinated_vlan_is_rejected():
    ops = [{"op": "create_vlan", "id": 9999, "name": "Bad"}]
    ai = NetwrightAI(client=propose_response("Add VLAN 9999", ops))
    change = propose_change(_topology_with_sw1(), "make vlan 9999", ai)
    assert change.has_errors is True
    assert change.applicable is False


def test_reference_to_unknown_device_is_rejected():
    ops = [{"op": "set_port_vlan", "device": "ghost", "port": "g0/1", "vlan": 10}]
    ai = NetwrightAI(client=propose_response("Touch ghost", ops))
    change = propose_change(_topology_with_sw1(), "configure ghost", ai)
    assert change.has_errors is True


def test_mass_delete_flagged_as_destructive():
    t = Topology()
    for i in range(4):
        t.add_device(Device(f"d{i}", f"D{i}", "switch"))
    ops = [{"op": "remove_device", "device": f"d{i}"} for i in range(4)]
    ai = NetwrightAI(client=propose_response("Delete everything", ops))
    change = propose_change(t, "remove all devices", ai)
    assert change.destructive == 4
    assert change.is_too_destructive(t) is True
