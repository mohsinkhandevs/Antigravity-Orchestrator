"""
Subagents Widget: Hierarchical Subagent Fleet & Telemetry Tracker.
==================================================================
Displays active and historical subagents spawned by orchestrators in Antigravity:
- Tree hierarchy (Parent -> Subagent -> Nested subagents)
- Live duration ticker (real-time seconds increment)
- Context window gauge and token breakdown (uncached in, cache hits, output)
- Compaction checkpoint indicators
- IPC inter-agent message drawer (briefings, remediation reports, review loops)
- Fast-action folder and log viewer shortcuts
"""

import os
import sys
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QHeaderView,
    QFrame,
    QSplitter,
    QPlainTextEdit,
    QProgressBar,
    QApplication,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QUrl
from PyQt6.QtGui import QColor, QFont, QDesktopServices

from app.ui.styles import (
    CARD_STYLE,
    COLOR_SURFACE_0,
    COLOR_SURFACE_1,
    COLOR_SURFACE_2,
    COLOR_SURFACE_HOVER,
    COLOR_BORDER_DEFAULT,
    COLOR_BORDER_SUBTLE,
    COLOR_BORDER_STRONG,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_FAINT,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_DANGER,
    COLOR_INFO,
)
from cdp.subagent_inspector import (
    build_subagent_tree,
    format_duration_human,
    get_subagents_fleet_summary,
    _parse_iso_timestamp,
    DEFAULT_BRAIN_DIR,
)


def _format_token_num(tokens: Optional[int]) -> str:
    if tokens is None or tokens <= 0:
        return "0"
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.2f}M"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.1f}k"
    return f"{tokens:,}"


