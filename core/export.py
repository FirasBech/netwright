"""Export the topology to text-first artifacts (unit-testable without Qt or AI).

- ``export_json``   — the project JSON.
- ``export_policy`` — a deny-by-default ``vlan_policy.json`` in SecureLink's exact
                      shape (string keys), derived from ``permit`` ACL rules.
                      This is the interop artifact SecureLink's guard consumes.
- ``export_svg``    — hand-rolled SVG text (no SVG library).
- ``export_png``    — renders a ui-supplied QGraphicsScene; kept behind a guard so
                      ``core`` stays Qt-free.
"""
from __future__ import annotations

import ipaddress
import json
from pathlib import Path

from .model import Topology
from .policy import VlanPolicyEngine

# Color-blind-aware palette, assigned by VLAN id.
_PALETTE = [
    "#38bdf8", "#34d399", "#fbbf24", "#f87171", "#a78bfa",
    "#f472b6", "#2dd4bf", "#fb923c", "#a3e635", "#60a5fa",
]


def vlan_color(vlan_id: int) -> str:
    return _PALETTE[vlan_id % len(_PALETTE)]


def _fill_for_vlan(topology: Topology, vlan_id: int) -> str:
    """The color used for a VLAN in BOTH nodes and the legend (kept consistent)."""
    vlan = topology.vlans.get(vlan_id)
    return (vlan.color if vlan and vlan.color else vlan_color(vlan_id))


