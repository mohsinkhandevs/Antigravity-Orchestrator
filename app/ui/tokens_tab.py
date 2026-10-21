"""
Tokens Tab: Dedicated Token Economics and Telemetry Dashboard.
Tracks uncached input tokens, context cache hits, generated output tokens,
per-turn token economics, step-level breakdown, and context window gauge.
"""

import os
import sys
from typing import Dict, Any, List, Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFrame,
    QSplitter,
    QMessageBox,
    QApplication,
    QProgressBar,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor

from app.ui.widgets import ResponsiveScrollContainer, ScrollableTableWidget
from app.ui.styles import (
    CARD_STYLE,
    COLOR_SURFACE_0,
    COLOR_SURFACE_1,
    COLOR_SURFACE_2,
    COLOR_BORDER_DEFAULT,
    COLOR_BORDER_SUBTLE,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_FAINT,
    COLOR_PRIMARY_HOVER,
)
from app.ui.subagents_widget import SubagentsWidget
from cdp.chat_lifecycle_inspector import (
    extract_conversation_id,
    format_duration_human,
    format_token_count,
    get_recent_conversations,
    inspect_conversation_lifecycle,
)


def _format_token_num(tokens: int) -> str:
    """Formats an integer token count with commas (e.g. 185,612)."""
    if tokens is None or tokens <= 0:
        return "0"
    return f"{tokens:,}"


