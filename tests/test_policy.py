import json

from core.policy import VlanPolicyEngine


def test_deny_by_default():
    engine = VlanPolicyEngine({10: [10, 20]})
    assert engine.is_allowed(10, 20) is True
    assert engine.is_allowed(10, 30) is False  # not listed
    assert engine.is_allowed(99, 10) is False  # src not in map


def test_load_skips_malformed_entries(tmp_path):
    path = tmp_path / "vlan_policy.json"
    path.write_text(
        json.dumps({"10": [10, 20], "bad": [1], "20": "notalist", "30": [30, "x"]}),
        encoding="utf-8",
    )
    engine = VlanPolicyEngine.from_file(path)
    assert engine.is_allowed(10, 20) is True
    assert engine.is_allowed(30, 30) is True  # "x" skipped, 30 kept
    assert engine.is_allowed(20, 20) is False  # "notalist" dropped


def test_round_trips_to_string_keys():
    engine = VlanPolicyEngine({10: [20, 10], 30: [30]})
    d = engine.as_dict()
    assert set(d.keys()) == {"10", "30"}
    assert d["10"] == [10, 20]  # sorted