def _netmask_for_ip(topology: Topology, ip: str) -> str:
    """Dotted netmask of the VLAN subnet containing ``ip``; default /24."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "255.255.255.0"
    for vlan in topology.vlans.values():
        if not vlan.subnet:
            continue
        try:
            net = ipaddress.ip_network(vlan.subnet, strict=False)
        except ValueError:
            continue
        if addr in net:
            return str(net.netmask)
    return "255.255.255.0"


def policy_map(topology: Topology) -> dict[str, list[int]]:
    """Build the deny-by-default source->[dest] map from permit ACL rules."""
    engine = VlanPolicyEngine()
    raw: dict[int, set[int]] = {}
    for acl in topology.acls:
        if acl.action == "permit":
            raw.setdefault(acl.src_vlan, set()).add(acl.dst_vlan)
    engine = VlanPolicyEngine({src: sorted(d) for src, d in raw.items()})
    return engine.as_dict()


def export_json(topology: Topology, path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(topology.to_dict(), fh, indent=2, sort_keys=True)


def export_policy(topology: Topology, path: str | Path) -> dict[str, list[int]]:
    """Write a SecureLink-compatible ``vlan_policy.json`` and return the map."""
    mapping = policy_map(topology)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(mapping, fh, indent=2, sort_keys=True)
    return mapping


def export_svg(topology: Topology) -> str:
    """Return a standalone SVG string (no external SVG library)."""
    width, height = 1000, 700
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#0f172a"/>',
    ]

    # Links first (drawn under nodes).
    for lk in topology.links.values():
        a = topology.devices.get(lk.a_device)
        b = topology.devices.get(lk.b_device)
        if not a or not b:
            continue
        parts.append(
            f'<line x1="{a.x:.0f}" y1="{a.y:.0f}" x2="{b.x:.0f}" '
            f'y2="{b.y:.0f}" stroke="#334155" stroke-width="2"/>'
        )

    # Devices.
    for dev in topology.devices.values():
        primary = next(
            (p.access_vlan for p in dev.ports if p.access_vlan), None
        )
        fill = _fill_for_vlan(topology, primary) if primary else "#1e293b"
        parts.append(
            f'<rect x="{dev.x - 30:.0f}" y="{dev.y - 18:.0f}" width="60" '
            f'height="36" rx="6" fill="{fill}" stroke="#e2e8f0"/>'
        )
        parts.append(
            f'<text x="{dev.x:.0f}" y="{dev.y + 4:.0f}" fill="#0f172a" '
            f'font-family="sans-serif" font-size="11" '
            f'text-anchor="middle">{_xml_escape(dev.name)}</text>'
        )

    # VLAN legend.
    y = 24
    for vid in sorted(topology.vlans):
        vlan = topology.vlans[vid]
        parts.append(
            f'<rect x="16" y="{y - 10}" width="12" height="12" '
            f'fill="{_fill_for_vlan(topology, vid)}"/>'
        )
        parts.append(
            f'<text x="34" y="{y}" fill="#e2e8f0" font-family="sans-serif" '
            f'font-size="12">V{vid} {_xml_escape(vlan.name)}</text>'
        )
        y += 20

    parts.append("</svg>")
    return "\n".join(parts)


def export_device_cli(topology: Topology, device_id: str, dialect: str = "ios") -> str:
    """Emit paste-ready, **SIMULATED** Cisco-IOS-style config for one device.

    This is a template generated from the design; it is NOT validated against
    real hardware. Review before applying anywhere. Only the ``ios`` dialect is
    implemented.
    """
    dev = topology.devices.get(device_id)
    if dev is None:
        raise KeyError(device_id)
    if dialect != "ios":
        raise ValueError(f"unsupported dialect: {dialect}")

    lines = [
        "! ---------------------------------------------------------------",
        "! SIMULATED configuration generated by Netwright — UNVERIFIED.",
        "! Review carefully before applying to any device.",
        "! ---------------------------------------------------------------",
        f"hostname {dev.name.replace(' ', '-')}",
        "!",
    ]

    # VLAN database (only VLANs this device actually uses).
    used: set[int] = set()
    for port in dev.ports:
        if port.access_vlan:
            used.add(port.access_vlan)
        used.update(port.allowed_vlans)
        if port.native_vlan:
            used.add(port.native_vlan)
    for vid in sorted(used):
        vlan = topology.vlans.get(vid)
        lines.append(f"vlan {vid}")
        if vlan and vlan.name:
            lines.append(f" name {vlan.name.replace(' ', '_')}")
    if used:
        lines.append("!")

    if dev.mgmt_ip:
        lines += [
            "interface Vlan1",
            f" ip address {dev.mgmt_ip} {_netmask_for_ip(topology, dev.mgmt_ip)}",
            " no shutdown",
            "!",
        ]

    # SVIs for VLAN gateways this device uses (router/L3-switch style).
    for vid in sorted(used):
        vlan = topology.vlans.get(vid)
        if vlan and vlan.gateway:
            lines += [
                f"interface Vlan{vid}",
                f" ip address {vlan.gateway} {_netmask_for_ip(topology, vlan.gateway)}",
                "!",
            ]

    for port in dev.ports:
        lines.append(f"interface {port.name}")
        if port.mode == "access":
            lines.append(" switchport mode access")
            if port.access_vlan:
                lines.append(f" switchport access vlan {port.access_vlan}")
        elif port.mode == "trunk":
            lines.append(" switchport mode trunk")
            if port.native_vlan:
                lines.append(f" switchport trunk native vlan {port.native_vlan}")
            if port.allowed_vlans:
                allowed = ",".join(str(v) for v in sorted(port.allowed_vlans))
                lines.append(f" switchport trunk allowed vlan {allowed}")
        elif port.mode == "routed":
            lines.append(" no switchport")
            if port.ip:
                lines.append(f" ip address {port.ip} {_netmask_for_ip(topology, port.ip)}")
        lines.append("!")

    return "\n".join(lines) + "\n"


def export_png(topology: Topology, scene, path: str | Path) -> bool:
    """Render a ui-supplied QGraphicsScene to PNG at 2x. Returns success.

    Imports PyQt5 lazily inside the function so ``core`` never imports Qt at
    module load. Safe to call without Qt — returns False instead of raising.
    """
    try:  # pragma: no cover - exercised only when PyQt5 + a scene are present
        from PyQt5.QtCore import QSize
        from PyQt5.QtGui import QImage, QPainter

        rect = scene.itemsBoundingRect()
        size = QSize(int(rect.width() * 2) or 2, int(rect.height() * 2) or 2)
        image = QImage(size, QImage.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        scene.render(painter, target=image.rect(), source=rect)
        painter.end()
        return bool(image.save(str(path), "PNG"))
    except Exception:
        return False


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
