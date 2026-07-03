"""Topology canvas — QGraphicsView/Scene with model/view separation.

The plain-data ``core.model.Topology`` stays the source of truth; the scene
builds ``DeviceItem``/``LinkItem`` graphics from it and keeps links routed as
nodes move. Keeping the model headless makes ``core`` testable without Qt.

Interaction handled here: drag-drop from the palette (drop sink), a simple
two-click link-draw mode, rubber-band select, wheel zoom, and a VLAN color
overlay with a legend. Structural mutations are delegated to the dashboard's
single undo path via sink callbacks, never applied to the model directly here.
"""
from __future__ import annotations

import os

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from core.export import vlan_color
from core.model import Topology
from .theme import PALETTE

GRID = 20
NODE_W, NODE_H = 96, 44
MIME_DEVICE = "application/x-netwright-device"

_ASSETS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "devices"
)
_RENDERERS: dict = {}


def glyph_renderer(kind: str):
    """A cached QSvgRenderer for a device kind, or None if unavailable."""
    if kind not in _RENDERERS:
        renderer = None
        try:
            from PyQt5.QtSvg import QSvgRenderer

            path = os.path.join(_ASSETS, f"{kind}.svg")
            if os.path.exists(path):
                candidate = QSvgRenderer(path)
                if candidate.isValid():
                    renderer = candidate
        except ImportError:  # QtSvg missing: plain rects still work
            renderer = None
        _RENDERERS[kind] = renderer
    return _RENDERERS[kind]


def primary_vlan(device) -> int | None:
    return next((p.access_vlan for p in device.ports if p.access_vlan), None)


class DeviceItem(QGraphicsObject):
    """A draggable device node. Notifies the scene to reroute links on move."""

    def __init__(self, device_id: str, name: str, kind: str, overlay_color: str):
        super().__init__()
        self.device_id = device_id
        self.kind = kind
        self._color = overlay_color
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsScenePositionChanges
        )
        self.setCacheMode(QGraphicsItem.DeviceCoordinateCache)
        self._press: tuple[float, float] | None = None
        self._label = QGraphicsSimpleTextItem(name, self)
        self._label.setBrush(QBrush(QColor(PALETTE["bg"])))
        # Leave room for the glyph chip on the left.
        self._label.setPos(-NODE_W / 2 + 40, -7)

    def boundingRect(self) -> QRectF:
        return QRectF(-NODE_W / 2, -NODE_H / 2, NODE_W, NODE_H)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(QBrush(QColor(self._color)))
        pen = QPen(QColor(PALETTE["accent"] if self.isSelected() else PALETTE["text"]))
        pen.setWidthF(2.0 if self.isSelected() else 1.5)
        painter.setPen(pen)
        painter.drawRoundedRect(self.boundingRect(), 8, 8)
        renderer = glyph_renderer(self.kind)
        if renderer is not None:
            renderer.render(
                painter, QRectF(-NODE_W / 2 + 6, -NODE_H / 2 + 8, 28, 28)
            )

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemScenePositionHasChanged and self.scene():
            # Graphics-only during an interactive drag; the model is committed
            # once, undoably, on mouse-release.
            self.scene().reroute_links(self.device_id, write_model=False)
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        self._press = (self.pos().x(), self.pos().y())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self._press is None:
            return
        new = (self.pos().x(), self.pos().y())
        if new != self._press and self.scene():
            self.scene().emit_move(self.device_id, self._press, new)
        self._press = None


class LinkItem(QGraphicsPathItem):
    def __init__(self, link_id: str, trunk: bool = False):
        super().__init__()
        self.link_id = link_id
        pen = QPen(QColor(PALETTE["border"]), 2)
        if trunk:
            pen.setStyle(Qt.DashLine)
        self.setPen(pen)
        self.setZValue(-1)  # below nodes

    def route(self, a: QPointF, b: QPointF) -> None:
        path = QPainterPath(a)
        path.lineTo(b)
        self.setPath(path)


