from core.model import Device, Port, Topology, Vlan


def test_topology_round_trip(sample_topology):
    d = sample_topology.to_dict()
    again = Topology.from_dict(d)
    assert again.to_dict() == d


def test_vlan_keys_serialize_as_strings_and_coerce_back():
    t = Topology(name="x")
    t.vlans = {10: Vlan(10, "Sales")}
    dumped = t.to_dict()
    assert set(dumped["vlans"].keys()) == {"10"}  # string keys on disk
    restored = Topology.from_dict(dumped)
    assert set(restored.vlans.keys()) == {10}  # int keys in memory


def test_device_helpers():
    t = Topology()
    dev = Device("d1", "D1", "switch", ports=[Port("p1", "p1")])
    t.add_device(dev)
    assert t.get_port("d1", "p1") is not None
    assert t.get_port("d1", "nope") is None
    t.remove_device("d1")
    assert "d1" not in t.devices
