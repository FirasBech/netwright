import json

import pytest

from core.project import SCHEMA_VERSION, NetwrightProject, ProjectError, migrate


def test_v1_list_vlans_upgrades_to_v2_dict():
    v1 = {
        "schema": "netwright.project",
        "version": 1,
        "name": "old",
        "topology": {
            "name": "old",
            "devices": {},
            "links": {},
            "vlans": [{"id": 10, "name": "Sales"}, {"id": 20, "name": "Eng"}],
            "subnets": [],
            "acls": [],
        },
    }
    upgraded = migrate(v1)
    assert upgraded["version"] == SCHEMA_VERSION
    assert upgraded["topology"]["vlans"]["10"]["name"] == "Sales"


def test_unknown_future_version_is_rejected(tmp_path):
    path = tmp_path / "future.netwright"
    path.write_text(json.dumps({"version": 999, "name": "x"}), encoding="utf-8")
    with pytest.raises(ProjectError):
        NetwrightProject.load(path)


def test_corrupt_json_does_not_overwrite_original(tmp_path):
    path = tmp_path / "bad.netwright"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ProjectError):
        NetwrightProject.load(path)
    # the original bytes are untouched
    assert path.read_text(encoding="utf-8") == "{ not json"