class TopologyScene(QGraphicsScene):
    def __init__(self, topology: Topology | None = None):
        super().__init__()
        self.topology = topology or Topology()
        self.device_items: dict[str, DeviceItem] = {}
        self.link_items: dict[str, LinkItem] = {}
        self.vlan_overlay = True
        self._move_sink = None  # (device_id, (old_x,old_y), (new_x,new_y)) -> None
        self.setBackgroundBrush(QBrush(QColor(PALETTE["bg"])))

    def set_topology(self, topology: Topology) -> None:
        self.topology = topology
        self.sync()

    def sync(self) -> None:
        """Rebuild all graphics items from the model."""
        self.clear()
        self.device_items.clear()
        self.link_items.clear()
        for dev in self.topology.devices.values():
            primary = primary_vlan(dev)
            color = (
                vlan_color(primary)
                if (primary and self.vlan_overlay)
                else PALETTE["panel"]
            )
            item = DeviceItem(dev.id, dev.name, dev.kind, color)
            item.setPos(dev.x, dev.y)
            self.addItem(item)
            self.device_items[dev.id] = item
        for lk in self.topology.links.values():
            link_item = LinkItem(lk.id, lk.kind == "trunk")
            self.addItem(link_item)
            self.link_items[lk.id] = link_item
            self._route(lk.id)

    def _route(self, link_id: str) -> None:
        lk = self.topology.links.get(link_id)
        item = self.link_items.get(link_id)
        if not lk or not item:
            return
        a = self.device_items.get(lk.a_device)
        b = self.device_items.get(lk.b_device)
        if a and b:
            item.route(a.pos(), b.pos())

    def reroute_links(self, device_id: str, write_model: bool = False) -> None:
        if write_model:
            item = self.device_items.get(device_id)
            dev = self.topology.devices.get(device_id)
            if item and dev:
                dev.x, dev.y = item.pos().x(), item.pos().y()
        for lid, lk in self.topology.links.items():
            if device_id in (lk.a_device, lk.b_device):
                self._route(lid)

    def move_device(self, device_id: str, x: float, y: float) -> None:
        """Programmatic move: updates graphics AND the model (used by tests)."""
        item = self.device_items.get(device_id)
        dev = self.topology.devices.get(device_id)
        if item:
            item.setPos(x, y)  # itemChange reroutes graphics only
        if dev:
            dev.x, dev.y = x, y

    def set_move_sink(self, fn) -> None:
        self._move_sink = fn

    def emit_move(self, device_id, old_pos, new_pos) -> None:
        if self._move_sink:
            self._move_sink(device_id, old_pos, new_pos)

    def selected_device_id(self) -> str | None:
        for item in self.selectedItems():
            if isinstance(item, DeviceItem):
                return item.device_id
        return None

    def selected_link_id(self) -> str | None:
        for item in self.selectedItems():
            if isinstance(item, LinkItem):
                return item.link_id
        return None

    def legend_entries(self) -> list[tuple[str, str]]:
        """(color, label) per VLAN, for the overlay legend."""
        out = []
        for vid in sorted(self.topology.vlans):
            vlan = self.topology.vlans[vid]
            out.append((vlan.color or vlan_color(vid), f"V{vid} {vlan.name}"))
        return out


class TopologyView(QGraphicsView):
    def __init__(self, scene: TopologyScene):
        super().__init__(scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setAcceptDrops(True)
        self._zoom = 1.0
        self.link_mode = False
        self._link_start: str | None = None
        self._drop_sink = None  # (kind, x, y) -> None
        self._link_sink = None  # (a_device, b_device) -> None

    # ---- external wiring ---------------------------------------------------
    def set_drop_sink(self, fn) -> None:
        self._drop_sink = fn

    def set_link_sink(self, fn) -> None:
        self._link_sink = fn

    def set_link_mode(self, on: bool) -> None:
        self.link_mode = on
        self._link_start = None
        self.setDragMode(
            QGraphicsView.NoDrag if on else QGraphicsView.RubberBandDrag
        )

    # ---- drag & drop from the palette --------------------------------------
    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(MIME_DEVICE) or event.mimeData().hasText():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        kind = event.mimeData().text() or "switch"
        pos = self.mapToScene(event.pos())
        x = round(pos.x() / GRID) * GRID
        y = round(pos.y() / GRID) * GRID
        if self._drop_sink:
            self._drop_sink(kind, float(x), float(y))
        event.acceptProposedAction()

    # ---- link-draw mode ----------------------------------------------------
    def mousePressEvent(self, event):
        if self.link_mode and event.button() == Qt.LeftButton:
            scene: TopologyScene = self.scene()
            item = self.itemAt(event.pos())
            dev_id = getattr(item, "device_id", None)
            if dev_id is None and item is not None:
                parent = item.parentItem()
                dev_id = getattr(parent, "device_id", None)
            if dev_id is not None:
                if self._link_start is None:
                    self._link_start = dev_id
                elif self._link_start != dev_id and self._link_sink:
                    self._link_sink(self._link_start, dev_id)
                    self._link_start = None
                return
        super().mousePressEvent(event)

    def fit_to_view(self) -> None:
        scene: TopologyScene = self.scene()
        if scene and scene.device_items:
            rect = scene.itemsBoundingRect()
            rect.adjust(-40, -40, 40, 40)
            self.fitInView(rect, Qt.KeepAspectRatio)
            self._zoom = self.transform().m11()

    def wheelEvent(self, event):
        factor = 1.0015 ** event.angleDelta().y()
        new_zoom = max(0.2, min(4.0, self._zoom * factor))
        factor = new_zoom / self._zoom
        self._zoom = new_zoom
        self.scale(factor, factor)

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        left = int(rect.left()) - (int(rect.left()) % GRID)
        top = int(rect.top()) - (int(rect.top()) % GRID)
        painter.setPen(QPen(QColor(PALETTE["grid_minor"]), 0))
        x = left
        while x < rect.right():
            painter.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))
            x += GRID
        y = top
        while y < rect.bottom():
            painter.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))
            y += GRID
