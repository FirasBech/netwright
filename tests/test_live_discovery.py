"""Live SSH discovery — exercised entirely with a fake runner (no real network)."""
import json

import pytest

from core.live_discovery import (
    DeviceCredential,
    available,
    live_discover,
    load_inventory,
)

HUAWEI = """\
GigabitEthernet0/0/1 has 1 neighbor(s):
Port ID        :GigabitEthernet0/0/24
System name    :Access-1
System capabilities enabled   :Bridge
Management address     :10.0.0.11
"""

CDP = """\
-------------------------
Device ID: Edge-RTR
Entry address(es):
  IP address: 10.0.0.254
Platform: cisco ISR4331,  Capabilities: Router
Interface: GigabitEthernet0/0,  Port ID (outgoing port): GigabitEthernet0/1
"""


class FakeRunner:
    """Returns canned command output per host; raises for 'unreachable' hosts."""

    def __init__(self, responses: dict[str, str]):
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def run(self, cred, command):
        self.calls.append((cred.host, command))
        if cred.host not in self.responses:
            raise ConnectionError(f"{cred.host} unreachable")
        return self.responses[cred.host]


def test_authorized_gate_blocks_by_default():
    with pytest.raises(PermissionError):
        live_discover([DeviceCredential("10.0.0.1", "admin")], runner=FakeRunner({}))


def test_neighbor_command_per_platform():
    assert DeviceCredential("h", "u", device_type="huawei").neighbor_command() == (
        "display lldp neighbor"
    )
    assert DeviceCredential("h", "u", device_type="cisco_ios").neighbor_command() == (
        "show cdp neighbors detail"
    )
    # override wins
    assert DeviceCredential("h", "u", command="custom cmd").neighbor_command() == (
        "custom cmd"
    )


def test_live_discover_builds_topology_from_reachable_devices():
    runner = FakeRunner({"10.0.0.1": HUAWEI})
    creds = [
        DeviceCredential("10.0.0.1", "admin", device_type="huawei", name="CoreSW"),
    ]
    result = live_discover(creds, runner=runner, authorized=True)
    assert result.reached == ["10.0.0.1"]
    assert not result.errors
    names = {d.name for d in result.topology.devices.values()}
    assert {"CoreSW", "Access-1"} <= names
    # the command actually sent matches the platform
    assert runner.calls == [("10.0.0.1", "display lldp neighbor")]


def test_unreachable_device_is_recorded_not_fatal():
    runner = FakeRunner({"10.0.0.1": HUAWEI})  # .2 will fail
    creds = [
        DeviceCredential("10.0.0.1", "admin", device_type="huawei", name="CoreSW"),
        DeviceCredential("10.0.0.2", "admin", device_type="cisco_ios", name="Edge"),
    ]
    result = live_discover(creds, runner=runner, authorized=True)
    assert result.reached == ["10.0.0.1"]
    assert "10.0.0.2" in result.errors
    assert result.topology.devices  # still built from the reachable one


def test_password_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("NETFORGE_SSH_PASSWORD", "s3cret")
    cred = DeviceCredential.from_dict({"host": "10.0.0.1", "username": "admin"})
    assert cred.password == "s3cret"
    # redacted() never exposes the password
    assert "password" not in cred.redacted()


def test_load_inventory(tmp_path):
    path = tmp_path / "inv.json"
    path.write_text(
        json.dumps([
            {"host": "10.0.0.1", "username": "admin", "device_type": "huawei"},
            {"host": "10.0.0.2", "username": "admin", "device_type": "cisco_ios"},
        ]),
        encoding="utf-8",
    )
    creds = load_inventory(str(path))
    assert [c.host for c in creds] == ["10.0.0.1", "10.0.0.2"]


def test_available_reflects_netmiko_presence():
    # netmiko is intentionally not a bundled dependency in this env.
    assert available() is False


def test_dashboard_live_discovery_with_fake_runner(qapp):
    from ui.dashboard import NetwrightWindow

    win = NetwrightWindow(auto_refresh=False)
    try:
        creds = [DeviceCredential("10.0.0.1", "admin", device_type="huawei",
                                  name="CoreSW")]
        result = win.discover_live(creds, runner=FakeRunner({"10.0.0.1": HUAWEI}),
                                   authorized=True)
        assert result.reached == ["10.0.0.1"]
        names = {d.name for d in win.project.topology.devices.values()}
        assert {"CoreSW", "Access-1"} <= names
    finally:
        win.deleteLater()