class SubagentsWidget(QWidget):
    """
    Dedicated widget for monitoring subagent fleet hierarchy, token economics,
    live duration, context window, and inter-agent communication.
    """

    sig_subagent_selected = pyqtSignal(str)  # conversation_id

    def __init__(self, parent: Optional[QWidget] = None, brain_dir: Optional[str] = None):
        super().__init__(parent)
        self.brain_dir = brain_dir or DEFAULT_BRAIN_DIR
        self.current_root_id: str = ""
        self.subagents_data: List[Dict[str, Any]] = []
        self._alive_nodes: List[Dict[str, Any]] = []

        self._init_ui()

        # 1-second ticker for live duration updates without disk I/O
        self.ticker_timer = QTimer(self)
        self.ticker_timer.setInterval(1000)
        self.ticker_timer.timeout.connect(self._on_tick)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(6)

        # ─── Container Frame ───
        self.container_frame = QFrame(self)
        self.container_frame.setStyleSheet(
            f"QFrame {{ background-color: {COLOR_SURFACE_1}; border: 1px solid {COLOR_BORDER_DEFAULT}; border-radius: 8px; }}"
        )
        container_layout = QVBoxLayout(self.container_frame)
        container_layout.setContentsMargins(10, 8, 10, 8)
        container_layout.setSpacing(8)

        # ─── Header Bar: Title + Fleet Stats Pill Badges ───
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)

        self.title_lbl = QLabel("🤖 Subagent Fleet Orchestration")
        self.title_lbl.setStyleSheet(f"font-size: 13px; font-weight: 700; color: {COLOR_TEXT_PRIMARY}; border: none;")
        header_layout.addWidget(self.title_lbl)

        # Badge: Subagent count
        self.badge_count = QLabel("0 Subagents")
        self.badge_count.setStyleSheet(
            f"background-color: {COLOR_SURFACE_2}; color: {COLOR_TEXT_SECONDARY}; padding: 2px 8px; "
            f"border-radius: 10px; font-size: 11px; font-weight: 600; border: 1px solid {COLOR_BORDER_SUBTLE};"
        )
        header_layout.addWidget(self.badge_count)

        # Badge: Active count
        self.badge_active = QLabel("0 Active")
        self.badge_active.setStyleSheet(
            f"background-color: #064e3b; color: {COLOR_SUCCESS}; padding: 2px 8px; "
            f"border-radius: 10px; font-size: 11px; font-weight: 600; border: 1px solid #059669;"
        )
        header_layout.addWidget(self.badge_active)

        # Badge: Context Peak
        self.badge_context = QLabel("Peak Ctx: 0")
        self.badge_context.setStyleSheet(
            f"background-color: {COLOR_SURFACE_2}; color: {COLOR_INFO}; padding: 2px 8px; "
            f"border-radius: 10px; font-size: 11px; font-weight: 600; border: 1px solid {COLOR_BORDER_SUBTLE};"
        )
        header_layout.addWidget(self.badge_context)

        # Badge: Cache Hit Rate
        self.badge_cache = QLabel("Cache Hit: 0%")
        self.badge_cache.setStyleSheet(
            f"background-color: {COLOR_SURFACE_2}; color: {COLOR_TEXT_MUTED}; padding: 2px 8px; "
            f"border-radius: 10px; font-size: 11px; font-weight: 600; border: 1px solid {COLOR_BORDER_SUBTLE};"
        )
        header_layout.addWidget(self.badge_cache)

        header_layout.addStretch(1)

        self.btn_refresh = QPushButton("↻ Refresh Fleet")
        self.btn_refresh.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_SURFACE_2}; color: {COLOR_TEXT_SECONDARY}; "
            f"border: 1px solid {COLOR_BORDER_DEFAULT}; padding: 3px 10px; border-radius: 6px; font-size: 11px; font-weight: 600; }} "
            f"QPushButton:hover {{ background-color: {COLOR_SURFACE_HOVER}; color: {COLOR_TEXT_PRIMARY}; border-color: {COLOR_BORDER_STRONG}; }}"
        )
        self.btn_refresh.clicked.connect(self.refresh)
        header_layout.addWidget(self.btn_refresh)

        container_layout.addLayout(header_layout)

        # ─── Splitter: Tree (top) + Drawer/Messages (bottom) ───
        self.splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.splitter.setStyleSheet(f"QSplitter::handle {{ background-color: {COLOR_BORDER_DEFAULT}; height: 2px; }}")

        # ─── Subagents Tree Widget ───
        self.tree = QTreeWidget(self.splitter)
        self.tree.setHeaderLabels([
            "Subagent / Role",
            "State",
            "Elapsed Duration",
            "Context Window",
            "Tokens (Cache / In / Out)",
            "Cache %",
            "Steps",
            "Compacted",
        ])
        self.tree.setStyleSheet(
            f"QTreeWidget {{ background-color: {COLOR_SURFACE_0}; border: 1px solid {COLOR_BORDER_SUBTLE}; "
            f"border-radius: 6px; color: {COLOR_TEXT_PRIMARY}; font-size: 11px; }} "
            f"QTreeWidget::item {{ padding: 4px; }} "
            f"QTreeWidget::item:selected {{ background-color: {COLOR_SURFACE_2}; color: {COLOR_TEXT_PRIMARY}; }} "
            f"QHeaderView::section {{ background-color: {COLOR_SURFACE_2}; color: {COLOR_TEXT_SECONDARY}; "
            f"font-size: 11px; font-weight: 600; border: none; padding: 4px; border-right: 1px solid {COLOR_BORDER_SUBTLE}; }}"
        )
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._on_item_selected)

        # ─── Drawer: Selected Subagent Details & IPC Messages ───
        self.drawer_widget = QWidget(self.splitter)
        drawer_layout = QVBoxLayout(self.drawer_widget)
        drawer_layout.setContentsMargins(4, 6, 4, 4)
        drawer_layout.setSpacing(6)

        drawer_header = QHBoxLayout()
        drawer_header.setSpacing(8)

        self.drawer_title = QLabel("Selected Subagent: None")
        self.drawer_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
        drawer_header.addWidget(self.drawer_title)

        drawer_header.addStretch(1)

        self.btn_copy_id = QPushButton("📋 Copy ID")
        self.btn_copy_id.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_SURFACE_2}; color: {COLOR_TEXT_SECONDARY}; "
            f"border: 1px solid {COLOR_BORDER_DEFAULT}; padding: 2px 8px; border-radius: 4px; font-size: 10px; }} "
            f"QPushButton:hover {{ background-color: {COLOR_SURFACE_HOVER}; color: {COLOR_TEXT_PRIMARY}; }}"
        )
        self.btn_copy_id.clicked.connect(self._on_copy_id)
        drawer_header.addWidget(self.btn_copy_id)

        self.btn_open_folder = QPushButton("📁 Brain Folder")
        self.btn_open_folder.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_SURFACE_2}; color: {COLOR_TEXT_SECONDARY}; "
            f"border: 1px solid {COLOR_BORDER_DEFAULT}; padding: 2px 8px; border-radius: 4px; font-size: 10px; }} "
            f"QPushButton:hover {{ background-color: {COLOR_SURFACE_HOVER}; color: {COLOR_TEXT_PRIMARY}; }}"
        )
        self.btn_open_folder.clicked.connect(self._on_open_brain_folder)
        drawer_header.addWidget(self.btn_open_folder)

        self.btn_open_logs = QPushButton("📄 Transcript")
        self.btn_open_logs.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_SURFACE_2}; color: {COLOR_TEXT_SECONDARY}; "
            f"border: 1px solid {COLOR_BORDER_DEFAULT}; padding: 2px 8px; border-radius: 4px; font-size: 10px; }} "
            f"QPushButton:hover {{ background-color: {COLOR_SURFACE_HOVER}; color: {COLOR_TEXT_PRIMARY}; }}"
        )
        self.btn_open_logs.clicked.connect(self._on_open_transcript)
        drawer_header.addWidget(self.btn_open_logs)

        drawer_layout.addLayout(drawer_header)

        # Subagent Info & IPC Mailbox View
        self.messages_view = QPlainTextEdit(self.drawer_widget)
        self.messages_view.setReadOnly(True)
        self.messages_view.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {COLOR_SURFACE_0}; border: 1px solid {COLOR_BORDER_SUBTLE}; "
            f"border-radius: 4px; color: {COLOR_TEXT_SECONDARY}; font-family: monospace; font-size: 11px; padding: 6px; }}"
        )
        self.messages_view.setPlaceholderText("Select a subagent above to view its IPC messages, briefings, and defect reports.")
        drawer_layout.addWidget(self.messages_view)

        # Set initial splitter proportions: 60% tree, 40% drawer
        self.splitter.setSizes([160, 120])
        container_layout.addWidget(self.splitter)

        main_layout.addWidget(self.container_frame)

    def load_conversation(self, conversation_id: str, summary_data: Optional[Dict[str, Any]] = None):
        """Loads subagent fleet for the specified conversation UUID."""
        clean_id = (conversation_id or "").strip()
        if not clean_id:
            self.clear()
            return

        self.current_root_id = clean_id
        self.refresh(summary_data=summary_data)

    def clear(self):
        """Clears tree and drawer contents."""
        self.current_root_id = ""
        self.subagents_data = []
        self._alive_nodes = []
        self.ticker_timer.stop()
        self.tree.clear()
        self.badge_count.setText("0 Subagents")
        self.badge_active.setText("0 Active")
        self.badge_context.setText("Peak Ctx: 0")
        self.badge_cache.setText("Cache Hit: 0%")
        self.drawer_title.setText("Selected Subagent: None")
        self.messages_view.clear()

    def refresh(self, summary_data: Optional[Dict[str, Any]] = None):
        """Refreshes the subagent hierarchy tree and summary statistics."""
        if not self.current_root_id:
            return

        if summary_data and isinstance(summary_data, dict) and "subagents_tree" in summary_data:
            summary = summary_data
        else:
            summary = get_subagents_fleet_summary(self.current_root_id, brain_dir=self.brain_dir)

        self.subagents_data = summary.get("subagents_tree", [])
        self._alive_nodes = []

        total_cnt = summary.get("total_count", 0)
        active_cnt = summary.get("active_count", 0)
        peak_ctx = summary.get("peak_context_tokens", 0)
        hit_ratio = summary.get("fleet_cache_hit_ratio", 0.0)

        self.badge_count.setText(f"{total_cnt} Subagent{'s' if total_cnt != 1 else ''}")
        self.badge_active.setText(f"{active_cnt} Active")
        if active_cnt > 0:
            self.badge_active.setStyleSheet(
                f"background-color: #064e3b; color: {COLOR_SUCCESS}; padding: 2px 8px; "
                f"border-radius: 10px; font-size: 11px; font-weight: 600; border: 1px solid #059669;"
            )
        else:
            self.badge_active.setStyleSheet(
                f"background-color: {COLOR_SURFACE_2}; color: {COLOR_TEXT_MUTED}; padding: 2px 8px; "
                f"border-radius: 10px; font-size: 11px; font-weight: 600; border: 1px solid {COLOR_BORDER_SUBTLE};"
            )

        self.badge_context.setText(f"Peak Ctx: {_format_token_num(peak_ctx)}")
        self.badge_cache.setText(f"Cache Hit: {hit_ratio:.1%}")

        self.tree.clear()

        # Populate tree recursively
        for sub_node in self.subagents_data:
            self._add_tree_node(None, sub_node)

        self.tree.expandAll()

        # Start live ticker timer if any subagent is currently ALIVE
        if self._alive_nodes:
            if not self.ticker_timer.isActive():
                self.ticker_timer.start()
        else:
            self.ticker_timer.stop()

    def _add_tree_node(self, parent_item: Optional[QTreeWidgetItem], node: Dict[str, Any]):
        item = QTreeWidgetItem()
        cid = node.get("conversation_id", "")
        role = node.get("role", "Subagent")
        type_name = node.get("type_name", "self")
        short_id = cid[:8] if len(cid) >= 8 else cid

        item.setText(0, f"🤖 {role} ({short_id})")
        item.setToolTip(0, f"UUID: {cid}\nType: {type_name}\nParent: {node.get('parent_id')}")

        st_label = node.get("status_label", "UNKNOWN")
        is_alive = node.get("is_alive", False)
        if is_alive:
            item.setText(1, f"🟢 {st_label}")
            item.setForeground(1, QColor(COLOR_SUCCESS))
        elif node.get("is_killed"):
            item.setText(1, "🔴 KILLED")
            item.setForeground(1, QColor(COLOR_DANGER))
        else:
            item.setText(1, f"⚪ {st_label}")
            item.setForeground(1, QColor(COLOR_TEXT_MUTED))

        m = node.get("metrics") or {}
        item.setText(2, m.get("duration_human", "0s"))

        # Context Window
        ctx_curr = m.get("current_context_tokens", 0)
        ctx_peak = m.get("peak_context_tokens", 0)
        item.setText(3, f"{_format_token_num(ctx_curr)} / {_format_token_num(ctx_peak)}")

        # Token breakdown (Cache / In / Out)
        c_tok = m.get("cache_read_tokens", 0)
        i_tok = m.get("input_tokens", 0)
        o_tok = m.get("output_tokens", 0)
        item.setText(4, f"{_format_token_num(c_tok)} / {_format_token_num(i_tok)} / {_format_token_num(o_tok)}")

        # Cache hit ratio
        hr = m.get("cache_hit_ratio", 0.0)
        item.setText(5, f"{hr:.1%}")

        # Steps
        item.setText(6, str(m.get("step_count", 0)))

        # Compactions
        comp_count = m.get("compaction_count", 0)
        if comp_count > 0:
            item.setText(7, f"🟡 {comp_count}")
            item.setForeground(7, QColor(COLOR_WARNING))
        else:
            item.setText(7, "-")

        item.setData(0, Qt.ItemDataRole.UserRole, node)

        if is_alive and m.get("first_time"):
            self._alive_nodes.append({"item": item, "first_time": m.get("first_time")})

        if parent_item is None:
            self.tree.addTopLevelItem(item)
        else:
            parent_item.addChild(item)

        for child in node.get("children", []):
            self._add_tree_node(item, child)

    def _on_tick(self):
        """Tick handler to update elapsed duration on alive subagent items."""
        if not self._alive_nodes:
            self.ticker_timer.stop()
            return

        now_utc = datetime.now(timezone.utc)
        for entry in self._alive_nodes:
            item = entry.get("item")
            t_str = entry.get("first_time")
            if item and t_str:
                t0 = _parse_iso_timestamp(t_str)
                if t0:
                    secs = max(0.0, (now_utc - t0).total_seconds())
                    item.setText(2, format_duration_human(secs))

    def _on_item_selected(self):
        """Displays selected subagent's IPC messages, briefings, and metadata."""
        items = self.tree.selectedItems()
        if not items:
            self.drawer_title.setText("Selected Subagent: None")
            self.messages_view.clear()
            return

        node: Dict[str, Any] = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not node:
            return

        cid = node.get("conversation_id", "")
        role = node.get("role", "Subagent")
        self.drawer_title.setText(f"Selected: {role} ({cid[:8]})")
        self.sig_subagent_selected.emit(cid)

        # Build detailed summary and IPC mailbox transcript
        lines: List[str] = []
        lines.append(f"═══════════════════════════════════════════════════════════════════════════════")
        lines.append(f"SUBAGENT: {role}")
        lines.append(f"Conversation ID: {cid}")
        lines.append(f"Type: {node.get('type_name')}  |  State: {node.get('raw_state')}  |  Spawn Step: {node.get('spawn_step_index')}")
        if node.get("workspace_uris"):
            lines.append(f"Workspace: {', '.join(node.get('workspace_uris'))}")

        m = node.get("metrics") or {}
        lines.append(f"Duration: {m.get('duration_human')}  |  Steps: {m.get('step_count')}  |  Tool Calls: {m.get('tool_calls_count')}  |  Compactions: {m.get('compaction_count')}")
        lines.append(f"Current Context: {m.get('current_context_tokens'):,} tokens  |  Peak Context: {m.get('peak_context_tokens'):,} tokens")
        lines.append(f"Tokens: Cache: {m.get('cache_read_tokens'):,}  |  In: {m.get('input_tokens'):,}  |  Out: {m.get('output_tokens'):,}  |  Hit Rate: {m.get('cache_hit_ratio'):.2%}")
        lines.append(f"═══════════════════════════════════════════════════════════════════════════════\n")

        messages = node.get("messages") or []
        lines.append(f"📨 INTER-AGENT IPC MESSAGES ({len(messages)} messages exchanged):")
        lines.append(f"───────────────────────────────────────────────────────────────────────────────")
        if not messages:
            lines.append("  (No direct inter-agent messages recorded in .system_generated/messages/)")
        else:
            for idx, msg in enumerate(messages, 1):
                sender = msg.get("sender", "")
                ts = msg.get("timestamp", "")
                title = msg.get("title", "")
                lines.append(f"\n[#{idx}] {ts} — {title}")
                lines.append(f"     From: {sender[:8]}... ➔ To: {msg.get('recipient', '')[:8]}... (Priority: {msg.get('priority')})")
                lines.append(f"     Content:")
                for c_line in msg.get("content", "").splitlines():
                    lines.append(f"       {c_line}")

        self.messages_view.setPlainText("\n".join(lines))

    def _get_selected_cid(self) -> Optional[str]:
        items = self.tree.selectedItems()
        if items:
            node = items[0].data(0, Qt.ItemDataRole.UserRole)
            if node:
                return node.get("conversation_id")
        return None

    def _on_copy_id(self):
        cid = self._get_selected_cid()
        if cid:
            QApplication.clipboard().setText(cid)

    def _on_open_brain_folder(self):
        cid = self._get_selected_cid()
        if cid:
            path = os.path.join(self.brain_dir, cid)
            if os.path.isdir(path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))

    def _on_open_transcript(self):
        cid = self._get_selected_cid()
        if cid:
            path = os.path.join(self.brain_dir, cid, ".system_generated", "logs", "transcript.jsonl")
            if os.path.isfile(path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))
