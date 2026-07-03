"""Live topology discovery over SSH (optional, authorized use only).

Unlike :mod:`core.discovery` (which parses text you paste), this module can
connect to devices and fetch their neighbor tables itself. It does so by reusing
the offline parsers: SSH simply automates running ``display lldp neighbor`` /
``show cdp neighbors detail`` and hands the output to
``discovery.discover_from_texts``. No new parsing, no scanning/probing — it logs
in and reads, exactly as an operator would.

AUTHORIZED USE ONLY. This connects to real infrastructure. Only run it against
devices you own or are explicitly authorized to administer. ``live_discover``
requires an explicit ``authorized=True`` acknowledgement.

``netmiko`` is an optional dependency and is imported lazily/guarded, so the app
runs without it (``available()`` reports False). Tests inject a fake runner, so
the suite never touches a real network.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

from .discovery import discover_from_texts
from .model import Topology

# netmiko device_type -> the neighbor command to run on that platform.
PLATFORM_COMMANDS = {
    "huawei": "display lldp neighbor",
    "huawei_vrpv8": "display lldp neighbor",
    "hp_comware": "display lldp neighbor-information",
    "cisco_ios": "show cdp neighbors detail",
    "cisco_xe": "show cdp neighbors detail",
    "cisco_nxos": "show cdp neighbors detail",
    "cisco_xr": "show lldp neighbors detail",
    "arista_eos": "show lldp neighbors detail",
    "juniper_junos": "show lldp neighbors",
    "generic": "show lldp neighbors detail",
}
DEFAULT_COMMAND = "show lldp neighbors detail"


def available() -> bool:
    """True if the optional ``netmiko`` transport is importable."""
    try:
        import netmiko  # noqa: F401

        return True
    except ImportError:
        return False


@dataclass
class DeviceCredential:
    host: str
    username: str
    password: str = ""
    device_type: str = "huawei"  # netmiko-style platform key
    port: int = 22
    name: str | None = None       # display name; defaults to host
    command: str | None = None    # override the neighbor command

    def neighbor_command(self) -> str:
        return self.command or PLATFORM_COMMANDS.get(self.device_type, DEFAULT_COMMAND)

    @classmethod
    def from_dict(cls, d: dict) -> "DeviceCredential":
        return cls(
            host=d["host"],
            username=d.get("username", ""),
            password=d.get("password", "") or os.environ.get("NETFORGE_SSH_PASSWORD", ""),
            device_type=d.get("device_type", "huawei"),
            port=int(d.get("port", 22)),
            name=d.get("name"),
            command=d.get("command"),
        )

    def redacted(self) -> dict:
        return {"host": self.host, "username": self.username,
                "device_type": self.device_type, "name": self.name or self.host}


@dataclass
class DiscoveryResult:
    topology: Topology
    reached: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)  # host -> error message


class CommandRunner(Protocol):
    """Fetch the output of ``command`` from a device (real SSH or a test fake)."""

    def run(self, cred: DeviceCredential, command: str) -> str: ...


class NetmikoRunner:
    """Real SSH transport. Imports netmiko lazily so the app runs without it."""

    def __init__(self, timeout: int = 20, read_timeout: int = 30) -> None:
        self.timeout = timeout
        self.read_timeout = read_timeout

    def run(self, cred: DeviceCredential, command: str) -> str:
        try:
            from netmiko import ConnectHandler
        except ImportError as exc:  # pragma: no cover - env without netmiko
            raise RuntimeError(
                "netmiko is not installed. Run: pip install netmiko"
            ) from exc
        conn = ConnectHandler(
            device_type=cred.device_type,
            host=cred.host,
            username=cred.username,
            password=cred.password,
            port=cred.port,
            conn_timeout=self.timeout,
        )
        try:
            return conn.send_command(command, read_timeout=self.read_timeout)
        finally:
            conn.disconnect()


def live_discover(
    creds: list[DeviceCredential],
    runner: CommandRunner | None = None,
    name: str = "Discovered",
    authorized: bool = False,
) -> DiscoveryResult:
    """Connect to each device, fetch its neighbors, and build a merged topology.

    Per-device failures are recorded in ``result.errors`` and skipped, so one
    unreachable device does not abort the whole run. Pass ``authorized=True`` to
    confirm you may connect to these devices.
    """
    if not authorized:
        raise PermissionError(
            "live_discover connects to real devices; pass authorized=True to "
            "confirm you are authorized to manage them."
        )
    runner = runner or NetmikoRunner()
    sources: list[tuple[str, str]] = []
    reached: list[str] = []
    errors: dict[str, str] = {}
    for cred in creds:
        try:
            text = runner.run(cred, cred.neighbor_command())
            sources.append((cred.name or cred.host, text))
            reached.append(cred.host)
        except Exception as exc:  # noqa: BLE001 - report, never abort the run
            errors[cred.host] = str(exc)
    topology = (
        discover_from_texts(sources, name=name) if sources else Topology(name=name)
    )
    return DiscoveryResult(topology=topology, reached=reached, errors=errors)


def load_inventory(path: str) -> list[DeviceCredential]:
    """Load a JSON inventory: a list of {host, username, password?, device_type?}."""
    import json

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "devices" in data:
        data = data["devices"]
    if not isinstance(data, list):
        raise ValueError("inventory must be a JSON list of device objects")
    return [DeviceCredential.from_dict(d) for d in data]
