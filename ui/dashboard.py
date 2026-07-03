"""Netwright dashboard — QMainWindow with palette, canvas, properties + AI docks.

Construct ``NetwrightWindow(auto_refresh=False)`` to build every panel WITHOUT
showing it, so the UI is testable under ``QT_QPA_PLATFORM=offscreen``. Every
structural edit — palette drop, link draw, properties edit, CLI-equivalent
action, and an applied AI proposal — flows through one undo path
(``_apply`` -> ``QUndoStack``), so undo/redo and AI edits are uniform and the
scene re-syncs from the model on every change.
"""
from __future__ import annotations

import copy
import os
import shutil
import sys
import threading

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QIcon
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QAction,
    QApplication,
    QDockWidget,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QUndoStack,
    QVBoxLayout,
    QWidget,
)

from ai.assistant import propose_change, explain
from ai.client import NetwrightAI
from ai.tools import ops_to_commands, validate_ops
from core.commands import (
    AddAclRule,
    AddLink,
    CompositeCommand,
    CreateVlan,
    DeleteVlan,
    MoveDevice,
    RenameDevice,
    SetDeviceFields,
    SetPortVlan,
    SetTrunk,
)
from core.ids import new_id
from core.model import DEVICE_KINDS, AclRule, Device, Link, Port, Topology, Vlan
from core.policy import import_policy_file
from core.project import NetwrightProject
from core.settings import Settings
from core.validate import validate
from .canvas import MIME_DEVICE, TopologyScene, TopologyView
from .commands_qt import CoreCommandWrapper
from .theme import build_qss, palette_for, severity_colors

