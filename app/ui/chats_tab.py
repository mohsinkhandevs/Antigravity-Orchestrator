"""
Chats Tab: Streamlined conversation lifecycle inspection, per-prompt breakdown,
interactive model thinking (CoT) toggle, tool execution waterfall timeline,
files impacted summary, and curated artifact viewer.
"""

import os
import sys
from typing import Dict, Any, List, Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFrame,
    QPlainTextEdit,
    QSplitter,
    QScrollArea,
    QMessageBox,
    QApplication,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QUrl
from PyQt6.QtGui import QDesktopServices, QColor, QTextCursor

from app.ui.styles import (
    CARD_STYLE,
    COLOR_SURFACE_2,
    COLOR_BORDER_DEFAULT,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_FAINT,
)
from app.ui.widgets import SegmentedControl, ToggleSwitch, ResponsiveScrollContainer, ScrollableTableWidget
from app.ui.subagents_widget import SubagentsWidget
from cdp.chat_lifecycle_inspector import (
    extract_conversation_id,
    format_duration_human,
    get_active_and_unfinished_chats,
    get_recent_conversations,
    inspect_conversation_lifecycle,
)


def _get_status_visuals(status: str) -> Dict[str, str]:
    """Returns icon, label, text color, background color, and border color for a lifecycle status."""
    st = (status or "UNKNOWN").upper()
    if st in ("RUNNING", "GENERATING"):
        return {
            "icon": "🟢",
            "label": "RUNNING",
            "fg": "#34d399",
            "bg": "#064e3b",
            "border": "#059669",
        }
    if st == "COMPLETED":
        return {
            "icon": "✔",
            "label": "COMPLETED",
            "fg": "#4ade80",
            "bg": "#14532d",
            "border": "#16a34a",
        }
    if st in ("STOPPED_BY_USER", "USER_STOPPED", "STOPPED", "PAUSED"):
        return {
            "icon": "⏸",
            "label": "STOPPED BY USER",
            "fg": "#94a3b8",
            "bg": "#1e293b",
            "border": "#475569",
        }
    if st in ("QUOTA_EXHAUSTED", "HALTED_QUOTA"):
        return {
            "icon": "🔒",
            "label": "QUOTA EXHAUSTED",
            "fg": "#fbbf24",
            "bg": "#451a03",
            "border": "#d97706",
        }
    if st == "INTERRUPTED":
        return {
            "icon": "⚡",
            "label": "INTERRUPTED",
            "fg": "#fb923c",
            "bg": "#431407",
            "border": "#ea580c",
        }
    if st == "WAITING_FOR_INPUT":
        return {
            "icon": "⏳",
            "label": "WAITING INPUT",
            "fg": "#38bdf8",
            "bg": "#0c4a6e",
            "border": "#0284c7",
        }
    if st in ("STEP_ERROR", "EXECUTION_ERROR", "ERROR", "FAILED"):
        return {
            "icon": "❌",
            "label": st.replace("_", " "),
            "fg": "#f87171",
            "bg": "#450a0a",
            "border": "#dc2626",
        }
    return {
        "icon": "⚪",
        "label": st,
        "fg": COLOR_TEXT_MUTED,
        "bg": COLOR_SURFACE_2,
        "border": COLOR_BORDER_DEFAULT,
    }


