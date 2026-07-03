import json

from core.export import export_json, export_policy, export_svg, policy_map
from core.model import AclRule, Topology, Vlan
from core.policy import VlanPolicyEngine


def test_export_json_round_trips(tmp_path, sample_topology):
    path = tmp_path / "t.json"
    export_json(sample_topology, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert Topology.from_dict(data).to_dict() == sample_topology.to_dict()


def test_export_policy_shape_is_securelink_compatible(tmp_path):
    t = Topology(
        vlans={10: Vlan(10, "Sales"), 20: Vlan(20, "Eng")},
        acls=[AclRule(10, 20, "permit"), AclRule(10, 10, "permit")],
    )
    path = tmp_path / "vlan_policy.json"
    mapping = export_policy(t, path)
    # string keys on disk, deny-by-default
    assert set(mapping.keys()) == {"10"}
    assert sorted(mapping["10"]) == [10, 20]

    # the vendored engine accepts what we exported
    engine = VlanPolicyEngine.from_file(path)
    assert engine.is_allowed(10, 20) is True
    assert engine.is_allowed(20, 10) is False


def test_export_svg_has_expected_substrings(sample_topology):
    svg = export_svg(sample_topology)
    assert svg.startswith("<svg")
    assert svg.strip().endswith("</svg>")
    assert "SW1" in svg
    assert "V10" in svg  # VLAN legend entry
