"""SNMP LLDP-MIB discovery — exercised with a fake session (no real network)."""
import json

import pytest

from core.snmp_discovery import (
    OID_LOC_PORTID,
    OID_REM_CAP_ENABLED,
    OID_REM_MANADDR_SUBTYPE,
    OID_REM_PORTID,
    OID_REM_SYSDESC,
    OID_REM_SYSNAME,
    SnmpCredential,
    available,
    decode_caps,
    lldp_neighbors_from_session,
    load_inventory,
    snmp_discover,
)


class FakeSession:
    """Returns canned LLDP-MIB column walks. Index = timeMark.localPortNum.remIndex."""

    def __init__(self, columns: dict[str, dict[str, object]]):
        self.columns = columns

    def walk(self, oid: str) -> dict[str, object]:
        return dict(self.columns.get(oid, {}))


def _two_neighbor_columns():
    # localPortNum 5 -> Gi0/0/1 ; localPortNum 6 -> Gi0/0/2
    return {
        OID_REM_SYSNAME: {"0.5.1": "SwitchB", "0.6.1": "Edge-RTR"},
        OID_REM_PORTID: {"0.5.1": "GigabitEthernet0/0/2", "0.6.1": "Gi0/0/0"},
        OID_REM_SYSDESC: {"0.5.1": "Huawei S5720 Switch", "0.6.1": "Cisco ISR4331"},
        OID_REM_CAP_ENABLED: {"0.5.1": "Bridge", "0.6.1": "Router"},
        OID_LOC_PORTID: {"5": "GigabitEthernet0/0/1", "6": "GigabitEthernet0/0/2"},
        OID_REM_MANADDR_SUBTYPE: {
            "0.5.1.1.4.10.0.0.2": 1,   # ipv4 (subtype 1, len 4) -> 10.0.0.2
            "0.6.1.1.4.10.0.0.254": 1,
        },
    }


def test_decode_caps_bitstring():
    # 0x20 => bit 2 (Bridge); 0x28 => bits 2 and 4 (Bridge + Router)
    assert decode_caps(bytes([0x20])) == "Bridge"
    assert decode_caps(bytes([0x28])) == "Bridge Router"
    assert decode_caps("Router") == "Router"  # already-decoded strings pass through


def test_lldp_neighbors_correlated_by_index():
    nbrs = lldp_neighbors_from_session(FakeSession(_two_neighbor_columns()))
    by_name = {n.neighbor_name: n for n in nbrs}
    assert set(by_name) == {"SwitchB", "Edge-RTR"}
    b = by_name["SwitchB"]
    assert b.local_intf == "GigabitEthernet0/0/1"     # resolved via loc-port table
    assert b.neighbor_intf == "GigabitEthernet0/0/2"
    assert b.mgmt_ip == "10.0.0.2"                     # parsed from man-addr index
    assert b.kind == "switch"
    assert by_name["Edge-RTR"].kind == "router"
    assert by_name["Edge-RTR"].mgmt_ip == "10.0.0.254"


def test_snmp_discover_builds_and_merges():
    cols = _two_neighbor_columns()
    result = snmp_discover(
        [SnmpCredential("10.0.0.1", name="CoreSW")],
        session_factory=lambda c: FakeSession(cols),
        authorized=True,
    )
    assert result.reached == ["10.0.0.1"]
    names = {d.name for d in result.topology.devices.values()}
    assert {"CoreSW", "SwitchB", "Edge-RTR"} <= names
    assert len(result.topology.links) == 2


def test_authorized_gate_blocks_by_default():
    with pytest.raises(PermissionError):
        snmp_discover([SnmpCredential("10.0.0.1")], session_factory=lambda c: None)


def test_failed_device_recorded_not_fatal():
    def factory(cred):
        if cred.host == "10.0.0.9":
            raise ConnectionError("no SNMP response")
        return FakeSession(_two_neighbor_columns())

    result = snmp_discover(
        [SnmpCredential("10.0.0.1", name="A"), SnmpCredential("10.0.0.9", name="B")],
        session_factory=factory, authorized=True,
    )
    assert result.reached == ["10.0.0.1"]
    assert "10.0.0.9" in result.errors
    assert result.topology.devices  # still built from the reachable device


def test_community_falls_back_to_env_and_is_redacted(monkeypatch):
    monkeypatch.setenv("NETFORGE_SNMP_COMMUNITY", "s3cret-ro")
    cred = SnmpCredential.from_dict({"host": "10.0.0.1"})
    assert cred.community == "s3cret-ro"
    assert "community" not in cred.redacted()


def test_load_inventory(tmp_path):
    path = tmp_path / "snmp.json"
    path.write_text(
        json.dumps([{"host": "10.0.0.1", "community": "public", "version": "2c"}]),
        encoding="utf-8",
    )
    creds = load_inventory(str(path))
    assert creds[0].host == "10.0.0.1" and creds[0].version == "2c"


def test_available_reflects_pysnmp_presence():
    # available() must agree with whether pysnmp.hlapi actually imports, so this
    # holds whether or not the optional dependency is installed.
    try:
        import pysnmp.hlapi  # noqa: F401

        expected = True
    except Exception:
        expected = False
    assert available() is expected


def test_dashboard_snmp_discovery_with_fake_session(qapp):
    from ui.dashboard import NetwrightWindow

    win = NetwrightWindow(auto_refresh=False)
    try:
        cols = _two_neighbor_columns()
        result = win.discover_snmp(
            [SnmpCredential("10.0.0.1", name="CoreSW")],
            session_factory=lambda c: FakeSession(cols), authorized=True,
        )
        assert result.reached == ["10.0.0.1"]
        names = {d.name for d in win.project.topology.devices.values()}
        assert {"CoreSW", "SwitchB", "Edge-RTR"} <= names
    finally:
        win.deleteLater()
