"""SNMP LLDP-MIB discovery (optional, authorized use only).

Useful when SSH is locked down but SNMP read access is open. It walks the
LLDP-MIB remote-systems table and correlates the columns by index to build the
same :class:`~core.discovery.Neighbor` records the SSH/offline paths produce,
then reuses ``discovery.topology_from_neighbors`` + ``merge``.

The correlation logic works against an :class:`SnmpSession` abstraction, so it is
fully unit-tested with a fake session — the suite never sends a packet.
``pysnmp`` is imported lazily/guarded; ``available()`` reports whether it's here.

AUTHORIZED USE ONLY — read-only SNMP against devices you own or administer.
``snmp_discover`` requires an explicit ``authorized=True`` acknowledgement.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from .discovery import Neighbor, merge, topology_from_neighbors
from .live_discovery import DiscoveryResult
from .model import Topology

# LLDP-MIB object columns (walked and correlated by index).
OID_REM_PORTID = "1.0.8802.1.1.2.1.4.1.1.7"
OID_REM_SYSNAME = "1.0.8802.1.1.2.1.4.1.1.9"
OID_REM_SYSDESC = "1.0.8802.1.1.2.1.4.1.1.10"
OID_REM_CAP_ENABLED = "1.0.8802.1.1.2.1.4.1.1.12"
OID_LOC_PORTID = "1.0.8802.1.1.2.1.3.7.1.3"
OID_LOC_PORTDESC = "1.0.8802.1.1.2.1.3.7.1.4"
OID_REM_MANADDR_SUBTYPE = "1.0.8802.1.1.2.1.4.2.1.3"

# lldpRemSysCapEnabled bit order (MSB first).
_CAP_BITS = ["Other", "Repeater", "Bridge", "WLAN", "Router", "Telephone",
             "DOCSIS", "Station"]


def available() -> bool:
    try:
        import pysnmp.hlapi  # noqa: F401

        return True
    except ImportError:
        return False


def decode_caps(value) -> str:
    """Decode an lldpRemSysCapEnabled octet-string into capability words."""
    if isinstance(value, (bytes, bytearray)) and value:
        b = value[0]
        return " ".join(
            name for i, name in enumerate(_CAP_BITS) if b & (0x80 >> i)
        )
    return str(value or "")


@dataclass
class SnmpCredential:
    host: str
    community: str = "public"
    version: str = "2c"
    port: int = 161
    name: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "SnmpCredential":
        return cls(
            host=d["host"],
            community=d.get("community") or os.environ.get(
                "NETFORGE_SNMP_COMMUNITY", "public"
            ),
            version=str(d.get("version", "2c")),
            port=int(d.get("port", 161)),
            name=d.get("name"),
        )

    def redacted(self) -> dict:
        return {"host": self.host, "version": self.version,
                "name": self.name or self.host}


class SnmpSession(Protocol):
    """Walk an OID subtree, returning ``{index_suffix: value}``."""

    def walk(self, oid: str) -> dict[str, object]: ...


def lldp_neighbors_from_session(session: SnmpSession) -> list[Neighbor]:
    """Correlate LLDP-MIB columns by index into Neighbor records (pure logic)."""
    names = session.walk(OID_REM_SYSNAME)
    portids = session.walk(OID_REM_PORTID)
    descs = session.walk(OID_REM_SYSDESC)
    caps = session.walk(OID_REM_CAP_ENABLED)
    locports = session.walk(OID_LOC_PORTID) or session.walk(OID_LOC_PORTDESC)
    mgmt = _mgmt_addresses(session.walk(OID_REM_MANADDR_SUBTYPE))

    out: list[Neighbor] = []
    for idx, name in names.items():
        parts = idx.split(".")
        # Remote-table index is timeMark.localPortNum.remIndex.
        loc_port_num = parts[1] if len(parts) >= 2 else ""
        local_intf = str(locports.get(loc_port_num, loc_port_num))
        out.append(
            Neighbor(
                local_intf=local_intf,
                neighbor_name=str(name),
                neighbor_intf=str(portids.get(idx, "")),
                platform=str(descs.get(idx, "")),
                mgmt_ip=mgmt.get(idx),
                capabilities=decode_caps(caps.get(idx, "")),
            )
        )
    return out


def _mgmt_addresses(manaddr: dict[str, object]) -> dict[str, str]:
    """Extract IPv4 mgmt addresses from lldpRemManAddr index encoding.

    Index: timeMark.localPortNum.remIndex.addrSubtype.addrLen.<addr octets>.
    addrSubtype 1 + addrLen 4 => the next four octets are the IPv4 address.
    """
    result: dict[str, str] = {}
    for idx in manaddr:
        parts = idx.split(".")
        if len(parts) >= 9 and parts[3] == "1" and parts[4] == "4":
            prefix = ".".join(parts[:3])          # ties back to the neighbor row
            result[prefix] = ".".join(parts[5:9])  # the IPv4 address
    return result


class PysnmpSession:
    """Real SNMPv2c transport (best-effort; imported lazily)."""

    def __init__(self, cred: SnmpCredential, timeout: int = 3, retries: int = 1):
        self.cred = cred
        self.timeout = timeout
        self.retries = retries

    def walk(self, oid: str) -> dict[str, object]:  # pragma: no cover - needs pysnmp
        from pysnmp.hlapi import (
            CommunityData,
            ContextData,
            ObjectIdentity,
            ObjectType,
            SnmpEngine,
            UdpTransportTarget,
            nextCmd,
        )

        result: dict[str, object] = {}
        it = nextCmd(
            SnmpEngine(),
            CommunityData(self.cred.community, mpModel=1),
            UdpTransportTarget(
                (self.cred.host, self.cred.port),
                timeout=self.timeout,
                retries=self.retries,
            ),
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
            lexicographicMode=False,
        )
        for err_ind, err_stat, _err_idx, var_binds in it:
            if err_ind or err_stat:
                break
            for name, val in var_binds:
                s = str(name)
                if not s.startswith(oid):
                    continue
                suffix = s[len(oid):].lstrip(".")
                try:
                    result[suffix] = val.asOctets()
                except AttributeError:
                    result[suffix] = val.prettyPrint()
        return result


def snmp_discover(
    creds: list[SnmpCredential],
    session_factory=None,
    name: str = "Discovered",
    authorized: bool = False,
) -> DiscoveryResult:
    """Walk each device's LLDP-MIB over SNMP and build a merged topology."""
    if not authorized:
        raise PermissionError(
            "snmp_discover reads from real devices; pass authorized=True to "
            "confirm you are authorized to query them."
        )
    factory = session_factory or (lambda c: PysnmpSession(c))
    topology = Topology(name=name)
    reached: list[str] = []
    errors: dict[str, str] = {}
    for cred in creds:
        try:
            session = factory(cred)
            neighbors = lldp_neighbors_from_session(session)
            merge(topology, topology_from_neighbors(cred.name or cred.host, neighbors))
            reached.append(cred.host)
        except Exception as exc:  # noqa: BLE001 - report, never abort the run
            errors[cred.host] = str(exc)
    _grid_layout(topology)
    return DiscoveryResult(topology=topology, reached=reached, errors=errors)


def load_inventory(path: str) -> list[SnmpCredential]:
    import json

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "devices" in data:
        data = data["devices"]
    if not isinstance(data, list):
        raise ValueError("inventory must be a JSON list of device objects")
    return [SnmpCredential.from_dict(d) for d in data]


def _grid_layout(topology: Topology, cols: int = 4, step: int = 160) -> None:
    for i, dev in enumerate(topology.devices.values()):
        dev.x = float((i % cols) * step)
        dev.y = float((i // cols) * step)