class ChatsTab(QWidget):
    """
    Dedicated tab for inspecting any conversation's execution lifecycle:
    - Unified conversation picker with recent project chats and UUID input
    - Clean top status bar with duration and project metadata
    - Master-detail splitter: Per-prompt history breakdown (top) + full turn detail (bottom)
    - 🧠 Model Thinking (Chain-of-Thought) toggle & 📁 Files Impacted summary
    - ⏱️ Tool Execution Waterfall timeline
    - Curated brain artifacts viewer and one-click IDE jump
    - Auto-resume continuation prompt composer
    """

    sig_conversation_selected = pyqtSignal(str)
    sig_open_in_antigravity_requested = pyqtSignal(str)
    sig_send_prompt_in_chat_requested = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_conversation_id: str = ""
        self.current_report: Optional[Dict[str, Any]] = None
        self._current_selected_turn: Optional[Dict[str, Any]] = None
        self._active_response_view: str = "output"  # 'output' or 'thinking'
        self._worker_active_projects: Dict[str, Any] = {}

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(3000)
        self.refresh_timer.timeout.connect(self._on_auto_refresh_tick)

        self._build_ui()
        self.refresh_recent_conversations()

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.scroll_area = ResponsiveScrollContainer(self, margin_h=12, margin_v=10, spacing=8)
        root_layout.addWidget(self.scroll_area)

        # ─────────────────────────────────────────────────────────────────────
        # 1. UNIFIED CONVERSATION PICKER & ACTION BAR
        # ─────────────────────────────────────────────────────────────────────
        top_frame = QFrame()
        top_frame.setStyleSheet(CARD_STYLE)
        top_layout = QVBoxLayout(top_frame)
        top_layout.setContentsMargins(12, 8, 12, 8)
        top_layout.setSpacing(8)

        # Row 1: Selection & Search Controls
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        lbl_conv = QLabel("Chat:")
        lbl_conv.setStyleSheet(f"font-weight: 700; font-size: 12px; color: {COLOR_TEXT_PRIMARY};")
        row1.addWidget(lbl_conv)

        self.combo_recent_chats = QComboBox()
        self.combo_recent_chats.setMinimumHeight(30)
        self.combo_recent_chats.setMinimumWidth(320)
        self.combo_recent_chats.currentIndexChanged.connect(self._on_recent_chat_selected)
        row1.addWidget(self.combo_recent_chats, 3)

        self.txt_conversation_id = QLineEdit()
        self.txt_conversation_id.setPlaceholderText("Paste Conversation UUID or brain path...")
        self.txt_conversation_id.setMinimumHeight(30)
        self.txt_conversation_id.setMinimumWidth(200)
        self.txt_conversation_id.setClearButtonEnabled(True)
        self.txt_conversation_id.returnPressed.connect(self.on_inspect_clicked)
        row1.addWidget(self.txt_conversation_id, 2)

        self.btn_inspect = QPushButton("🔍 Inspect")
        self.btn_inspect.setObjectName("btn-primary")
        self.btn_inspect.setProperty("variant", "primary")
        self.btn_inspect.setMinimumHeight(30)
        self.btn_inspect.clicked.connect(self.on_inspect_clicked)
        row1.addWidget(self.btn_inspect)

        self.btn_refresh = QPushButton("🔄")
        self.btn_refresh.setToolTip("Refresh conversations & active data")
        self.btn_refresh.setObjectName("btn-secondary")
        self.btn_refresh.setProperty("variant", "secondary")
        self.btn_refresh.setFixedSize(30, 30)
        self.btn_refresh.clicked.connect(self._on_manual_refresh_clicked)
        row1.addWidget(self.btn_refresh)
        top_layout.addLayout(row1)

        # Row 2: Status Badges, Metadata & Actions
        row2 = QHBoxLayout()
        row2.setSpacing(8)

        self.lbl_status_badge = QLabel("⚪ No Chat Selected")
        self.lbl_status_badge.setStyleSheet(
            f"background-color: {COLOR_SURFACE_2}; color: {COLOR_TEXT_MUTED}; "
            f"border: 1px solid {COLOR_BORDER_DEFAULT}; border-radius: 4px; "
            f"padding: 3px 10px; font-size: 11px; font-weight: 700;"
        )
        row2.addWidget(self.lbl_status_badge)

        self.lbl_duration_chip = QLabel("⏱ —")
        self.lbl_duration_chip.setStyleSheet(
            f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px; font-weight: 600; padding: 0 4px;"
        )
        row2.addWidget(self.lbl_duration_chip)

        self.lbl_project_chip = QLabel("Project: —")
        self.lbl_project_chip.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 600; padding: 0 4px;"
        )
        row2.addWidget(self.lbl_project_chip)

        row2.addStretch()

        self.btn_open_chat = QPushButton("↗ Open in Antigravity")
        self.btn_open_chat.setObjectName("btn-secondary")
        self.btn_open_chat.setProperty("variant", "secondary")
        self.btn_open_chat.setMinimumHeight(28)
        self.btn_open_chat.setEnabled(False)
        self.btn_open_chat.setToolTip("Switch Antigravity IDE directly to this conversation")
        self.btn_open_chat.clicked.connect(self._on_open_in_antigravity_clicked)
        row2.addWidget(self.btn_open_chat)

        self.btn_open_brain = QPushButton("📂 Brain Folder")
        self.btn_open_brain.setObjectName("btn-secondary")
        self.btn_open_brain.setProperty("variant", "secondary")
        self.btn_open_brain.setMinimumHeight(28)
        self.btn_open_brain.setEnabled(False)
        self.btn_open_brain.clicked.connect(self._on_open_brain_folder_clicked)
        row2.addWidget(self.btn_open_brain)
        top_layout.addLayout(row2)

        self.scroll_area.add_card(top_frame)

        # ─────────────────────────────────────────────────────────────────────
        # 2. OPTIONAL DIAGNOSTIC BANNER (Only shown for genuine Quota or Fatal Errors)
        # ─────────────────────────────────────────────────────────────────────
        self.frame_diag_banner = QFrame()
        self.frame_diag_banner.setVisible(False)
        diag_layout = QHBoxLayout(self.frame_diag_banner)
        diag_layout.setContentsMargins(10, 6, 10, 6)
        diag_layout.setSpacing(8)

        self.lbl_diag_text = QLabel("")
        self.lbl_diag_text.setWordWrap(True)
        self.lbl_diag_text.setStyleSheet("font-size: 11px; font-weight: 600; background: transparent; border: none;")
        diag_layout.addWidget(self.lbl_diag_text, 1)

        self.lbl_diag_source = QLabel("")
        self.lbl_diag_source.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 10px; background: transparent; border: none;"
        )
        diag_layout.addWidget(self.lbl_diag_source)

        self.scroll_area.add_card(self.frame_diag_banner)

        # ─────────────────────────────────────────────────────────────────────
        # 3. MASTER-DETAIL VERTICAL SPLITTER
        # ─────────────────────────────────────────────────────────────────────
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)

        # ── Top Pane: Per-Prompt History Table (~35% height) ──
        turns_frame = QFrame()
        turns_frame.setStyleSheet(CARD_STYLE)
        turns_layout = QVBoxLayout(turns_frame)
        turns_layout.setContentsMargins(10, 8, 10, 8)
        turns_layout.setSpacing(6)

        turns_header = QHBoxLayout()
        self.lbl_turns_title = QLabel("⏱ Prompt History (0 Prompts)")
        self.lbl_turns_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
        turns_header.addWidget(self.lbl_turns_title)

        turns_header.addStretch()

        self.lbl_chat_title = QLabel("Select a conversation above to inspect")
        self.lbl_chat_title.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {COLOR_TEXT_SECONDARY};")
        turns_header.addWidget(self.lbl_chat_title)

        turns_layout.addLayout(turns_header)

        self.table_turns = ScrollableTableWidget(0, 6)
        self.table_turns.setHorizontalHeaderLabels([
            "#",
            "Prompt Submitted",
            "Started At",
            "Duration",
            "Steps / Tools",
            "Status",
        ])
        self.table_turns.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_turns.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table_turns.setAlternatingRowColors(True)
        self.table_turns.verticalHeader().setVisible(False)
        self.table_turns.setShowGrid(False)
        self.table_turns.setMinimumHeight(140)
        self.table_turns.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.table_turns.itemSelectionChanged.connect(self._on_turn_selection_changed)

        hdr = self.table_turns.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

        turns_layout.addWidget(self.table_turns)
        self.splitter.addWidget(turns_frame)

        # ── Bottom Pane: Selected Turn Detail (~65% height) ──
        detail_frame = QFrame()
        detail_frame.setStyleSheet(CARD_STYLE)
        detail_layout = QVBoxLayout(detail_frame)
        detail_layout.setContentsMargins(10, 8, 10, 8)
        detail_layout.setSpacing(6)

        # Resizable horizontal split between Prompt and Response/Thinking
        h_splitter = QSplitter(Qt.Orientation.Horizontal)
        h_splitter.setChildrenCollapsible(False)

        # Left Column: Selected Prompt
        prompt_panel = QWidget()
        prompt_layout = QVBoxLayout(prompt_panel)
        prompt_layout.setContentsMargins(0, 0, 0, 0)
        prompt_layout.setSpacing(4)

        prompt_hdr = QHBoxLayout()
        self.lbl_selected_prompt_hdr = QLabel("💬 User Prompt:")
        self.lbl_selected_prompt_hdr.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {COLOR_TEXT_SECONDARY};"
        )
        prompt_hdr.addWidget(self.lbl_selected_prompt_hdr)
        prompt_hdr.addStretch()

        self.btn_copy_prompt = QPushButton("📋 Copy")
        self.btn_copy_prompt.setObjectName("btn-secondary")
        self.btn_copy_prompt.setFixedHeight(22)
        self.btn_copy_prompt.clicked.connect(self._copy_prompt_text)
        prompt_hdr.addWidget(self.btn_copy_prompt)
        prompt_layout.addLayout(prompt_hdr)

        self.txt_turn_prompt = QPlainTextEdit()
        self.txt_turn_prompt.setReadOnly(True)
        self.txt_turn_prompt.setMinimumHeight(110)
        self.txt_turn_prompt.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.txt_turn_prompt.setPlaceholderText("Select a prompt row above to view the full prompt text...")
        prompt_layout.addWidget(self.txt_turn_prompt)
        h_splitter.addWidget(prompt_panel)

        # Right Column: Agent Output / Thinking with Segmented Toggle
        resp_panel = QWidget()
        resp_layout = QVBoxLayout(resp_panel)
        resp_layout.setContentsMargins(0, 0, 0, 0)
        resp_layout.setSpacing(4)

        resp_hdr = QHBoxLayout()
        resp_hdr.setSpacing(6)

        self.lbl_selected_response_hdr = QLabel("🤖 Output:")
        self.lbl_selected_response_hdr.setStyleSheet(
            f"font-size: 11px; font-weight: 700; color: {COLOR_TEXT_SECONDARY};"
        )
        resp_hdr.addWidget(self.lbl_selected_response_hdr)

        self.view_segmented = SegmentedControl(self)
        self.btn_view_output = self.view_segmented.add_segment("output", "🤖 Output")
        self.btn_view_thinking = self.view_segmented.add_segment("thinking", "🧠 Thinking")
        self.view_segmented.sig_segment_changed.connect(self._on_segment_changed)
        resp_hdr.addWidget(self.view_segmented)

        resp_hdr.addStretch()

        lbl_auto = QLabel("Auto-scroll")
        lbl_auto.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 600;")
        resp_hdr.addWidget(lbl_auto)

        self.chk_resp_autoscroll = ToggleSwitch(self, checked=True)
        self.chk_resp_autoscroll.setToolTip("Toggle automatic scrolling to latest response/thinking text")
        self.chk_resp_autoscroll.toggled.connect(self._on_autoscroll_toggled)
        resp_hdr.addWidget(self.chk_resp_autoscroll)

        resp_hdr.addSpacing(6)

        self.btn_copy_resp = QPushButton("📋 Copy")
        self.btn_copy_resp.setObjectName("btn-secondary")
        self.btn_copy_resp.setProperty("variant", "ghost")
        self.btn_copy_resp.setFixedHeight(26)
        self.btn_copy_resp.clicked.connect(self._copy_response_text)
        resp_hdr.addWidget(self.btn_copy_resp)
        resp_layout.addLayout(resp_hdr)

        self.txt_turn_response = QPlainTextEdit()
        self.txt_turn_response.setReadOnly(True)
        self.txt_turn_response.setMinimumHeight(110)
        self.txt_turn_response.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.txt_turn_response.setPlaceholderText("Agent output and step execution details will appear here...")
        resp_layout.addWidget(self.txt_turn_response)
        h_splitter.addWidget(resp_panel)

        h_splitter.setStretchFactor(0, 1)
        h_splitter.setStretchFactor(1, 2)
        detail_layout.addWidget(h_splitter, 1)

        # ── 1. Tool Execution Waterfall Strip ──
        waterfall_hdr = QHBoxLayout()
        waterfall_hdr.setSpacing(6)

        lbl_wf_title = QLabel("⏱️ Execution Waterfall:")
        lbl_wf_title.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {COLOR_TEXT_SECONDARY};")
        waterfall_hdr.addWidget(lbl_wf_title)

        self.lbl_waterfall_summary = QLabel("⚡ Model: 0s · 🛠️ Tools: 0s")
        self.lbl_waterfall_summary.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_MUTED};")
        waterfall_hdr.addWidget(self.lbl_waterfall_summary)

        waterfall_hdr.addStretch()
        detail_layout.addLayout(waterfall_hdr)

        self.waterfall_scroll = QScrollArea()
        self.waterfall_scroll.setWidgetResizable(True)
        self.waterfall_scroll.setFixedHeight(34)
        self.waterfall_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.waterfall_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.waterfall_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.waterfall_chips_container = QWidget()
        self.waterfall_chips_container.setStyleSheet("background: transparent;")
        self.waterfall_chips_layout = QHBoxLayout(self.waterfall_chips_container)
        self.waterfall_chips_layout.setContentsMargins(0, 0, 0, 0)
        self.waterfall_chips_layout.setSpacing(6)
        self.waterfall_chips_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.waterfall_scroll.setWidget(self.waterfall_chips_container)

        detail_layout.addWidget(self.waterfall_scroll)

        # ── 2. Files Impacted Strip (Scrollable) ──
        files_bar = QHBoxLayout()
        files_bar.setSpacing(6)

        lbl_files_title = QLabel("📁 Files Impacted:")
        lbl_files_title.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {COLOR_TEXT_SECONDARY};")
        files_bar.addWidget(lbl_files_title)

        self.files_scroll = QScrollArea()
        self.files_scroll.setWidgetResizable(True)
        self.files_scroll.setFixedHeight(34)
        self.files_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.files_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.files_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.files_chips_container = QWidget()
        self.files_chips_container.setStyleSheet("background: transparent;")
        self.files_chips_layout = QHBoxLayout(self.files_chips_container)
        self.files_chips_layout.setContentsMargins(0, 0, 0, 0)
        self.files_chips_layout.setSpacing(6)
        self.files_chips_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.files_scroll.setWidget(self.files_chips_container)
        files_bar.addWidget(self.files_scroll, 1)

        detail_layout.addLayout(files_bar)

        # ── 3. Artifacts Strip (Scrollable) ──
        artifacts_bar = QHBoxLayout()
        artifacts_bar.setSpacing(6)

        lbl_art_title = QLabel("📦 Generated Artifacts:")
        lbl_art_title.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {COLOR_TEXT_SECONDARY};")
        artifacts_bar.addWidget(lbl_art_title)

        self.artifacts_scroll = QScrollArea()
        self.artifacts_scroll.setWidgetResizable(True)
        self.artifacts_scroll.setFixedHeight(34)
        self.artifacts_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.artifacts_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.artifacts_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.artifacts_chips_container = QWidget()
        self.artifacts_chips_container.setStyleSheet("background: transparent;")
        self.artifacts_chips_layout = QHBoxLayout(self.artifacts_chips_container)
        self.artifacts_chips_layout.setContentsMargins(0, 0, 0, 0)
        self.artifacts_chips_layout.setSpacing(6)
        self.artifacts_chips_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.artifacts_scroll.setWidget(self.artifacts_chips_container)
        artifacts_bar.addWidget(self.artifacts_scroll, 1)

        detail_layout.addLayout(artifacts_bar)

        # ── 4. Continuation Input Strip ──
        cont_frame = QFrame()
        cont_frame.setStyleSheet(
            f"background-color: {COLOR_SURFACE_2}; border: 1px solid {COLOR_BORDER_DEFAULT}; border-radius: 6px;"
        )
        cont_layout = QHBoxLayout(cont_frame)
        cont_layout.setContentsMargins(8, 4, 8, 4)
        cont_layout.setSpacing(8)

        lbl_cont = QLabel("⚡ Continue / Resume:")
        lbl_cont.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
        cont_layout.addWidget(lbl_cont)

        self.txt_continuation_prompt = QLineEdit()
        self.txt_continuation_prompt.setPlaceholderText("Type continuation or follow-up prompt...")
        self.txt_continuation_prompt.setStyleSheet(
            "QLineEdit { background-color: #0b0f19; border: 1px solid #1e293b; border-radius: 4px; padding: 4px 8px; color: #f8fafc; font-size: 11px; }"
        )
        self.txt_continuation_prompt.returnPressed.connect(self._on_send_continuation_clicked)
        cont_layout.addWidget(self.txt_continuation_prompt, 1)

        self.btn_send_continuation = QPushButton("Send Prompt ↵")
        self.btn_send_continuation.setObjectName("btn-primary")
        self.btn_send_continuation.setProperty("variant", "primary")
        self.btn_send_continuation.setFixedHeight(26)
        self.btn_send_continuation.clicked.connect(self._on_send_continuation_clicked)
        cont_layout.addWidget(self.btn_send_continuation)

        detail_layout.addWidget(cont_frame)

        self.splitter.addWidget(detail_frame)

        # ── 5. Subagent Fleet Orchestration Panel ──
        self.subagents_widget = SubagentsWidget(self)
        self.subagents_widget.setVisible(False)
        self.splitter.addWidget(self.subagents_widget)

        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setStretchFactor(2, 2)
        self.splitter.setMinimumHeight(440)
        self.splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.scroll_area.add_card(self.splitter, stretch=1)

    # ─────────────────────────────────────────────────────────────────────────
    # CONVERSATION SELECTION & RECENT CHATS
    # ─────────────────────────────────────────────────────────────────────────

    def update_worker_active_projects(self, active_projects_dict: Dict[str, Any]):
        """Retained for compatibility with main_window signal connections."""
        self._worker_active_projects = dict(active_projects_dict or {})

    def refresh_active_chats(self):
        """Refreshes the recent conversations list."""
        self.refresh_recent_conversations()

    def refresh_recent_conversations(self):
        """Populates the Recent Chats dropdown from conversation_summaries.db."""
        recent = get_recent_conversations(limit=40)
        self.combo_recent_chats.blockSignals(True)
        self.combo_recent_chats.clear()
        self.combo_recent_chats.addItem("— Select Recent Conversation —", "")
        for item in recent:
            cid = item.get("id", "")
            proj = item.get("project") or "Default"
            title = item.get("title") or cid[:8]
            if len(title) > 42:
                title = title[:39] + "..."
            label = f"[{proj}] {title}  ({cid[:8]})"
            self.combo_recent_chats.addItem(label, cid)
        self.combo_recent_chats.blockSignals(False)

    def _on_recent_chat_selected(self, index: int):
        if index <= 0:
            return
        cid = self.combo_recent_chats.itemData(index)
        if cid:
            self.inspect_conversation(cid)

    def _on_manual_refresh_clicked(self):
        self.refresh_recent_conversations()
        if self.current_conversation_id:
            self.inspect_conversation(self.current_conversation_id, preserve_selection=True)

    def _on_auto_refresh_tick(self):
        if self.current_conversation_id and self.current_report and self.current_report.get("is_running"):
            self.inspect_conversation(self.current_conversation_id, preserve_selection=True)
        else:
            self.refresh_timer.stop()

    def sync_conversation(self, conversation_id: str):
        """Syncs the selected conversation from another tab without re-emitting signals."""
        cid = extract_conversation_id(conversation_id)
        if cid and cid != self.current_conversation_id:
            self.inspect_conversation(cid, emit_sync=False)

    # ─────────────────────────────────────────────────────────────────────────
    # LIFECYCLE INSPECTION & REPORT RENDERING
    # ─────────────────────────────────────────────────────────────────────────

    def on_inspect_clicked(self):
        raw = self.txt_conversation_id.text().strip()
        cid = extract_conversation_id(raw)
        if not cid:
            QMessageBox.warning(
                self,
                "Invalid Conversation ID",
                "Please enter a valid Conversation UUID (e.g. 71d91ecf-6610-4c77-aee9-b80f617df473).",
            )
            return
        self.inspect_conversation(cid)

    def inspect_conversation(
        self,
        raw_id_or_url: str,
        preserve_selection: bool = False,
        emit_sync: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Loads and renders the full lifecycle report for the specified conversation ID."""
        cid = extract_conversation_id(raw_id_or_url)
        if not cid:
            return None

        report = inspect_conversation_lifecycle(cid)
        if not report.get("found"):
            self.lbl_status_badge.setText("❌ Conversation Not Found")
            self.lbl_status_badge.setStyleSheet(
                "background-color: #450a0a; color: #f87171; border: 1px solid #dc2626; border-radius: 4px; padding: 3px 10px; font-size: 11px; font-weight: 700;"
            )
            return None

        self.current_conversation_id = cid
        self.current_report = report
        if not self.txt_conversation_id.hasFocus():
            self.txt_conversation_id.setText(cid)

        # Sync dropdown if present
        self.combo_recent_chats.blockSignals(True)
        for i in range(self.combo_recent_chats.count()):
            if self.combo_recent_chats.itemData(i) == cid:
                self.combo_recent_chats.setCurrentIndex(i)
                break
        self.combo_recent_chats.blockSignals(False)

        self._render_report(report, preserve_selection=preserve_selection)

        # Manage auto-refresh for running agents
        if report.get("is_running"):
            if not self.refresh_timer.isActive():
                self.refresh_timer.start()
        else:
            self.refresh_timer.stop()

        if emit_sync:
            self.sig_conversation_selected.emit(cid)

        return report

    def _render_report(self, report: Dict[str, Any], preserve_selection: bool = False):
        short_id = report["conversation_id"][:8]
        overall_state = report.get("overall_state", "UNKNOWN")
        vis = _get_status_visuals(overall_state)

        # 1. Status Badge
        self.lbl_status_badge.setText(f"{vis['icon']} {vis['label']}")
        self.lbl_status_badge.setStyleSheet(
            f"background-color: {vis['bg']}; color: {vis['fg']}; "
            f"border: 1px solid {vis['border']}; border-radius: 4px; "
            f"padding: 3px 10px; font-size: 11px; font-weight: 700;"
        )

        # 2. Duration Chip
        dur_str = report.get("total_active_duration_formatted") or format_duration_human(
            report.get("total_active_duration_seconds", 0.0)
        )
        self.lbl_duration_chip.setText(f"⏱ Active: {dur_str}")

        # 3. Project Chip
        proj = report.get("project") or "Default"
        self.lbl_project_chip.setText(f"Project: {proj}")

        # 4. Action Buttons
        self.btn_open_chat.setEnabled(True)
        self.btn_open_brain.setEnabled(True)

        # 5. Table Header Count & Title
        total_prompts = int(report.get("total_prompts", 0))
        total_steps = int(report.get("total_steps", 0))
        total_tools = int(report.get("total_tool_calls", 0))
        self.lbl_turns_title.setText(
            f"⏱ Prompt History ({total_prompts} Prompts · {total_steps} Steps · {total_tools} Tools)"
        )
        chat_title = report.get("title", short_id)
        self.lbl_chat_title.setText(f"{chat_title}  ({short_id})")

        # 6. Diagnostic Banner
        if overall_state in ("QUOTA_EXHAUSTED", "HALTED_QUOTA", "STEP_ERROR", "EXECUTION_ERROR", "ERROR", "FAILED"):
            self.frame_diag_banner.setVisible(True)
            self.frame_diag_banner.setStyleSheet(
                f"QFrame {{ background-color: {vis['bg']}; border: 1px solid {vis['border']}; border-radius: 6px; }}"
            )
            reason = report.get("termination_detail") or f"Conversation terminated with {overall_state}."
            self.lbl_diag_text.setText(f"{vis['icon']}  {reason}")
            self.lbl_diag_text.setStyleSheet(
                f"color: {vis['fg']}; font-size: 11px; font-weight: 600; background: transparent; border: none;"
            )
            src = report.get("termination_source", "")
            self.lbl_diag_source.setText(f"Source: {src}" if src else "")
        else:
            self.frame_diag_banner.setVisible(False)

        # 7. Populate Per-Prompt Turns Table
        turns = report.get("prompt_turns", [])
        self.table_turns.blockSignals(True)
        prev_row = self.table_turns.currentRow()
        self.table_turns.setRowCount(len(turns))

        for row, turn in enumerate(turns):
            p_idx = turn.get("prompt_index", row + 1)
            is_res = bool(turn.get("is_auto_resume", False))
            idx_label = f"#{p_idx}" + (" (Resume)" if is_res else "")
            item_idx = QTableWidgetItem(idx_label)
            item_idx.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_idx.setFlags(item_idx.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_turns.setItem(row, 0, item_idx)

            full_prompt = str(turn.get("prompt_text") or "")
            single_line = " ".join(full_prompt.split())
            preview = single_line[:110] + "..." if len(single_line) > 110 else (single_line or "(No text)")
            item_prompt = QTableWidgetItem(preview)
            item_prompt.setToolTip(full_prompt)
            item_prompt.setFlags(item_prompt.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_turns.setItem(row, 1, item_prompt)

            item_start = QTableWidgetItem(str(turn.get("started_at", "-")))
            item_start.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_start.setFlags(item_start.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_turns.setItem(row, 2, item_start)

            dur_fmt = str(turn.get("duration_formatted") or format_duration_human(turn.get("duration_seconds", 0.0)))
            item_dur = QTableWidgetItem(dur_fmt)
            item_dur.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_dur.setForeground(QColor("#38bdf8"))
            item_dur.setFlags(item_dur.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_turns.setItem(row, 3, item_dur)

            s_cnt = turn.get("steps_count", 0)
            t_cnt = turn.get("tool_calls_count", 0)
            item_steps = QTableWidgetItem(f"{s_cnt} steps · {t_cnt} tools")
            item_steps.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_steps.setFlags(item_steps.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_turns.setItem(row, 4, item_steps)

            t_vis = _get_status_visuals(str(turn.get("status", "UNKNOWN")))
            item_st = QTableWidgetItem(f"{t_vis['icon']} {t_vis['label']}")
            item_st.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_st.setForeground(QColor(t_vis["fg"]))
            item_st.setFlags(item_st.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_turns.setItem(row, 5, item_st)

        self.table_turns.blockSignals(False)

        # 8. Selection Management
        target_row = 0
        if preserve_selection and 0 <= prev_row < len(turns):
            target_row = prev_row
        elif len(turns) > 0:
            target_row = len(turns) - 1

        if len(turns) > 0:
            self.table_turns.selectRow(target_row)
            self._select_turn(turns[target_row])
        else:
            self._clear_turn_detail()

        self._render_artifacts(report.get("artifacts", []))

        # 9. Subagent Fleet Orchestration
        sub_count = report.get("total_subagents", 0)
        cid = report.get("conversation_id", "")
        if sub_count > 0 and hasattr(self, "subagents_widget"):
            self.subagents_widget.setVisible(True)
            self.subagents_widget.load_conversation(cid, summary_data=report.get("subagents_summary"))
        elif hasattr(self, "subagents_widget"):
            self.subagents_widget.setVisible(False)
            self.subagents_widget.clear()

    def _on_turn_selection_changed(self):
        selected = self.table_turns.selectedItems()
        if not selected or not self.current_report:
            return
        row = self.table_turns.currentRow()
        turns = self.current_report.get("prompt_turns", [])
        if 0 <= row < len(turns):
            self._select_turn(turns[row])

    def _select_turn(self, turn: Dict[str, Any]):
        self._current_selected_turn = turn
        p_idx = turn.get("prompt_index", 1)
        self.lbl_selected_prompt_hdr.setText(f"💬 Prompt #{p_idx}:")

        prompt_text = str(turn.get("prompt_text") or "")
        self.txt_turn_prompt.setPlainText(prompt_text)

        self._render_response_text(turn)
        self._render_waterfall(turn)
        self._render_files_impacted(turn.get("files_impacted", []))

    def _clear_turn_detail(self):
        self._current_selected_turn = None
        self.txt_turn_prompt.clear()
        self.txt_turn_response.clear()
        self.lbl_waterfall_summary.setText("⚡ Model: 0s · 🛠️ Tools: 0s")
        self._clear_layout(self.waterfall_chips_layout)
        self._clear_layout(self.files_chips_layout)

    def _render_response_text(self, turn: Dict[str, Any]):
        if self._active_response_view == "thinking":
            self.lbl_selected_response_hdr.setText("🧠 Model Thinking (Chain of Thought):")
            text = str(turn.get("thinking_text") or "(No internal reasoning captured for this turn)")
            self.txt_turn_response.setPlainText(text)
        else:
            self.lbl_selected_response_hdr.setText("🤖 Agent Output:")
            text = str(turn.get("response_text") or "(No planner response output recorded for this turn)")
            self.txt_turn_response.setPlainText(text)

        if self.chk_resp_autoscroll.isChecked():
            cursor = self.txt_turn_response.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.txt_turn_response.setTextCursor(cursor)

    def _render_waterfall(self, turn: Dict[str, Any]):
        self._clear_layout(self.waterfall_chips_layout)

        m_sec = float(turn.get("total_model_duration_seconds", 0.0))
        t_sec = float(turn.get("total_tool_duration_seconds", 0.0))
        self.lbl_waterfall_summary.setText(
            f"⚡ Model: {format_duration_human(m_sec)} · 🛠️ Tools: {format_duration_human(t_sec)}"
        )

        wf_steps = turn.get("waterfall_steps", [])
        if not wf_steps:
            lbl_none = QLabel("No execution steps recorded")
            lbl_none.setStyleSheet(f"color: {COLOR_TEXT_FAINT}; font-size: 11px;")
            self.waterfall_chips_layout.addWidget(lbl_none)
            return

        for step in wf_steps:
            is_model = step.get("phase") == "model"
            name = step.get("name") or ("Model Gen" if is_model else "Tool")
            dur = step.get("duration_formatted", "0s")
            is_bottle = step.get("is_bottleneck", False)
            err = step.get("error", "")

            chip = QLabel()
            if err:
                chip.setText(f"❌ {name} ({dur})")
                chip.setStyleSheet(
                    "background-color: #450a0a; color: #f87171; border: 1px solid #dc2626; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: 600;"
                )
            elif is_bottle:
                chip.setText(f"⏳ {name} ({dur})")
                chip.setStyleSheet(
                    "background-color: #451a03; color: #fbbf24; border: 1px solid #d97706; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: 600;"
                )
            elif is_model:
                chip.setText(f"⚡ {name} ({dur})")
                chip.setStyleSheet(
                    "background-color: #0c4a6e; color: #38bdf8; border: 1px solid #0284c7; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: 600;"
                )
            else:
                chip.setText(f"🛠️ {name} ({dur})")
                chip.setStyleSheet(
                    "background-color: #1e293b; color: #94a3b8; border: 1px solid #334155; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: 600;"
                )

            summary = step.get("summary", "")
            if summary:
                chip.setToolTip(f"{name} ({dur})\n{summary}")
            self.waterfall_chips_layout.addWidget(chip)

    def _render_files_impacted(self, files: List[Dict[str, Any]]):
        self._clear_layout(self.files_chips_layout)

        if not files:
            lbl_none = QLabel("None in this turn")
            lbl_none.setStyleSheet(f"color: {COLOR_TEXT_FAINT}; font-size: 11px;")
            self.files_chips_layout.addWidget(lbl_none)
            return

        for f_item in files:
            name = f_item.get("name", "file")
            action = f_item.get("action", "Modified")
            path = f_item.get("path", "")

            chip = QLabel()
            if action == "Created":
                chip.setText(f"➕ {name}")
                chip.setStyleSheet(
                    "background-color: #064e3b; color: #34d399; border: 1px solid #059669; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: 600;"
                )
            elif action == "Modified":
                chip.setText(f"✏️ {name}")
                chip.setStyleSheet(
                    "background-color: #0c4a6e; color: #38bdf8; border: 1px solid #0284c7; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: 600;"
                )
            else:
                chip.setText(f"👁️ {name}")
                chip.setStyleSheet(
                    "background-color: #1e293b; color: #94a3b8; border: 1px solid #334155; border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: 600;"
                )

            chip.setToolTip(f"{action}: {path}")
            self.files_chips_layout.addWidget(chip)

    def _render_artifacts(self, artifacts: List[Dict[str, Any]]):
        self._clear_layout(self.artifacts_chips_layout)

        if not artifacts:
            lbl_none = QLabel("No artifacts found in brain directory")
            lbl_none.setStyleSheet(f"color: {COLOR_TEXT_FAINT}; font-size: 11px;")
            self.artifacts_chips_layout.addWidget(lbl_none)
            return

        for art in artifacts:
            name = art.get("name", "artifact")
            path = art.get("path", "")

            btn_art = QPushButton(f"📄 {name}")
            btn_art.setObjectName("btn-secondary")
            btn_art.setProperty("variant", "secondary")
            btn_art.setFixedHeight(22)
            btn_art.setStyleSheet("padding: 1px 8px; font-size: 10px;")
            btn_art.setToolTip(f"Open {path}")
            btn_art.clicked.connect(lambda checked, p=path: self._open_file(p))
            self.artifacts_chips_layout.addWidget(btn_art)

    def _clear_layout(self, layout):
        while layout.count() > 0:
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    # ─────────────────────────────────────────────────────────────────────────
    # UI EVENT HANDLERS & ACTIONS
    # ─────────────────────────────────────────────────────────────────────────

    def _on_segment_changed(self, segment_id: str):
        self._active_response_view = segment_id
        if self._current_selected_turn:
            self._render_response_text(self._current_selected_turn)

    def _on_autoscroll_toggled(self, checked: bool):
        if checked:
            cursor = self.txt_turn_response.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.txt_turn_response.setTextCursor(cursor)

    def _copy_prompt_text(self):
        text = self.txt_turn_prompt.toPlainText().strip()
        if text:
            QApplication.clipboard().setText(text)

    def _copy_response_text(self):
        text = self.txt_turn_response.toPlainText().strip()
        if text:
            QApplication.clipboard().setText(text)

    def _on_open_in_antigravity_clicked(self):
        if self.current_conversation_id:
            self.sig_open_in_antigravity_requested.emit(self.current_conversation_id)

    def _on_open_brain_folder_clicked(self):
        if not self.current_conversation_id:
            return
        brain_path = os.path.expanduser(f"~/.gemini/antigravity/brain/{self.current_conversation_id}")
        if os.path.isdir(brain_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(brain_path))
        else:
            QMessageBox.information(self, "Brain Folder", f"No local brain folder at:\n{brain_path}")

    def _open_file(self, file_path: str):
        if file_path and os.path.isfile(file_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(file_path))

    def _on_send_continuation_clicked(self):
        prompt = self.txt_continuation_prompt.text().strip()
        if not prompt:
            return
        if not self.current_conversation_id:
            QMessageBox.warning(self, "No Chat Selected", "Please select a conversation before sending a prompt.")
            return

        self.sig_send_prompt_in_chat_requested.emit(self.current_conversation_id, prompt)
        self.txt_continuation_prompt.clear()
        QTimer.singleShot(1500, lambda: self.inspect_conversation(self.current_conversation_id, preserve_selection=True))
