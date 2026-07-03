import json

from core.policy import VlanPolicyEngine, acls_from_policy, import_policy_file


def test_acls_from_policy_round_trip():
    engine = VlanPolicyEngine({10: [10, 20], 30: [30]})
    acls = acls_from_policy(engine)
    pairs = {(a.src_vlan, a.dst_vlan) for a in acls}
    assert pairs == {(10, 10), (10, 20), (30, 30)}
    assert all(a.action == "permit" for a in acls)


def test_import_policy_file(tmp_path):
    path = tmp_path / "vlan_policy.json"
    path.write_text(json.dumps({"10": [20], "20": [20]}), encoding="utf-8")
    acls = import_policy_file(path)
    pairs = {(a.src_vlan, a.dst_vlan) for a in acls}
    assert pairs == {(10, 20), (20, 20)}


def test_imported_acls_reproduce_policy(tmp_path):
    # export shape -> import -> rebuild engine yields the same decisions
    original = VlanPolicyEngine({10: [20], 30: [10, 30]})
    path = tmp_path / "p.json"
    path.write_text(json.dumps(original.as_dict()), encoding="utf-8")
    acls = import_policy_file(path)
    rebuilt = VlanPolicyEngine(
        {a.src_vlan: [b.dst_vlan for b in acls if b.src_vlan == a.src_vlan]
         for a in acls}
    )
    assert rebuilt.is_allowed(10, 20) is True
    assert rebuilt.is_allowed(30, 10) is True
    assert rebuilt.is_allowed(20, 10) is False