class TokensTab(QWidget):
    """
    Dedicated tab for deep-dive token telemetry and economics:
    - 4 Executive KPI Metric Cards (Uncached In, Cached Context, Output, Peak Context)
    - Context Window progress gauge against 1.0M limit with /compact advisory alert
    - Turn-by-Turn token economics table (Prompt, In, Cached, Hit %, Out, Total, Peak)
    - Granular Step-by-Step API call token breakdown for selected turns
    - Copy token report summary utility
    """

    sig_conversation_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_conversation_id: str = ""
        self.current_report: Optional[Dict[str, Any]] = None
        self._current_selected_turn: Optional[Dict[str, Any]] = None
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
        self.btn_refresh.setToolTip("Refresh token data")
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

        self.lbl_project_chip = QLabel("Project: —")
        self.lbl_project_chip.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 600; padding: 0 4px;"
        )
        row2.addWidget(self.lbl_project_chip)

        self.lbl_model_badge = QLabel("Model: Gemini 3.8 Flash")
        self.lbl_model_badge.setStyleSheet(
            f"background-color: #0c4a6e; color: #38bdf8; border: 1px solid #0284c7; "
            f"border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 600;"
        )
        row2.addWidget(self.lbl_model_badge)

        row2.addStretch()

        self.btn_copy_summary = QPushButton("📋 Copy Token Summary")
        self.btn_copy_summary.setObjectName("btn-secondary")
        self.btn_copy_summary.setProperty("variant", "secondary")
        self.btn_copy_summary.setMinimumHeight(28)
        self.btn_copy_summary.setEnabled(False)
        self.btn_copy_summary.setToolTip("Copies token breakdown table to clipboard")
        self.btn_copy_summary.clicked.connect(self._copy_token_summary)
        row2.addWidget(self.btn_copy_summary)

        top_layout.addLayout(row2)
        self.scroll_area.add_card(top_frame)

        # ─────────────────────────────────────────────────────────────────────
        # 2. EXECUTIVE TOKEN KPI CARDS GRID (4 Modern Cards)
        # ─────────────────────────────────────────────────────────────────────
        kpi_frame = QFrame()
        kpi_frame.setStyleSheet(CARD_STYLE)
        kpi_layout = QGridLayout(kpi_frame)
        kpi_layout.setContentsMargins(12, 10, 12, 10)
        kpi_layout.setSpacing(10)
        kpi_layout.setColumnStretch(0, 1)
        kpi_layout.setColumnStretch(1, 1)
        kpi_layout.setColumnStretch(2, 1)
        kpi_layout.setColumnStretch(3, 1)

        # Card 1: Input Tokens (Uncached)
        self.lbl_kpi_input_tokens = QLabel("0")
        self.lbl_kpi_input_tokens.setStyleSheet("font-size: 22px; font-weight: 800; color: #38bdf8;")
        lbl_c1_sub = QLabel("📥 Uncached Input Tokens")
        lbl_c1_sub.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
        lbl_c1_desc = QLabel("New prompts & tool responses sent to model")
        lbl_c1_desc.setStyleSheet(f"font-size: 10px; color: {COLOR_TEXT_MUTED};")

        c1_box = QVBoxLayout()
        c1_box.setSpacing(2)
        c1_box.addWidget(lbl_c1_sub)
        c1_box.addWidget(self.lbl_kpi_input_tokens)
        c1_box.addWidget(lbl_c1_desc)
        kpi_layout.addLayout(c1_box, 0, 0)

        # Card 2: Cache Read Tokens
        self.lbl_kpi_cache_tokens = QLabel("0")
        self.lbl_kpi_cache_tokens.setStyleSheet("font-size: 22px; font-weight: 800; color: #34d399;")
        lbl_c2_sub = QLabel("⚡ Prompt Cache Read")
        lbl_c2_sub.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
        self.lbl_kpi_cache_rate = QLabel("0% Cache Hit Rate")
        self.lbl_kpi_cache_rate.setStyleSheet(
            "background-color: #064e3b; color: #34d399; border: 1px solid #059669; "
            "border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: 700;"
        )

        c2_top = QHBoxLayout()
        c2_top.addWidget(lbl_c2_sub)
        c2_top.addWidget(self.lbl_kpi_cache_rate)
        c2_top.addStretch()

        c2_box = QVBoxLayout()
        c2_box.setSpacing(2)
        c2_box.addLayout(c2_top)
        c2_box.addWidget(self.lbl_kpi_cache_tokens)
        lbl_c2_desc = QLabel("Reused context tokens (saves quota & latency)")
        lbl_c2_desc.setStyleSheet(f"font-size: 10px; color: {COLOR_TEXT_MUTED};")
        c2_box.addWidget(lbl_c2_desc)
        kpi_layout.addLayout(c2_box, 0, 1)

        # Card 3: Output Tokens
        self.lbl_kpi_output_tokens = QLabel("0")
        self.lbl_kpi_output_tokens.setStyleSheet("font-size: 22px; font-weight: 800; color: #a78bfa;")
        lbl_c3_sub = QLabel("📤 Output Tokens Generated")
        lbl_c3_sub.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
        self.lbl_kpi_speed = QLabel("⚡ — tok/s")
        self.lbl_kpi_speed.setStyleSheet(
            "background-color: #3b0764; color: #d8b4fe; border: 1px solid #7e22ce; "
            "border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: 700;"
        )

        c3_top = QHBoxLayout()
        c3_top.addWidget(lbl_c3_sub)
        c3_top.addWidget(self.lbl_kpi_speed)
        c3_top.addStretch()

        self.lbl_c3_desc = QLabel("Model reasoning (CoT), tool calls & responses")
        self.lbl_c3_desc.setStyleSheet(f"font-size: 10px; color: {COLOR_TEXT_MUTED};")

        c3_box = QVBoxLayout()
        c3_box.setSpacing(2)
        c3_box.addLayout(c3_top)
        c3_box.addWidget(self.lbl_kpi_output_tokens)
        c3_box.addWidget(self.lbl_c3_desc)
        kpi_layout.addLayout(c3_box, 0, 2)

        # Card 4: Peak Context Window Gauge
        self.lbl_kpi_peak_context = QLabel("0")
        self.lbl_kpi_peak_context.setStyleSheet("font-size: 22px; font-weight: 800; color: #f59e0b;")
        lbl_c4_sub = QLabel("🪟 Peak Context Window")
        lbl_c4_sub.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
        self.lbl_kpi_context_percent = QLabel("0% of 1.0M limit")
        self.lbl_kpi_context_percent.setStyleSheet(f"font-size: 10px; font-weight: 600; color: {COLOR_TEXT_MUTED};")

        c4_box = QVBoxLayout()
        c4_box.setSpacing(2)
        c4_box.addWidget(lbl_c4_sub)
        c4_box.addWidget(self.lbl_kpi_peak_context)
        c4_box.addWidget(self.lbl_kpi_context_percent)
        kpi_layout.addLayout(c4_box, 0, 3)

        self.scroll_area.add_card(kpi_frame)

        # ─────────────────────────────────────────────────────────────────────
        # 3. CONTEXT ADVISORY ALERT (Appears when Context >= 120,000 tokens)
        # ─────────────────────────────────────────────────────────────────────
        self.frame_ctx_warning = QFrame()
        self.frame_ctx_warning.setVisible(False)
        self.frame_ctx_warning.setStyleSheet(
            "background-color: #451a03; border: 1px solid #d97706; border-radius: 6px;"
        )
        ctx_warn_layout = QHBoxLayout(self.frame_ctx_warning)
        ctx_warn_layout.setContentsMargins(10, 6, 10, 6)
        ctx_warn_layout.setSpacing(8)

        self.lbl_ctx_warning = QLabel("")
        self.lbl_ctx_warning.setStyleSheet("color: #fbbf24; font-size: 11px; font-weight: 600;")
        ctx_warn_layout.addWidget(self.lbl_ctx_warning, 1)

        self.scroll_area.add_card(self.frame_ctx_warning)

        # ─────────────────────────────────────────────────────────────────────
        # 4. MASTER-DETAIL VERTICAL SPLITTER
        # ─────────────────────────────────────────────────────────────────────
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)

        # ── Top Pane: Turn-by-Turn Token Economics Table (~50% height) ──
        turns_frame = QFrame()
        turns_frame.setStyleSheet(CARD_STYLE)
        turns_layout = QVBoxLayout(turns_frame)
        turns_layout.setContentsMargins(10, 8, 10, 8)
        turns_layout.setSpacing(6)

        turns_header = QHBoxLayout()
        self.lbl_turns_title = QLabel("🪙 Turn-by-Turn Token Economics (0 Prompts)")
        self.lbl_turns_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
        turns_header.addWidget(self.lbl_turns_title)
        turns_header.addStretch()

        self.lbl_total_api_tokens = QLabel("Total API Tokens: —")
        self.lbl_total_api_tokens.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {COLOR_TEXT_SECONDARY};")
        turns_header.addWidget(self.lbl_total_api_tokens)
        turns_layout.addLayout(turns_header)

        self.table_turns = ScrollableTableWidget(0, 10)
        self.table_turns.setHorizontalHeaderLabels([
            "#",
            "Prompt Submitted",
            "Uncached In",
            "Cached Read",
            "Cache Hit %",
            "Output Tokens",
            "Gen Time",
            "Speed",
            "Turn Total",
            "Peak Context",
        ])
        self.table_turns.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_turns.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table_turns.setAlternatingRowColors(True)
        self.table_turns.verticalHeader().setVisible(False)
        self.table_turns.setShowGrid(False)
        self.table_turns.setMinimumHeight(160)
        self.table_turns.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.table_turns.itemSelectionChanged.connect(self._on_turn_selection_changed)

        hdr = self.table_turns.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(9, QHeaderView.ResizeMode.ResizeToContents)

        turns_layout.addWidget(self.table_turns)
        self.splitter.addWidget(turns_frame)

        # ── Bottom Pane: Step-by-Step API Calls Table (~50% height) ──
        steps_frame = QFrame()
        steps_frame.setStyleSheet(CARD_STYLE)
        steps_layout = QVBoxLayout(steps_frame)
        steps_layout.setContentsMargins(10, 8, 10, 8)
        steps_layout.setSpacing(6)

        steps_header = QHBoxLayout()
        self.lbl_steps_title = QLabel("⏱️ Step-by-Step API Calls for Selected Turn")
        self.lbl_steps_title.setStyleSheet(f"font-size: 12px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};")
        steps_header.addWidget(self.lbl_steps_title)
        steps_header.addStretch()

        self.lbl_turn_summary = QLabel("")
        self.lbl_turn_summary.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_MUTED};")
        steps_header.addWidget(self.lbl_turn_summary)
        steps_layout.addLayout(steps_header)

        self.table_steps = ScrollableTableWidget(0, 8)
        self.table_steps.setHorizontalHeaderLabels([
            "Step #",
            "Phase",
            "Name / Action",
            "Uncached In",
            "Cached Read",
            "Output Tokens",
            "Duration",
            "Speed",
        ])
        self.table_steps.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table_steps.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table_steps.setAlternatingRowColors(True)
        self.table_steps.verticalHeader().setVisible(False)
        self.table_steps.setShowGrid(False)
        self.table_steps.setMinimumHeight(160)
        self.table_steps.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        shdr = self.table_steps.horizontalHeader()
        shdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        shdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        shdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        shdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        shdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        shdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        shdr.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        shdr.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)

        steps_layout.addWidget(self.table_steps)
        self.splitter.addWidget(steps_frame)

        # ── Subagent Fleet Orchestration Panel ──
        self.subagents_widget = SubagentsWidget(self)
        self.subagents_widget.setVisible(False)
        self.splitter.addWidget(self.subagents_widget)

        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 1)
        self.splitter.setMinimumHeight(380)
        self.splitter.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.scroll_area.add_card(self.splitter, stretch=1)

    # ─────────────────────────────────────────────────────────────────────────
    # CONVERSATION SELECTION & RECENT CHATS
    # ─────────────────────────────────────────────────────────────────────────

    def update_worker_active_projects(self, active_projects_dict: Dict[str, Any]):
        """Retained for compatibility with main_window signal connections."""
        self._worker_active_projects = dict(active_projects_dict or {})

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
    # LIFECYCLE INSPECTION & TOKEN REPORT RENDERING
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
        """Loads and renders the full token report for the specified conversation ID."""
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

        self._render_token_dashboard(report, preserve_selection=preserve_selection)

        # Auto-refresh if running
        if report.get("is_running"):
            if not self.refresh_timer.isActive():
                self.refresh_timer.start()
        else:
            self.refresh_timer.stop()

        if emit_sync:
            self.sig_conversation_selected.emit(cid)

        return report

    def _render_token_dashboard(self, report: Dict[str, Any], preserve_selection: bool = False):
        short_id = report["conversation_id"][:8]
        proj = report.get("project") or "Default"
        is_running = report.get("is_running", False)

        # 1. Header Badges
        if is_running:
            self.lbl_status_badge.setText("🟢 RUNNING")
            self.lbl_status_badge.setStyleSheet(
                "background-color: #064e3b; color: #34d399; border: 1px solid #059669; border-radius: 4px; padding: 3px 10px; font-size: 11px; font-weight: 700;"
            )
        else:
            self.lbl_status_badge.setText("✔ COMPLETED")
            self.lbl_status_badge.setStyleSheet(
                "background-color: #14532d; color: #4ade80; border: 1px solid #16a34a; border-radius: 4px; padding: 3px 10px; font-size: 11px; font-weight: 700;"
            )

        self.lbl_project_chip.setText(f"Project: {proj}")
        self.btn_copy_summary.setEnabled(True)

        # 2. Executive KPI Metrics
        uncached_in = int(report.get("conversation_uncached_input_tokens", 0))
        cached_read = int(report.get("conversation_cache_tokens", 0))
        output_tokens = int(report.get("conversation_output_tokens", 0))
        total_tokens = int(report.get("conversation_total_tokens", 0))
        peak_context = int(report.get("conversation_peak_context", 0))
        cache_rate = float(report.get("conversation_cache_hit_rate", 0.0))

        self.lbl_kpi_input_tokens.setText(_format_token_num(uncached_in))
        self.lbl_kpi_cache_tokens.setText(_format_token_num(cached_read))
        self.lbl_kpi_cache_rate.setText(f"⚡ {cache_rate:.1f}% Cache Hit Rate")
        self.lbl_kpi_output_tokens.setText(_format_token_num(output_tokens))
        self.lbl_kpi_peak_context.setText(_format_token_num(peak_context))

        avg_speed = float(report.get("conversation_generation_speed", 0.0))
        conv_model_sec = float(report.get("conversation_model_duration_seconds", 0.0))
        conv_tool_sec = float(report.get("conversation_tool_duration_seconds", 0.0))
        if avg_speed > 0:
            self.lbl_kpi_speed.setText(f"⚡ {avg_speed:.1f} tok/s")
            self.lbl_kpi_speed.setToolTip(
                f"Pure LLM Generation Speed: {avg_speed:.1f} tok/s\n"
                f"Model Deliberation Time: {conv_model_sec:.1f}s\n"
                f"Tool Execution Time (excluded): {conv_tool_sec:.1f}s"
            )
            self.lbl_c3_desc.setText(f"Active Gen: {conv_model_sec:.1f}s · Pure LLM Speed")
        else:
            self.lbl_kpi_speed.setText("⚡ — tok/s")
            self.lbl_kpi_speed.setToolTip("Pure generation speed is calculated during active LLM token generation.")
            self.lbl_c3_desc.setText("Model reasoning (CoT), tool calls & responses")

        load_pct = (peak_context / 1_000_000.0) * 100.0
        self.lbl_kpi_context_percent.setText(f"{load_pct:.1f}% of 1.0M limit")

        if peak_context >= 200_000:
            self.lbl_kpi_peak_context.setStyleSheet("font-size: 22px; font-weight: 800; color: #ef4444;")
        elif peak_context >= 100_000:
            self.lbl_kpi_peak_context.setStyleSheet("font-size: 22px; font-weight: 800; color: #f59e0b;")
        else:
            self.lbl_kpi_peak_context.setStyleSheet("font-size: 22px; font-weight: 800; color: #10b981;")

        # 3. Context Advisory Alert Banner
        if peak_context >= 120_000:
            self.frame_ctx_warning.setVisible(True)
            self.lbl_ctx_warning.setText(
                f"⚠️ High Context Window: {peak_context:,} tokens ({load_pct:.1f}% of 1.0M limit). "
                f"Consider running /compact to compress conversation history, speed up generations, and save quota."
            )
        else:
            self.frame_ctx_warning.setVisible(False)

        # 4. Total API Tokens Header
        self.lbl_total_api_tokens.setText(f"Total API Processed: {total_tokens:,} tokens")

        # 5. Populate Turn-by-Turn Table
        turns = report.get("prompt_turns", [])
        total_prompts = len(turns)
        self.lbl_turns_title.setText(f"🪙 Turn-by-Turn Token Economics ({total_prompts} Prompts)")

        self.table_turns.blockSignals(True)
        prev_row = self.table_turns.currentRow()
        self.table_turns.setRowCount(len(turns))

        for row, turn in enumerate(turns):
            p_idx = turn.get("prompt_index", row + 1)
            is_res = bool(turn.get("is_auto_resume", False))
            idx_label = f"#{p_idx}" + (" (R)" if is_res else "")
            item_idx = QTableWidgetItem(idx_label)
            item_idx.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_idx.setFlags(item_idx.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_turns.setItem(row, 0, item_idx)

            full_prompt = str(turn.get("prompt_text") or "")
            single_line = " ".join(full_prompt.split())
            preview = single_line[:90] + "..." if len(single_line) > 90 else (single_line or "(No text)")
            item_prompt = QTableWidgetItem(preview)
            item_prompt.setToolTip(full_prompt)
            item_prompt.setFlags(item_prompt.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_turns.setItem(row, 1, item_prompt)

            t_in = int(turn.get("input_tokens", 0))
            item_in = QTableWidgetItem(_format_token_num(t_in))
            item_in.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item_in.setForeground(QColor("#38bdf8"))
            item_in.setFlags(item_in.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_turns.setItem(row, 2, item_in)

            t_cache = int(turn.get("cache_read_tokens", 0))
            item_cache = QTableWidgetItem(_format_token_num(t_cache))
            item_cache.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item_cache.setForeground(QColor("#34d399"))
            item_cache.setFlags(item_cache.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_turns.setItem(row, 3, item_cache)

            hit_rate = float(turn.get("cache_hit_rate", 0.0))
            item_rate = QTableWidgetItem(f"{hit_rate:.0f}%")
            item_rate.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if hit_rate >= 80:
                item_rate.setForeground(QColor("#34d399"))
            elif hit_rate >= 50:
                item_rate.setForeground(QColor("#fbbf24"))
            else:
                item_rate.setForeground(QColor("#94a3b8"))
            item_rate.setFlags(item_rate.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_turns.setItem(row, 4, item_rate)

            t_out = int(turn.get("output_tokens", 0))
            item_out = QTableWidgetItem(_format_token_num(t_out))
            item_out.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item_out.setForeground(QColor("#a78bfa"))
            item_out.setFlags(item_out.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_turns.setItem(row, 5, item_out)

            m_sec = float(turn.get("total_model_duration_seconds", 0.0))
            t_sec = float(turn.get("total_tool_duration_seconds", 0.0))
            item_gen = QTableWidgetItem(f"{m_sec:.1f}s" if m_sec > 0 else "—")
            item_gen.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item_gen.setForeground(QColor("#94a3b8"))
            item_gen.setToolTip(f"Model Deliberation: {m_sec:.1f}s\nTool Execution: {t_sec:.1f}s")
            item_gen.setFlags(item_gen.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_turns.setItem(row, 6, item_gen)

            turn_spd = float(turn.get("tokens_per_second", 0.0))
            wall_spd = float(turn.get("wall_clock_speed", 0.0))
            item_spd = QTableWidgetItem(f"{turn_spd:.1f} t/s" if turn_spd > 0 else "—")
            item_spd.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if turn_spd >= 60.0:
                item_spd.setForeground(QColor("#38bdf8"))
            elif turn_spd >= 30.0:
                item_spd.setForeground(QColor("#a78bfa"))
            elif turn_spd > 0:
                item_spd.setForeground(QColor("#fbbf24"))
            else:
                item_spd.setForeground(QColor("#64748b"))
            item_spd.setToolTip(
                f"Pure Generation Speed: {turn_spd:.1f} tok/s\n"
                f"Model Active Time: {m_sec:.1f}s\n"
                f"Tool Time (excluded): {t_sec:.1f}s\n"
                f"Overall Wall-Clock Speed: {wall_spd:.1f} tok/s"
            )
            item_spd.setFlags(item_spd.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_turns.setItem(row, 7, item_spd)

            t_tot = int(turn.get("total_tokens", 0))
            item_tot = QTableWidgetItem(_format_token_num(t_tot))
            item_tot.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item_tot.setFlags(item_tot.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_turns.setItem(row, 8, item_tot)

            t_peak = int(turn.get("context_window_peak", 0))
            item_peak = QTableWidgetItem(_format_token_num(t_peak))
            item_peak.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if t_peak >= 120_000:
                item_peak.setForeground(QColor("#fbbf24"))
            item_peak.setFlags(item_peak.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_turns.setItem(row, 9, item_peak)

        self.table_turns.blockSignals(False)

        # 6. Selection Management
        target_row = 0
        if preserve_selection and 0 <= prev_row < len(turns):
            target_row = prev_row
        elif len(turns) > 0:
            target_row = len(turns) - 1

        if len(turns) > 0:
            self.table_turns.selectRow(target_row)
            self._select_turn(turns[target_row])
        else:
            self.table_steps.setRowCount(0)
            self.lbl_steps_title.setText("⏱️ Step-by-Step API Calls for Selected Turn")
            self.lbl_turn_summary.setText("")

        # 7. Subagent Fleet Orchestration
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
        wf_steps = turn.get("waterfall_steps", [])

        t_in = int(turn.get("input_tokens", 0))
        t_cache = int(turn.get("cache_read_tokens", 0))
        t_out = int(turn.get("output_tokens", 0))
        t_tot = int(turn.get("total_tokens", 0))

        turn_spd = float(turn.get("tokens_per_second", 0.0))
        m_sec = float(turn.get("total_model_duration_seconds", 0.0))
        spd_info = f" · ⚡ {turn_spd:.1f} tok/s ({m_sec:.1f}s gen)" if turn_spd > 0 else ""
        self.lbl_steps_title.setText(
            f"⏱️ Step-by-Step API Calls for Turn #{p_idx} ({len(wf_steps)} steps)"
        )
        self.lbl_turn_summary.setText(
            f"Turn Total: {t_tot:,} tokens ({t_in:,} in · {t_cache:,} cached · {t_out:,} out){spd_info}"
        )

        self.table_steps.blockSignals(True)
        self.table_steps.setRowCount(len(wf_steps))

        for row, step in enumerate(wf_steps):
            s_idx = step.get("step_index", row + 1)
            item_idx = QTableWidgetItem(f"#{s_idx}")
            item_idx.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_idx.setFlags(item_idx.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_steps.setItem(row, 0, item_idx)

            phase = step.get("phase", "model")
            phase_label = "Model Deliberation" if phase == "model" else "Tool Execution"
            item_phase = QTableWidgetItem(phase_label)
            item_phase.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if phase == "model":
                item_phase.setForeground(QColor("#38bdf8"))
            else:
                item_phase.setForeground(QColor("#94a3b8"))
            item_phase.setFlags(item_phase.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_steps.setItem(row, 1, item_phase)

            name = step.get("name", "")
            summary = step.get("summary", "")
            item_name = QTableWidgetItem(name)
            item_name.setToolTip(f"{name}\n{summary}")
            item_name.setFlags(item_name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_steps.setItem(row, 2, item_name)

            s_in = int(step.get("input_tokens", 0))
            item_in = QTableWidgetItem(_format_token_num(s_in) if s_in > 0 else "—")
            item_in.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if s_in > 0:
                item_in.setForeground(QColor("#38bdf8"))
            item_in.setFlags(item_in.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_steps.setItem(row, 3, item_in)

            s_cache = int(step.get("cache_read_tokens", 0))
            item_cache = QTableWidgetItem(_format_token_num(s_cache) if s_cache > 0 else "—")
            item_cache.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if s_cache > 0:
                item_cache.setForeground(QColor("#34d399"))
            item_cache.setFlags(item_cache.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_steps.setItem(row, 4, item_cache)

            s_out = int(step.get("output_tokens", 0))
            item_out = QTableWidgetItem(_format_token_num(s_out) if s_out > 0 else "—")
            item_out.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if s_out > 0:
                item_out.setForeground(QColor("#a78bfa"))
            item_out.setFlags(item_out.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_steps.setItem(row, 5, item_out)

            dur = step.get("duration_formatted", "0s")
            item_dur = QTableWidgetItem(dur)
            item_dur.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_dur.setFlags(item_dur.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_steps.setItem(row, 6, item_dur)

            step_spd = float(step.get("tokens_per_second", 0.0))
            step_dur = float(step.get("duration_seconds", 0.0))
            item_spd = QTableWidgetItem(f"{step_spd:.1f} t/s" if step_spd > 0 else "—")
            item_spd.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if step_spd >= 60.0:
                item_spd.setForeground(QColor("#38bdf8"))
            elif step_spd > 0:
                item_spd.setForeground(QColor("#a78bfa"))
            else:
                item_spd.setForeground(QColor("#64748b"))
            if phase == "model" and step_dur > 0:
                item_spd.setToolTip(f"{s_out} output tokens / {step_dur:.1f}s active deliberation")
            item_spd.setFlags(item_spd.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_steps.setItem(row, 7, item_spd)

        self.table_steps.blockSignals(False)

    def _copy_token_summary(self):
        """Generates a clean text summary of the conversation tokens and copies it to clipboard."""
        if not self.current_report:
            return

        rep = self.current_report
        cid = rep.get("conversation_id", "")
        title = rep.get("title", cid[:8])
        uncached = rep.get("conversation_uncached_input_tokens", 0)
        cached = rep.get("conversation_cache_tokens", 0)
        output = rep.get("conversation_output_tokens", 0)
        total = rep.get("conversation_total_tokens", 0)
        peak = rep.get("conversation_peak_context", 0)
        rate = rep.get("conversation_cache_hit_rate", 0.0)
        conv_spd = rep.get("conversation_generation_speed", 0.0)
        gen_sec = rep.get("conversation_model_duration_seconds", 0.0)
        tool_sec = rep.get("conversation_tool_duration_seconds", 0.0)

        lines = [
            f"=== Token Economics Summary: {title} ({cid[:8]}) ===",
            f"Uncached Input Tokens: {uncached:,}",
            f"Cached Context Read:   {cached:,} ({rate:.1f}% cache hit rate)",
            f"Output Generated:      {output:,}",
            f"Pure Generation Speed: {conv_spd:.1f} tok/s ({gen_sec:.1f}s gen, {tool_sec:.1f}s tools)",
            f"Total API Processed:   {total:,}",
            f"Peak Context Window:   {peak:,} tokens ({(peak/1_000_000*100):.1f}% of 1.0M limit)",
            "",
            "Turn Breakdown:",
            "Turn | Uncached In | Cached Read | Cache Hit% | Output | Gen Time | Speed   | Turn Total | Peak Ctx",
            "-" * 95,
        ]

        for turn in rep.get("prompt_turns", []):
            idx = turn.get("prompt_index", 1)
            tin = turn.get("input_tokens", 0)
            tcache = turn.get("cache_read_tokens", 0)
            trate = turn.get("cache_hit_rate", 0.0)
            tout = turn.get("output_tokens", 0)
            m_sec = turn.get("total_model_duration_seconds", 0.0)
            tspd = turn.get("tokens_per_second", 0.0)
            ttot = turn.get("total_tokens", 0)
            tpeak = turn.get("context_window_peak", 0)
            lines.append(
                f"#{idx:02d}  | {tin:>11,} | {tcache:>11,} | {trate:>9.0f}% | {tout:>6,} | {m_sec:>7.1f}s | {tspd:>5.1f} t/s | {ttot:>10,} | {tpeak:>8,}"
            )

        summary_text = "\n".join(lines)
        QApplication.clipboard().setText(summary_text)
        QMessageBox.information(
            self,
            "Token Summary Copied",
            "Token telemetry summary copied to clipboard!",
        )