THEME_CYCLE = ["dark", "light", "high_contrast"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _parse_vlan_csv(text: str) -> list[int]:
    """Parse '10, 20,30' -> [10, 20, 30], ignoring blanks and non-numbers."""
    out: list[int] = []
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            out.append(int(chunk))
    return out


def describe_op(op: dict) -> str:
    """A one-line human description of a proposed op, for the ProposalCard."""
    k = op.get("op")
    if k == "create_vlan":
        return f"+ Create VLAN {op.get('id')} ({op.get('name', '')})"
    if k == "delete_vlan":
        return f"− Delete VLAN {op.get('id')}"
    if k == "set_port_vlan":
        return f"~ {op.get('device')}/{op.get('port')} → access VLAN {op.get('vlan')}"
    if k == "set_trunk":
        return f"~ {op.get('device')}/{op.get('port')} → trunk {op.get('allowed_vlans')}"
    if k == "edit_vlan":
        return f"~ Edit VLAN {op.get('id')} ({op.get('name', '')})"
    if k == "set_subnet":
        return f"~ VLAN {op.get('id')} subnet {op.get('cidr', '')}"
    if k == "add_acl":
        return f"+ Permit VLAN {op.get('src_vlan')} → {op.get('dst_vlan')}"
    if k == "remove_acl":
        return f"− Remove permit {op.get('src_vlan')} → {op.get('dst_vlan')}"
    if k == "add_device":
        return f"+ Add {op.get('kind', 'device')} {op.get('name', '')}"
    if k == "remove_device":
        return f"− Remove device {op.get('device')}"
    if k == "set_device":
        return f"~ Edit device {op.get('device')}"
    if k == "add_link":
        return f"+ Link {op.get('a_device')} ↔ {op.get('b_device')}"
    if k == "remove_link":
        return f"− Remove link {op.get('link')}"
    if k == "rename":
        return f"~ Rename {op.get('device')} → {op.get('name')}"
    return f"~ {k}"


class PaletteList(QListWidget):
    """Device palette whose drags carry the device kind as a custom MIME type."""

    def mimeData(self, items):
        from PyQt5.QtCore import QMimeData

        data = QMimeData()
        kind = items[0].data(Qt.UserRole) if items else "switch"
        data.setText(kind)
        data.setData(MIME_DEVICE, kind.encode("utf-8"))
        return data


class NetwrightWindow(QMainWindow):
    # Worker-thread results marshaled back to the UI thread via signals.
    ai_proposal_ready = pyqtSignal(object)
    ai_failed = pyqtSignal(str)
    ai_explained = pyqtSignal(str)
    live_result_ready = pyqtSignal(object)
    live_failed = pyqtSignal(str)

    def __init__(self, auto_refresh: bool = True, parent=None):
        super().__init__(parent)
        self.settings = Settings.load()
        self.ai = NetwrightAI(api_key=self.settings.resolve_api_key())
        self.project = NetwrightProject(name="Untitled", topology=Topology("Untitled"))
        self.undo_stack = QUndoStack(self)
        self.undo_stack.setUndoLimit(200)
        self._path: str | None = None
        self._loading = False
        self._pending_change = None
        self._recovered = False  # autosave-recovered content is unsaved
        self._theme = self.settings.theme if self.settings.theme in THEME_CYCLE else "dark"
        self._severity = severity_colors(self._theme)

        self.setWindowTitle("Netwright")
        self.resize(1480, 920)
        self.setMinimumSize(1000, 680)
        self.setStyleSheet(build_qss(self._theme))

        self.scene = TopologyScene(self.project.topology)
        self.canvas = TopologyView(self.scene)
        self.canvas.set_drop_sink(self.add_device_at)
        self.canvas.set_link_sink(self.create_link)
        self.scene.set_move_sink(self._on_canvas_move)
        self.scene.selectionChanged.connect(self._on_selection_changed)
        self.setCentralWidget(self.canvas)

        self._build_toolbar()
        self._build_palette_dock()
        self._build_right_dock()
        self._build_issues_dock()
        self._build_minimap_dock()
        self._build_menu()

        self.ai_proposal_ready.connect(self._on_ai_proposal)
        self.ai_failed.connect(self._on_ai_failed)
        self.ai_explained.connect(self.ai_transcript.append)
        self.live_result_ready.connect(self._on_live_result)
        self.live_failed.connect(
            lambda m: QMessageBox.critical(self, "Live discovery failed", m)
        )

        # The scene re-syncs from the model on every undo/redo/push.
        self.undo_stack.indexChanged.connect(lambda *_: self._after_change())
        self.undo_stack.cleanChanged.connect(lambda *_: self._update_status())

        self._install_exception_guard()
        self._update_status()
        if auto_refresh:
            # Timer autosave only in the real app; tests drive autosave_now().
            self._autosave_timer = QTimer(self)
            self._autosave_timer.timeout.connect(self.autosave_now)
            self._autosave_timer.start(60_000)
            self.refresh()

    # ---- construction ------------------------------------------------------
    def _build_toolbar(self) -> None:
        tb = self.addToolBar("Main")
        tb.setMovable(False)
        for text, slot in [
            ("New", self.new_project),
            ("Open", self.open_project),
            ("Save", self.save_project),
            ("Export", self.export_dialog),
        ]:
            act = QAction(text, self)
            act.triggered.connect(slot)
            tb.addAction(act)
        tb.addSeparator()
        tb.addAction(self.undo_stack.createUndoAction(self, "Undo"))
        tb.addAction(self.undo_stack.createRedoAction(self, "Redo"))
        tb.addSeparator()
        self.link_action = QAction("Link", self)
        self.link_action.setCheckable(True)
        self.link_action.toggled.connect(self.canvas.set_link_mode)
        tb.addAction(self.link_action)
        self.overlay_action = QAction("VLAN overlay", self)
        self.overlay_action.setCheckable(True)
        self.overlay_action.setChecked(True)
        self.overlay_action.toggled.connect(self.toggle_overlay)
        tb.addAction(self.overlay_action)
        fit_act = QAction("Fit", self)
        fit_act.triggered.connect(self.canvas.fit_to_view)
        tb.addAction(fit_act)
        theme_act = QAction("Theme", self)
        theme_act.triggered.connect(self.cycle_theme)
        tb.addAction(theme_act)
        tb.addSeparator()
        validate_act = QAction("Validate", self)
        validate_act.triggered.connect(self.refresh)
        tb.addAction(validate_act)

    def _build_palette_dock(self) -> None:
        dock = QDockWidget("Devices", self)
        dock.setAllowedAreas(Qt.LeftDockWidgetArea)
        self.palette = PaletteList()
        self.palette.setDragEnabled(True)
        self.palette.setDragDropMode(QAbstractItemView.DragOnly)
        for kind in DEVICE_KINDS:
            item = QListWidgetItem(kind.capitalize())
            item.setData(Qt.UserRole, kind)
            self.palette.addItem(item)
        self.palette.itemDoubleClicked.connect(
            lambda it: self.add_device_at(it.data(Qt.UserRole), 0.0, 0.0)
        )
        dock.setWidget(self.palette)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

    def _build_right_dock(self) -> None:
        dock = QDockWidget("Inspector", self)
        dock.setAllowedAreas(Qt.RightDockWidgetArea)
        self.right_tabs = QTabWidget()
        self.right_tabs.addTab(self._build_properties(), "Properties")
        self.right_tabs.addTab(self._build_ai_tab(), "AI")
        dock.setWidget(self.right_tabs)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

    def _build_properties(self) -> QWidget:
        self.prop_stack = QStackedWidget()
        self.prop_stack.addWidget(self._build_project_page())  # index 0
        self.prop_stack.addWidget(self._build_device_page())   # index 1
        return self.prop_stack

    def _build_project_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self.prop_name = QLabel(self.project.name)
        layout.addWidget(QLabel("<b>Project</b>"))
        layout.addWidget(self.prop_name)

        # VLAN management
        vlan_box = QGroupBox("VLANs")
        vlan_layout = QVBoxLayout(vlan_box)
        self.vlan_list = QListWidget()
        vlan_layout.addWidget(self.vlan_list)
        row = QHBoxLayout()
        self.vlan_id_input = QSpinBox()
        self.vlan_id_input.setRange(1, 4094)
        self.vlan_id_input.setValue(10)
        self.vlan_name_input = QLineEdit()
        self.vlan_name_input.setPlaceholderText("name")
        add_vlan = QPushButton("Add")
        add_vlan.clicked.connect(self._add_vlan_clicked)
        del_vlan = QPushButton("Remove")
        del_vlan.clicked.connect(self._remove_vlan_clicked)
        row.addWidget(self.vlan_id_input)
        row.addWidget(self.vlan_name_input)
        row.addWidget(add_vlan)
        row.addWidget(del_vlan)
        vlan_layout.addLayout(row)
        layout.addWidget(vlan_box)

        # Inter-VLAN permit (deny-by-default)
        acl_box = QGroupBox("Permit inter-VLAN (deny-by-default)")
        acl_layout = QHBoxLayout(acl_box)
        self.acl_src = QSpinBox()
        self.acl_src.setRange(1, 4094)
        self.acl_dst = QSpinBox()
        self.acl_dst.setRange(1, 4094)
        add_acl = QPushButton("Permit")
        add_acl.clicked.connect(
            lambda: self.add_acl_permit(self.acl_src.value(), self.acl_dst.value())
        )
        acl_layout.addWidget(QLabel("src"))
        acl_layout.addWidget(self.acl_src)
        acl_layout.addWidget(QLabel("dst"))
        acl_layout.addWidget(self.acl_dst)
        acl_layout.addWidget(add_acl)
        layout.addWidget(acl_box)

        # Legend
        self.legend = QListWidget()
        layout.addWidget(QLabel("<b>VLAN legend</b>"))
        layout.addWidget(self.legend)
        layout.addStretch(1)
        return page

    def _build_device_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        self.dev_id_label = QLabel("—")
        self.dev_name_input = QLineEdit()
        self.dev_mgmt_input = QLineEdit()
        self.dev_vlan_input = QSpinBox()
        self.dev_vlan_input.setRange(0, 4094)
        self.dev_trunk_input = QLineEdit()
        self.dev_trunk_input.setPlaceholderText("trunk allowed e.g. 10,20,30 (blank = access)")
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._apply_device_page)
        form.addRow("Device", self.dev_id_label)
        form.addRow("Name", self.dev_name_input)
        form.addRow("Mgmt IP", self.dev_mgmt_input)
        form.addRow("Port 1 access VLAN", self.dev_vlan_input)
        form.addRow("Port 1 trunk", self.dev_trunk_input)
        form.addRow(apply_btn)
        return page

    def _build_ai_tab(self) -> QWidget:
        ai_tab = QWidget()
        ai_layout = QVBoxLayout(ai_tab)
        self.ai_transcript = QTextBrowser()
        self.ai_input = QPlainTextEdit()
        self.ai_input.setPlaceholderText(
            "Describe a change, e.g. 'Isolate Guest from Sales and Engineering'"
        )
        self.ai_input.setFixedHeight(72)
        self.ai_ask = QPushButton("Ask")
        self.ai_ask.clicked.connect(self.ask_ai)
        explain_btn = QPushButton("Explain")
        explain_btn.clicked.connect(self.explain_design)
        row = QHBoxLayout()
        row.addWidget(self.ai_input)
        row.addWidget(self.ai_ask)
        row.addWidget(explain_btn)

        # Proposal card (hidden until a proposal arrives)
        self.proposal_card = QGroupBox("Proposed changes")
        card_layout = QVBoxLayout(self.proposal_card)
        self.proposal_summary = QLabel("")
        self.proposal_summary.setWordWrap(True)
        self.proposal_ops = QListWidget()  # checkable rows = per-op accept/reject
        card_hint = QLabel("Untick any op to exclude it; the rest is re-validated.")
        card_hint.setWordWrap(True)
        card_buttons = QHBoxLayout()
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.clicked.connect(self._apply_pending_proposal)
        reject_btn = QPushButton("Reject")
        reject_btn.clicked.connect(self._reject_pending_proposal)
        card_buttons.addWidget(self.apply_btn)
        card_buttons.addWidget(reject_btn)
        card_layout.addWidget(self.proposal_summary)
        card_layout.addWidget(self.proposal_ops)
        card_layout.addWidget(card_hint)
        card_layout.addLayout(card_buttons)
        self.proposal_card.setVisible(False)

        ai_layout.addWidget(self.ai_transcript)
        ai_layout.addWidget(self.proposal_card)
        ai_layout.addLayout(row)
        if not self.ai.available():
            self.ai_ask.setEnabled(False)
            self.ai_transcript.append(
                "Set ANTHROPIC_API_KEY to enable the assistant. "
                "Design, validation, and export work without it."
            )
        return ai_tab

    def _build_issues_dock(self) -> None:
        dock = QDockWidget("Issues", self)
        dock.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.RightDockWidgetArea)
        self.issues_panel = QTreeWidget()
        self.issues_panel.setColumnCount(3)
        self.issues_panel.setHeaderLabels(["Severity", "Code", "Message"])
        self.issues_panel.itemClicked.connect(self._on_issue_clicked)
        self._issue_targets: dict[int, tuple] = {}
        dock.setWidget(self.issues_panel)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)

    def _build_minimap_dock(self) -> None:
        from PyQt5.QtWidgets import QGraphicsView

        dock = QDockWidget("Minimap", self)
        dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.BottomDockWidgetArea)
        self.minimap = QGraphicsView(self.scene)  # second view on the same scene
        self.minimap.setInteractive(False)
        self.minimap.setFixedHeight(140)
        self.minimap.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.minimap.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        dock.setWidget(self.minimap)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

    def _update_minimap(self) -> None:
        if self.scene.device_items:
            rect = self.scene.itemsBoundingRect().adjusted(-40, -40, 40, 40)
            self.minimap.fitInView(rect, Qt.KeepAspectRatio)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        discover = QAction("Discover from LLDP/CDP…", self)
        discover.triggered.connect(self.discover_dialog)
        file_menu.addAction(discover)
        live = QAction("Live discover (SSH)…", self)
        live.triggered.connect(self.live_discover_dialog)
        file_menu.addAction(live)
        snmp = QAction("Live discover (SNMP)…", self)
        snmp.triggered.connect(self.snmp_discover_dialog)
        file_menu.addAction(snmp)
        import_policy = QAction("Import VLAN policy…", self)
        import_policy.triggered.connect(self.import_policy_dialog)
        export_ios = QAction("Export device config (IOS)…", self)
        export_ios.triggered.connect(self.export_ios_dialog)
        file_menu.addAction(import_policy)
        file_menu.addAction(export_ios)

        help_menu = self.menuBar().addMenu("Help")
        manual = QAction("User Manual", self)
        manual.triggered.connect(lambda: self._show_doc("MANUAL.md", "User Manual"))
        faq = QAction("FAQ", self)
        faq.triggered.connect(lambda: self._show_doc("FAQ.md", "FAQ"))
        about = QAction("About Netwright", self)
        about.triggered.connect(self._show_about)
        help_menu.addAction(manual)
        help_menu.addAction(faq)
        help_menu.addSeparator()
        help_menu.addAction(about)

    # ---- single mutation path ---------------------------------------------
    def _apply(self, core_cmd, text: str | None = None) -> None:
        self.undo_stack.push(
            CoreCommandWrapper(self.project.topology, core_cmd, text)
        )

    def _after_change(self) -> None:
        """Re-sync the scene from the model after any push/undo/redo."""
        prev_sel = self.scene.selected_device_id()
        self.scene.set_topology(self.project.topology)
        if prev_sel and prev_sel in self.scene.device_items:
            self.scene.device_items[prev_sel].setSelected(True)
        self._populate_issues(validate(self.project.topology))
        self._populate_vlan_widgets()
        self._update_minimap()
        self._update_status()

    # ---- controller methods (the GUI and tests call these) -----------------
    def add_device_at(self, kind: str, x: float, y: float) -> str:
        if kind not in DEVICE_KINDS:
            kind = "switch"
        dev = Device(
            id=new_id(kind[:2]),
            name=f"{kind.capitalize()}-{len(self.project.topology.devices) + 1}",
            kind=kind,
            x=x,
            y=y,
            ports=[Port(f"Gi0/{i}", f"Gi0/{i}") for i in range(1, 5)],
        )
        from core.commands import AddDevice

        self._apply(AddDevice(dev), f"Add {kind}")
        return dev.id

    def create_link(self, a_device: str, b_device: str, kind: str = "ethernet") -> str | None:
        topo = self.project.topology
        a = topo.devices.get(a_device)
        b = topo.devices.get(b_device)
        if not a or not b:
            return None
        a_port = self._free_port(a)
        b_port = self._free_port(b)
        link = Link(new_id("ln"), a_device, a_port, b_device, b_port, kind=kind)
        self._apply(AddLink(link), "Add link")
        return link.id

    def _free_port(self, device: Device) -> str:
        used = {
            p
            for lk in self.project.topology.links.values()
            for (d, p) in (
                (lk.a_device, lk.a_port),
                (lk.b_device, lk.b_port),
            )
            if d == device.id
        }
        for port in device.ports:
            if port.id not in used:
                return port.id
        # all busy: extend the device with a fresh port
        new_port = Port(f"Gi0/{len(device.ports) + 1}", f"Gi0/{len(device.ports) + 1}")
        device.ports.append(new_port)
        return new_port.id

    def create_vlan(self, vid: int, name: str, color: str = "#38bdf8") -> None:
        self._apply(CreateVlan(Vlan(vid, name, color=color)), f"Create VLAN {vid}")

    def delete_vlan(self, vid: int) -> None:
        self._apply(DeleteVlan(vid), f"Delete VLAN {vid}")

    def add_acl_permit(self, src: int, dst: int) -> None:
        self._apply(AddAclRule(AclRule(src, dst, "permit")), f"Permit {src}->{dst}")

    def rename_device(self, device_id: str, name: str) -> None:
        self._apply(RenameDevice(device_id, name), "Rename device")

    def set_port_access_vlan(self, device_id: str, port_id: str, vlan: int | None) -> None:
        self._apply(SetPortVlan(device_id, port_id, vlan), "Set access VLAN")

    def apply_proposal(self, change) -> int:
        """Apply a whole AI proposal as ONE undoable composite command."""
        if change is None or not change.applicable:
            return 0
        return self.apply_ops(change.ops, change.summary)

    def apply_ops(self, ops: list[dict], summary: str | None = None,
                  force: bool = False) -> int:
        """Re-validate an op subset against the live design; apply if clean.

        Returns the number of ops applied (0 if the subset is invalid or blocked).
        This is the guardrail shared by 'Apply all' and per-op accept/reject.
        ``force`` bypasses the mass-deletion block after explicit confirmation.
        """
        if not ops:
            return 0
        # Block a proposal that would delete too much unless explicitly confirmed.
        from ai.assistant import DESTRUCTIVE_FRACTION_LIMIT
        from ai.tools import destructive_count

        total = max(1, len(self.project.topology.devices))
        if not force and destructive_count(ops) / total > DESTRUCTIVE_FRACTION_LIMIT:
            return 0
        # Semantic check, then stage on a scratch copy and run full validation.
        if any(i.severity == "error" for i in validate_ops(self.project.topology, ops)):
            return 0
        try:
            from core.commands import CommandStack

            baseline = set(validate(self.project.topology))
            scratch = Topology.from_dict(self.project.topology.to_dict())
            stack = CommandStack(scratch)
            for cmd in ops_to_commands(scratch, ops):
                stack.execute(cmd)
            # Block only errors the change INTRODUCES, not pre-existing ones.
            new_errors = [
                i for i in validate(scratch)
                if i.severity == "error" and i not in baseline
            ]
            if new_errors:
                return 0
        except Exception:
            return 0
        cmds = ops_to_commands(self.project.topology, ops)
        self._apply(
            CompositeCommand(cmds, summary or "Apply AI proposal"),
            f"Apply AI proposal ({len(cmds)} changes)",
        )
        return len(cmds)

    def set_trunk(self, device_id: str, port_id: str,
                  allowed: list[int], native: int | None = None) -> None:
        self._apply(SetTrunk(device_id, port_id, allowed, native), "Set trunk")

    def record_move(self, device_id: str, old_x: float, old_y: float,
                    new_x: float, new_y: float) -> None:
        """Commit an interactive drag as one undoable MoveDevice."""
        self._apply(
            MoveDevice(device_id, new_x, new_y, old_x=old_x, old_y=old_y),
            "Move device",
        )

    def import_policy(self, path: str) -> int:
        """Import a SecureLink vlan_policy.json as permit ACLs (one undo step)."""
        acls = import_policy_file(path)
        existing = {(a.src_vlan, a.dst_vlan, a.action) for a in self.project.topology.acls}
        cmds = [
            AddAclRule(a)
            for a in acls
            if (a.src_vlan, a.dst_vlan, a.action) not in existing
        ]
        if not cmds:
            return 0
        self._apply(CompositeCommand(cmds, "Import VLAN policy"), "Import VLAN policy")
        return len(cmds)

    def cycle_theme(self) -> None:
        idx = (THEME_CYCLE.index(self._theme) + 1) % len(THEME_CYCLE)
        self.apply_theme(THEME_CYCLE[idx])

    def apply_theme(self, name: str) -> None:
        self._theme = name
        self._severity = severity_colors(name)
        self.setStyleSheet(build_qss(name))
        self.scene.setBackgroundBrush(QColor(palette_for(name)["bg"]))
        self.scene.sync()
        self._populate_issues(validate(self.project.topology))
        # Persist the choice so the next launch starts in this theme.
        self.settings.theme = name
        try:
            self.settings.save()
        except OSError:
            pass

    def toggle_overlay(self, on: bool) -> None:
        self.scene.vlan_overlay = on
        self.scene.sync()
        self._populate_vlan_widgets()

    def _on_canvas_move(self, device_id, old_pos, new_pos) -> None:
        # Defer the push: mutating + rebuilding the scene inside the item's own
        # mouse-release event would destroy the item mid-event.
        QTimer.singleShot(
            0,
            lambda: self.record_move(
                device_id, old_pos[0], old_pos[1], new_pos[0], new_pos[1]
            ),
        )

    def load_topology(self, topology: Topology, name: str | None = None) -> None:
        """Replace the current document with a topology (e.g. from discovery)."""
        self.project = NetwrightProject(name=name or topology.name, topology=topology)
        self._path = None
        self._recovered = True  # discovered content is unsaved
        self.undo_stack.clear()
        self.refresh()

    def discover_dialog(self) -> None:
        from PyQt5.QtWidgets import QFileDialog

        from core.discovery import discover_from_files

        if not self._confirm_discard():
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Discover from LLDP/CDP neighbor output", ROOT,
            "Neighbor dumps (*.txt *.log *.cfg);;All files (*)",
        )
        if not paths:
            return
        topology = discover_from_files(paths, name="Discovered")
        self.load_topology(topology)
        self.canvas.fit_to_view()
        QMessageBox.information(
            self, "Discovery",
            f"Discovered {len(topology.devices)} device(s) and "
            f"{len(topology.links)} link(s).",
        )

    def discover_live(self, creds, runner=None, authorized: bool = True):
        """Run SSH discovery and load the result. Testable with an injected runner."""
        from core.live_discovery import live_discover

        result = live_discover(creds, runner=runner, authorized=authorized,
                               name="Discovered (SSH)")
        if result.topology.devices:
            self.load_topology(result.topology)
        return result

    def live_discover_dialog(self) -> None:
        from PyQt5.QtWidgets import QFileDialog

        from core.live_discovery import available as ssh_available
        from core.live_discovery import load_inventory

        if not ssh_available():
            QMessageBox.warning(
                self, "Live discovery",
                "The 'netmiko' package is not installed.\n\n"
                "Run: pip install netmiko",
            )
            return
        resp = QMessageBox.question(
            self, "Authorized use",
            "Live discovery connects to the devices in your inventory over SSH.\n"
            "Only proceed for networks you own or are authorized to manage.\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if resp != QMessageBox.Yes or not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "SSH inventory (JSON)", ROOT, "Inventory (*.json)"
        )
        if not path:
            return
        try:
            creds = load_inventory(path)
        except Exception as exc:
            QMessageBox.critical(self, "Inventory error", str(exc))
            return

        def worker():
            try:
                self.discover_live(creds, authorized=True)
                self.live_result_ready.emit(True)
            except Exception as exc:  # noqa: BLE001
                self.live_failed.emit(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_live_result(self, _ok) -> None:
        self.canvas.fit_to_view()
        t = self.project.topology
        QMessageBox.information(
            self, "Discovery",
            f"Discovered {len(t.devices)} device(s) and {len(t.links)} link(s).",
        )

    def discover_snmp(self, creds, session_factory=None, authorized: bool = True):
        """Run SNMP discovery and load the result. Testable with a fake factory."""
        from core.snmp_discovery import snmp_discover

        result = snmp_discover(creds, session_factory=session_factory,
                               authorized=authorized, name="Discovered (SNMP)")
        if result.topology.devices:
            self.load_topology(result.topology)
        return result

    def snmp_discover_dialog(self) -> None:
        from PyQt5.QtWidgets import QFileDialog

        from core.snmp_discovery import available as snmp_available
        from core.snmp_discovery import load_inventory as load_snmp_inventory

        if not snmp_available():
            QMessageBox.warning(
                self, "SNMP discovery",
                "The 'pysnmp' package is not installed.\n\nRun: pip install pysnmp",
            )
            return
        resp = QMessageBox.question(
            self, "Authorized use",
            "SNMP discovery reads the LLDP-MIB from the devices in your inventory.\n"
            "Only proceed for networks you own or are authorized to manage.\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if resp != QMessageBox.Yes or not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "SNMP inventory (JSON)", ROOT, "Inventory (*.json)"
        )
        if not path:
            return
        try:
            creds = load_snmp_inventory(path)
        except Exception as exc:
            QMessageBox.critical(self, "Inventory error", str(exc))
            return

        def worker():
            try:
                self.discover_snmp(creds, authorized=True)
                self.live_result_ready.emit(True)
            except Exception as exc:  # noqa: BLE001
                self.live_failed.emit(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def import_policy_dialog(self) -> None:
        from PyQt5.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, "Import VLAN policy", ROOT, "JSON (*.json)"
        )
        if path:
            n = self.import_policy(path)
            QMessageBox.information(
                self, "Import VLAN policy", f"Imported {n} permit rule(s)."
            )

    def export_ios_dialog(self) -> None:
        from PyQt5.QtWidgets import QFileDialog

        from core.export import export_device_cli

        dev_id = self.scene.selected_device_id()
        if not dev_id:
            QMessageBox.information(
                self, "Export device config", "Select a device first."
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export IOS config", ROOT, "Config (*.txt *.cfg)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(export_device_cli(self.project.topology, dev_id))

    # ---- refresh / panels --------------------------------------------------
    def refresh(self) -> None:
        self._after_change()

    def _populate_issues(self, issues) -> None:
        self.issues_panel.clear()
        self._issue_targets = {}
        for idx, issue in enumerate(issues):
            item = QTreeWidgetItem([issue.severity, issue.code, issue.message])
            color = self._severity.get(issue.severity)
            if color:
                item.setForeground(0, QColor(color))
            self.issues_panel.addTopLevelItem(item)
            self._issue_targets[id(item)] = (issue.device_ids, issue.link_ids)

    def _populate_vlan_widgets(self) -> None:
        if not hasattr(self, "vlan_list"):
            return
        self.prop_name.setText(self.project.name)
        self.vlan_list.clear()
        for vid in sorted(self.project.topology.vlans):
            v = self.project.topology.vlans[vid]
            self.vlan_list.addItem(f"{vid} — {v.name}")
        self.legend.clear()
        for color, label in self.scene.legend_entries():
            item = QListWidgetItem(label)
            item.setForeground(QColor(color))
            self.legend.addItem(item)

    def _on_issue_clicked(self, item, _col) -> None:
        device_ids, link_ids = self._issue_targets.get(id(item), ((), ()))
        for d in device_ids:
            node = self.scene.device_items.get(d)
            if node:
                node.setSelected(True)
                self.canvas.centerOn(node)

    def _on_selection_changed(self) -> None:
        try:
            dev_id = self.scene.selected_device_id()
        except RuntimeError:  # scene already destroyed during teardown
            return
        if dev_id:
            self._load_device_page(dev_id)
            self.prop_stack.setCurrentIndex(1)
        else:
            self.prop_stack.setCurrentIndex(0)

    # ---- properties wiring -------------------------------------------------
    def _add_vlan_clicked(self) -> None:
        self.create_vlan(
            self.vlan_id_input.value(),
            self.vlan_name_input.text().strip() or f"VLAN{self.vlan_id_input.value()}",
        )
        self.vlan_name_input.clear()

    def _remove_vlan_clicked(self) -> None:
        row = self.vlan_list.currentItem()
        if row:
            vid = int(row.text().split(" — ")[0])
            self.delete_vlan(vid)

    def _load_device_page(self, device_id: str) -> None:
        dev = self.project.topology.devices.get(device_id)
        if not dev:
            return
        self._loading = True
        self.dev_id_label.setText(f"{dev.name} ({dev.kind})")
        self.dev_name_input.setText(dev.name)
        self.dev_mgmt_input.setText(dev.mgmt_ip or "")
        first = dev.ports[0] if dev.ports else None
        self.dev_vlan_input.setValue((first.access_vlan or 0) if first else 0)
        if first and first.mode == "trunk":
            self.dev_trunk_input.setText(",".join(str(v) for v in first.allowed_vlans))
        else:
            self.dev_trunk_input.setText("")
        self._sel_device = device_id
        self._loading = False

    def _apply_device_page(self) -> None:
        """Commit all Properties edits for the selected device as ONE undo step."""
        if self._loading or not getattr(self, "_sel_device", None):
            return
        dev_id = self._sel_device
        dev = self.project.topology.devices.get(dev_id)
        if not dev:
            return
        cmds = []
        new_name = self.dev_name_input.text().strip()
        if new_name and new_name != dev.name:
            cmds.append(RenameDevice(dev_id, new_name))
        mgmt = self.dev_mgmt_input.text().strip() or None
        if mgmt != dev.mgmt_ip:
            cmds.append(SetDeviceFields(dev_id, mgmt_ip=mgmt, mgmt_ip_set=True))
        first = dev.ports[0] if dev.ports else None
        if first:
            trunk_text = self.dev_trunk_input.text().strip()
            if trunk_text:
                allowed = _parse_vlan_csv(trunk_text)
                if first.mode != "trunk" or first.allowed_vlans != allowed:
                    cmds.append(SetTrunk(dev_id, first.id, allowed))
            else:
                new_vlan = self.dev_vlan_input.value() or None
                if first.mode != "access" or first.access_vlan != new_vlan:
                    cmds.append(SetPortVlan(dev_id, first.id, new_vlan))
        if cmds:
            self._apply(CompositeCommand(cmds, "Edit device"), "Edit device")

    # ---- AI ----------------------------------------------------------------
    def ask_ai(self) -> None:
        intent = self.ai_input.toPlainText().strip()
        if not intent:
            return
        self.ai_transcript.append(f"<b>You:</b> {intent}")
        self.ai_ask.setEnabled(False)
        # Snapshot on the UI thread so the worker never reads the live model
        # while the user keeps editing.
        snapshot = Topology.from_dict(copy.deepcopy(self.project.topology.to_dict()))
        ai = self.ai

        def worker():
            try:
                self.ai_proposal_ready.emit(propose_change(snapshot, intent, ai))
            except Exception as exc:
                self.ai_failed.emit(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def explain_design(self) -> None:
        ai = self.ai if self.ai.available() else None
        self.ai_explained.emit(
            f"<b>Netwright:</b> {explain(self.project.topology, ai)}"
        )

    def _on_ai_proposal(self, change) -> None:
        self.ai_ask.setEnabled(self.ai.available())
        if change.has_errors or not change.ops:
            self.ai_transcript.append(
                "<b>Netwright:</b> The proposal did not validate and was not applied."
            )
            for issue in change.op_issues + change.predicted_issues:
                if issue.severity == "error":
                    self.ai_transcript.append(f"&nbsp;&nbsp;• {issue.message}")
            self.proposal_card.setVisible(False)
            return
        self._pending_change = change
        self.proposal_summary.setText(change.summary or "Proposed changes")
        self.proposal_ops.clear()
        for op in change.ops:
            item = QListWidgetItem(describe_op(op))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.proposal_ops.addItem(item)
        destructive = " — includes deletions" if change.destructive else ""
        self.apply_btn.setText(f"Apply ({len(change.ops)}){destructive}")
        self.proposal_card.setVisible(True)

    def _selected_ops(self) -> list[dict]:
        ops = []
        for i in range(self.proposal_ops.count()):
            if self.proposal_ops.item(i).checkState() == Qt.Checked:
                ops.append(self._pending_change.ops[i])
        return ops

    def _apply_pending_proposal(self) -> None:
        if self._pending_change is None:
            return
        ops = self._selected_ops()
        from ai.assistant import DESTRUCTIVE_FRACTION_LIMIT
        from ai.tools import destructive_count

        total = max(1, len(self.project.topology.devices))
        force = False
        if destructive_count(ops) / total > DESTRUCTIVE_FRACTION_LIMIT:
            resp = QMessageBox.question(
                self,
                "Confirm destructive change",
                f"This proposal deletes {destructive_count(ops)} item(s) — a large "
                "share of the design. Apply anyway?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                self.ai_transcript.append("<b>Netwright:</b> Destructive proposal cancelled.")
                return
            force = True
        n = self.apply_ops(ops, force=force)
        if n == 0:
            self.ai_transcript.append(
                "<b>Netwright:</b> The selected ops did not validate; nothing applied."
            )
            return
        self.ai_transcript.append(
            f"<b>Netwright:</b> Applied {n} change(s). Use Undo to revert as one step."
        )
        self._pending_change = None
        self.proposal_card.setVisible(False)

    def _reject_pending_proposal(self) -> None:
        self._pending_change = None
        self.proposal_card.setVisible(False)
        self.ai_transcript.append("<b>Netwright:</b> Proposal rejected.")

    def _on_ai_failed(self, msg: str) -> None:
        self.ai_ask.setEnabled(self.ai.available())
        self.ai_transcript.append(f"<b>Netwright:</b> {msg}")

    # ---- file actions ------------------------------------------------------
    def new_project(self) -> None:
        if not self._confirm_discard():
            return
        self.project = NetwrightProject(name="Untitled", topology=Topology("Untitled"))
        self._path = None
        self.undo_stack.clear()
        self.refresh()

    def open_project(self) -> None:
        from PyQt5.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, "Open project", ROOT, "Netwright (*.netwright)"
        )
        if path:
            self.load_path(path)

    # ---- autosave / backup / recovery ---------------------------------------
    def _autosave_path(self) -> str:
        if self._path:
            return self._path + ".autosave"
        base = os.path.join(os.path.expanduser("~"), ".netwright")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "unsaved.netwright.autosave")

    def autosave_now(self) -> str | None:
        """Write a crash-recovery snapshot if there are unsaved changes."""
        if self.undo_stack.isClean() and not self._recovered:
            return None
        path = self._autosave_path()
        try:
            self.project.topology = self.scene.topology
            self.project.save(path)
            return path
        except Exception:
            return None  # autosave must never take the app down

    @staticmethod
    def find_autosave(path: str) -> str | None:
        """An autosave newer than the project file, if one exists."""
        auto = path + ".autosave"
        if os.path.exists(auto) and (
            not os.path.exists(path)
            or os.path.getmtime(auto) > os.path.getmtime(path)
        ):
            return auto
        return None

    def load_path(self, path: str, recover: bool | None = None) -> None:
        """Open a project; offer to recover a newer autosave if one exists.

        ``recover``: True/False decides silently; None prompts (GUI) or skips
        recovery (headless), so tests never block on a dialog.
        """
        try:
            auto = self.find_autosave(path)
            use_auto = False
            if auto is not None:
                if recover is None and self.isVisible():
                    resp = QMessageBox.question(
                        self,
                        "Recover autosave?",
                        "A newer autosave of this project exists. Recover it?",
                        QMessageBox.Yes | QMessageBox.No,
                    )
                    use_auto = resp == QMessageBox.Yes
                else:
                    use_auto = bool(recover)
            self.project = NetwrightProject.load(auto if use_auto else path)
            self._path = path
            self._recovered = use_auto  # recovered content is unsaved by definition
            self.undo_stack.clear()
            self.refresh()
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", str(exc))

    def save_to(self, path: str) -> None:
        """Save with a .bak rotation of the previous file; clears the autosave."""
        if os.path.exists(path):
            try:
                shutil.copy2(path, path + ".bak")
            except OSError:
                pass  # backup is best-effort; the atomic save still protects us
        self.project.topology = self.scene.topology
        self.project.save(path)
        self._path = path
        self._recovered = False
        try:
            os.remove(path + ".autosave")
        except OSError:
            pass
        self.undo_stack.setClean()
        self._update_status()

    def save_project(self) -> None:
        from PyQt5.QtWidgets import QFileDialog

        path = self._path
        if not path:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save project", ROOT, "Netwright (*.netwright)"
            )
        if path:
            self.save_to(path)

    def export_dialog(self) -> None:
        from PyQt5.QtWidgets import QFileDialog

        from core.export import export_json

        path, _ = QFileDialog.getSaveFileName(self, "Export JSON", ROOT, "JSON (*.json)")
        if path:
            export_json(self.project.topology, path)

    # ---- helpers -----------------------------------------------------------
    def _update_status(self) -> None:
        t = self.project.topology
        ai_state = "ready" if self.ai.available() else "set ANTHROPIC_API_KEY"
        self.statusBar().showMessage(
            f"{len(t.devices)} devices · {len(t.links)} links · "
            f"{len(t.vlans)} VLANs · model {self.ai.model} · AI: {ai_state}"
        )
        is_dirty = not self.undo_stack.isClean() or self._recovered
        self.setWindowTitle(f"Netwright — {self.project.name}{' *' if is_dirty else ''}")

    def _confirm_discard(self) -> bool:
        if self.undo_stack.isClean() and not self._recovered:
            return True
        resp = QMessageBox.question(
            self,
            "Unsaved changes",
            "Discard unsaved changes?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        )
        if resp == QMessageBox.Save:
            self.save_project()
            return True
        return resp == QMessageBox.Discard

    def _show_doc(self, filename: str, title: str) -> None:
        from PyQt5.QtWidgets import QDialog

        path = os.path.join(ROOT, "docs", filename)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            text = f"{filename} not found."
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(720, 600)
        layout = QVBoxLayout(dlg)
        browser = QTextBrowser()
        browser.setMarkdown(text)
        layout.addWidget(browser)
        dlg.exec_()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Netwright",
            "Netwright — visual VLAN & network designer with AI.\n"
            "© 2026 Firas Bech · MIT License.",
        )

    def _install_exception_guard(self) -> None:
        log_dir = os.path.join(os.path.expanduser("~"), ".netwright", "logs")

        def _hook(exc_type, exc, tb):
            try:
                os.makedirs(log_dir, exist_ok=True)
                import traceback

                with open(
                    os.path.join(log_dir, "ui-errors.log"), "a", encoding="utf-8"
                ) as fh:
                    fh.write("".join(traceback.format_exception(exc_type, exc, tb)))
            except Exception:
                pass
            sys.__excepthook__(exc_type, exc, tb)

        sys.excepthook = _hook

    def closeEvent(self, event) -> None:
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()


def launch_dashboard(argv=None) -> int:
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setStyle("Fusion")
    icon_path = os.path.join(ROOT, "assets", "netwright.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    window = NetwrightWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(launch_dashboard())
