"""Topology discovery from device neighbor output (LLDP / CDP).

Netwright does not scan the network or talk to devices. Instead you run the
neighbor command on each device and give Netwright the text — the same workflow
as reading ``display lldp neighbor`` on a Huawei box — and it reconstructs the
topology: devices, the links between them, and each neighbor's detected model,
management IP, and role.

Supported inputs (auto-detected):
- Huawei LLDP    — ``display lldp neighbor`` (detailed) and ``... brief``
- Cisco CDP      — ``show cdp neighbors detail``
- Standard LLDP  — ``show lldp neighbors detail`` (Cisco/Arista/generic)

Each text block is one device's view. Feed several (one per device) and they are
merged: devices dedup by name, links dedup by endpoint pair, so the reciprocal
A→B / B→A neighbor entries collapse into a single link.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .ids import new_id
from .model import Device, Link, Port, Topology


@dataclass
class Neighbor:
    local_intf: str
    neighbor_name: str
    neighbor_intf: str = ""
    platform: str = ""          # model / system description
    mgmt_ip: str | None = None
    capabilities: str = ""
    kind: str = "switch"

    def __post_init__(self):
        self.kind = kind_from(self.capabilities, self.platform)


# --------------------------------------------------------------------------
# Capability / platform -> device kind
# --------------------------------------------------------------------------
def kind_from(capabilities: str, platform: str = "") -> str:
    c = f" {capabilities} ".lower()
    p = platform.lower()
    if "server" in p:
        return "server"
    if "firewall" in p or "asa" in p or "usg" in p or "palo" in p or "fortigate" in p:
        return "firewall"
    if "wlan" in c or " ap " in c or re.search(r"\bw\b", c) or "access point" in p:
        return "ap"
    if "phone" in c or "telephone" in c or " t " in c:
        return "host"
    if "station" in c or "host" in c or re.search(r"\bs\b", c):
        return "host"
    has_router = "router" in c or re.search(r"\br\b", c)
    has_switch = "switch" in c or "bridge" in c or re.search(r"\bb\b", c)
    if has_router and not has_switch:
        return "router"
    if has_switch or has_router:  # L3 switch (bridge+router) reads as switch
        return "switch"
    return "switch"


def _slug(name: str) -> str:
    short = name.strip().split(".")[0]
    slug = re.sub(r"[^a-z0-9]+", "-", short.lower()).strip("-")
    return slug or "device"


def _first(pattern: str, text: str, flags=0) -> str:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else ""


# --------------------------------------------------------------------------
# Format detection + dispatch
# --------------------------------------------------------------------------
def detect_format(text: str) -> str:
    t = text.lower()
    if "show cdp" in t or ("device id:" in t and "platform:" in t):
        return "cdp"
    # Huawei detailed output has per-interface "<intf> has N neighbor(s):" headers.
    if re.search(r"has\s+\d+\s+neighbor", t):
        return "huawei_lldp"
    if re.search(r"neighbor\s+dev", t):  # "display lldp neighbor brief" table
        return "huawei_brief"
    if "display lldp" in t or "vrp" in t:
        return "huawei_lldp"
    return "lldp_std"  # "show lldp neighbors detail" and generic


def parse_neighbors(text: str, fmt: str = "auto") -> list[Neighbor]:
    if fmt == "auto":
        fmt = detect_format(text)
    return {
        "cdp": _parse_cdp,
        "huawei_lldp": _parse_huawei_detailed,
        "huawei_brief": _parse_huawei_brief,
        "lldp_std": _parse_lldp_std,
    }.get(fmt, _parse_lldp_std)(text)


# --------------------------------------------------------------------------
# Parsers
# --------------------------------------------------------------------------
def _parse_cdp(text: str) -> list[Neighbor]:
    out: list[Neighbor] = []
    blocks = re.split(r"-{4,}", text)
    for block in blocks:
        if "Device ID:" not in block:
            continue
        name = _first(r"Device ID:\s*(.+)", block)
        if not name:
            continue
        out.append(
            Neighbor(
                local_intf=_first(r"Interface:\s*([^\s,]+)", block),
                neighbor_name=name,
                neighbor_intf=_first(r"Port ID \(outgoing port\):\s*(.+)", block),
                platform=_first(r"Platform:\s*([^,\n]+)", block),
                mgmt_ip=_first(r"IP(?:v4)? address:\s*([\d.]+)", block) or None,
                capabilities=_first(r"Capabilities:\s*([^\n]+)", block),
            )
        )
    return out


def _parse_lldp_std(text: str) -> list[Neighbor]:
    out: list[Neighbor] = []
    # Blocks begin at "Local Intf:"; fall back to whole text as one block.
    parts = re.split(r"(?im)^-{4,}\s*$|(?=^Local Intf:)", text)
    for block in parts:
        if "System Name" not in block and "Port id" not in block:
            continue
        name = _first(r"System Name:\s*(.+)", block)
        if not name:
            continue
        desc = _first(r"System Description:\s*\n?\s*(.+)", block)
        out.append(
            Neighbor(
                local_intf=_first(r"Local Intf:\s*(\S+)", block),
                neighbor_name=name,
                neighbor_intf=_first(r"Port id:\s*(\S+)", block),
                platform=desc,
                mgmt_ip=_first(r"(?:IP|Management Address(?:es)?):\s*([\d.]+)", block)
                or None,
                capabilities=_first(r"System Capabilities:\s*(.+)", block),
            )
        )
    return out


def _parse_huawei_detailed(text: str) -> list[Neighbor]:
    out: list[Neighbor] = []
    # Each neighbor section is introduced by "<intf> has N neighbor(s):".
    headers = list(re.finditer(r"(?im)^(\S+)\s+has\s+\d+\s+neighbor", text))
    for idx, hdr in enumerate(headers):
        local_intf = hdr.group(1)
        start = hdr.end()
        end = headers[idx + 1].start() if idx + 1 < len(headers) else len(text)
        block = text[start:end]
        name = _first(r"System name\s*:\s*(.+)", block)
        if not name:
            continue
        out.append(
            Neighbor(
                local_intf=local_intf,
                neighbor_name=name,
                neighbor_intf=_first(r"Port ID\s*:\s*(.+)", block),
                platform=_first(r"System description\s*:\s*\n?\s*(.+)", block),
                mgmt_ip=_first(r"Management address\s*:\s*([\d.]+)", block) or None,
                capabilities=_first(r"System capabilities enabled\s*:\s*(.+)", block)
                or _first(r"System capabilities supported\s*:\s*(.+)", block),
            )
        )
    return out


def _parse_huawei_brief(text: str) -> list[Neighbor]:
    out: list[Neighbor] = []
    for line in text.splitlines():
        cols = line.split()
        # Local Intf | Neighbor Dev | Neighbor Intf | Exptime
        if len(cols) >= 4 and re.match(r"(?i)(ge|xge|eth|gi|10ge|40ge)", cols[0]):
            out.append(
                Neighbor(
                    local_intf=cols[0],
                    neighbor_name=cols[1],
                    neighbor_intf=cols[2],
                    capabilities="bridge",  # brief output has no caps
                )
            )
    return out


# --------------------------------------------------------------------------
# Build + merge into a Topology
# --------------------------------------------------------------------------
def _ensure_port(device: Device, intf: str) -> str:
    intf = intf or "unknown"
    if not any(p.id == intf for p in device.ports):
        device.ports.append(Port(intf, intf, mode="trunk"))
    return intf


def topology_from_neighbors(
    local_name: str, neighbors: list[Neighbor], local_kind: str = "switch"
) -> Topology:
    t = Topology(name=local_name)
    local = Device(_slug(local_name), local_name, local_kind,
                   metadata={"discovered": True})
    t.add_device(local)
    for nb in neighbors:
        nid = _slug(nb.neighbor_name)
        dev = t.devices.get(nid)
        if dev is None:
            meta = {"discovered": True}
            if nb.platform:
                meta["model"] = nb.platform
            if nb.capabilities:
                meta["capabilities"] = nb.capabilities
            dev = Device(nid, nb.neighbor_name, nb.kind, mgmt_ip=nb.mgmt_ip,
                         metadata=meta)
            t.add_device(dev)
        a_port = _ensure_port(local, nb.local_intf)
        b_port = _ensure_port(dev, nb.neighbor_intf)
        t.add_link(Link(new_id("ln"), local.id, a_port, dev.id, b_port, kind="trunk"))
    return t


def _link_key(link: Link) -> frozenset:
    return frozenset(
        {(link.a_device, link.a_port), (link.b_device, link.b_port)}
    )


def merge(base: Topology, other: Topology) -> Topology:
    """Union devices (by id) and links (by endpoint pair) into ``base``."""
    for dev in other.devices.values():
        existing = base.devices.get(dev.id)
        if existing is None:
            base.add_device(dev)
        else:
            existing.mgmt_ip = existing.mgmt_ip or dev.mgmt_ip
            if dev.kind != "switch" and existing.kind == "switch":
                existing.kind = dev.kind  # a more specific role wins
            for k, v in dev.metadata.items():
                existing.metadata.setdefault(k, v)
            for port in dev.ports:
                _ensure_port(existing, port.id)
    seen = {_link_key(lk) for lk in base.links.values()}
    for lk in other.links.values():
        if _link_key(lk) not in seen:
            base.add_link(lk)
            seen.add(_link_key(lk))
    return base


def _layout(topology: Topology, cols: int = 4, step: int = 160) -> None:
    for i, dev in enumerate(topology.devices.values()):
        dev.x = float((i % cols) * step)
        dev.y = float((i // cols) * step)


def discover_from_texts(
    sources: list[tuple[str, str]], fmt: str = "auto", name: str = "Discovered"
) -> Topology:
    """Build one topology from several (local_name, text) neighbor dumps."""
    result = Topology(name=name)
    for local_name, text in sources:
        neighbors = parse_neighbors(text, fmt)
        merge(result, topology_from_neighbors(local_name, neighbors))
    result.name = name
    _layout(result)
    return result


def discover_from_files(
    paths: list[str], fmt: str = "auto", name: str = "Discovered"
) -> Topology:
    """Each file is one device's neighbor output; the file stem names that device."""
    sources: list[tuple[str, str]] = []
    for p in paths:
        path = Path(p)
        text = path.read_text(encoding="utf-8", errors="replace")
        sources.append((path.stem, text))
    return discover_from_texts(sources, fmt, name)
