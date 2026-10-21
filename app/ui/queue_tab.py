"""
Queue Tab: Prompt queue management, batch import/export, and invocation mode configuration.
Modernized to Linear / Fluent 2 design standards with zero functional regressions.
"""

import os
from typing import List, Dict, Any
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFileDialog,
    QMessageBox,
    QLabel,
    QMenu,
    QComboBox,
    QFrame,
    QDialog,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut, QColor

from app.models import (
    PromptItem,
    TaskStatus,
    ThreadSafePromptQueue,
    import_prompts_from_txt,
    export_prompts_to_txt,
    import_prompts_from_json,
    export_prompts_to_json,
    import_prompts_from_markdown,
    ensure_prompt_prefix,
    detect_prompt_mode,
)
from app.config import config, get_user_data_dir
from app.ui.styles import (
    CARD_STYLE,
    get_status_badge_html,
    COLOR_CANVAS,
    COLOR_SURFACE_0,
    COLOR_SURFACE_1,
    COLOR_SURFACE_2,
    COLOR_SURFACE_HOVER,
    COLOR_BORDER_DEFAULT,
    COLOR_BORDER_SUBTLE,
    COLOR_BORDER_STRONG,
    COLOR_PRIMARY,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_FAINT,
)
from app.ui.dialogs import PromptEditDialog, ImportPromptsDialog, DuplicateMultipleDialog
from app.ui.widgets import SegmentedControl, ModePillWidget, ResponsiveScrollContainer, ScrollableTableWidget


class QueueTab(QWidget):
    """Queue Management Panel with table editing, reordering, and import/export."""

    sig_queue_changed = pyqtSignal()
    sig_pause_project_requested = pyqtSignal(str)
    sig_resume_project_requested = pyqtSignal(str)
    sig_inspect_conversation_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.prompts: ThreadSafePromptQueue = ThreadSafePromptQueue()

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.scroll_area = ResponsiveScrollContainer(self, margin_h=12, margin_v=10, spacing=8)
        root_layout.addWidget(self.scroll_area)

        # ─────────────────────────────────────────────────────────────
        # 1. TOP ACTION TOOLBAR (CLUSTERED HIERARCHY)
        # ─────────────────────────────────────────────────────────────
        toolbar_frame = QFrame()
        toolbar_frame.setStyleSheet(CARD_STYLE)
        tb_main_layout = QVBoxLayout(toolbar_frame)
        tb_main_layout.setContentsMargins(12, 10, 12, 10)
        tb_main_layout.setSpacing(8)

        # ── Tier 1: Primary CRUD, Order & Segmented Mode ──
        tier1_bar = QHBoxLayout()
        tier1_bar.setContentsMargins(0, 0, 0, 0)
        tier1_bar.setSpacing(6)

        # CRUD cluster
        self.add_btn = QPushButton("+ Add")
        self.add_btn.setObjectName("btn-primary")
        self.add_btn.setProperty("variant", "primary")
        self.add_btn.setMinimumHeight(30)
        self.add_btn.clicked.connect(self._add_prompt)
        tier1_bar.addWidget(self.add_btn)

        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setObjectName("btn-secondary")
        self.edit_btn.setProperty("variant", "secondary")
        self.edit_btn.setMinimumHeight(30)
        self.edit_btn.clicked.connect(self._edit_selected_prompt)
        tier1_bar.addWidget(self.edit_btn)

        self.duplicate_btn = QPushButton("Duplicate")
        self.duplicate_btn.setObjectName("btn-secondary")
        self.duplicate_btn.setProperty("variant", "secondary")
        self.duplicate_btn.setMinimumHeight(30)
        self.duplicate_btn.setToolTip("Duplicate selected prompt (Ctrl+D)")
        self.duplicate_btn.clicked.connect(self._duplicate_selected_prompt)
        tier1_bar.addWidget(self.duplicate_btn)

        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setObjectName("btn-danger")
        self.delete_btn.setProperty("variant", "danger")
        self.delete_btn.setMinimumHeight(30)
        self.delete_btn.clicked.connect(self._delete_selected_prompt)
        tier1_bar.addWidget(self.delete_btn)

        div1 = self._create_toolbar_divider()
        tier1_bar.addWidget(div1)

        # Reorder cluster
        self.up_btn = QPushButton("▲ Up")
        self.up_btn.setObjectName("btn-secondary")
        self.up_btn.setProperty("variant", "ghost")
        self.up_btn.setMinimumHeight(30)
        self.up_btn.clicked.connect(lambda: self._move_selected(-1))
        tier1_bar.addWidget(self.up_btn)

        self.down_btn = QPushButton("▼ Down")
        self.down_btn.setObjectName("btn-secondary")
        self.down_btn.setProperty("variant", "ghost")
        self.down_btn.setMinimumHeight(30)
        self.down_btn.clicked.connect(lambda: self._move_selected(1))
        tier1_bar.addWidget(self.down_btn)

        div2 = self._create_toolbar_divider()
        tier1_bar.addWidget(div2)

        # Mode Segmented Control
        self.mode_segmented = SegmentedControl(self)
        self.btn_set_goal = self.mode_segmented.add_segment("/goal", "🎯  /goal")
        self.btn_set_plan = self.mode_segmented.add_segment("/plan", "📋  /plan")
        self.btn_set_goal.setToolTip("Set selected prompt to /goal mode")
        self.btn_set_plan.setToolTip("Set selected prompt to /plan mode")
        self.mode_segmented.sig_segment_changed.connect(lambda mode: self._set_selected_mode(mode))
        tier1_bar.addWidget(self.mode_segmented)

        tier1_bar.addStretch()

        self.reconcile_btn = QPushButton("⚡ Reconcile")
        self.reconcile_btn.setObjectName("btn-secondary")
        self.reconcile_btn.setProperty("variant", "secondary")
        self.reconcile_btn.setMinimumHeight(30)
        self.reconcile_btn.setToolTip("Check Antigravity background status for prompts with a Conversation ID")
        self.reconcile_btn.clicked.connect(lambda: self.reconcile_all_prompts(notify=True))
        tier1_bar.addWidget(self.reconcile_btn)

        tb_main_layout.addLayout(tier1_bar)

        # ── Tier 2: Filters & Batch Operations ──
        tier2_bar = QHBoxLayout()
        tier2_bar.setContentsMargins(0, 0, 0, 0)
        tier2_bar.setSpacing(6)

        lbl_filter = QLabel("Project:")
        lbl_filter.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; margin-left: 2px;")
        tier2_bar.addWidget(lbl_filter)

        self.filter_project_combo = QComboBox()
        self.filter_project_combo.setMinimumHeight(30)
        self.filter_project_combo.setStyleSheet(
            f"QComboBox {{ background: {COLOR_SURFACE_2}; color: {COLOR_TEXT_PRIMARY}; border: 1px solid {COLOR_BORDER_DEFAULT}; "
            f"border-radius: 6px; padding: 2px 8px; font-size: 11px; font-weight: 600; min-width: 130px; }}"
            f"QComboBox:hover {{ border-color: #38bdf8; }}"
            f"QComboBox QAbstractItemView {{ background: {COLOR_SURFACE_1}; color: {COLOR_TEXT_PRIMARY}; selection-background-color: {COLOR_PRIMARY}; }}"
        )
        self.filter_project_combo.addItem("All Projects")
        self.filter_project_combo.currentIndexChanged.connect(self._apply_project_filter)
        tier2_bar.addWidget(self.filter_project_combo)

        lbl_queue_filter = QLabel("Queue:")
        lbl_queue_filter.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; margin-left: 4px;")
        tier2_bar.addWidget(lbl_queue_filter)

        self.filter_queue_combo = QComboBox()
        self.filter_queue_combo.setMinimumHeight(30)
        self.filter_queue_combo.setStyleSheet(
            f"QComboBox {{ background: {COLOR_SURFACE_2}; color: {COLOR_TEXT_PRIMARY}; border: 1px solid {COLOR_BORDER_DEFAULT}; "
            f"border-radius: 6px; padding: 2px 8px; font-size: 11px; font-weight: 600; min-width: 120px; }}"
            f"QComboBox:hover {{ border-color: #38bdf8; }}"
            f"QComboBox QAbstractItemView {{ background: {COLOR_SURFACE_1}; color: {COLOR_TEXT_PRIMARY}; selection-background-color: {COLOR_PRIMARY}; }}"
        )
        self.filter_queue_combo.addItem("All Queues")
        self.filter_queue_combo.currentIndexChanged.connect(self._apply_project_filter)
        tier2_bar.addWidget(self.filter_queue_combo)

        tier2_bar.addStretch()

        self.import_btn = QPushButton("Import")
        self.import_btn.setObjectName("btn-secondary")
        self.import_btn.setProperty("variant", "secondary")
        self.import_btn.setMinimumHeight(30)
        self.import_btn.clicked.connect(self._import_queue)
        tier2_bar.addWidget(self.import_btn)

        self.export_btn = QPushButton("Export")
        self.export_btn.setObjectName("btn-secondary")
        self.export_btn.setProperty("variant", "secondary")
        self.export_btn.setMinimumHeight(30)
        self.export_btn.clicked.connect(self._export_queue)
        tier2_bar.addWidget(self.export_btn)

        div3 = self._create_toolbar_divider()
        tier2_bar.addWidget(div3)

        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setObjectName("btn-secondary")
        self.reset_btn.setProperty("variant", "secondary")
        self.reset_btn.setMinimumHeight(30)
        self.reset_btn.setToolTip("Reset completed/failed prompts back to Pending")
        self.reset_btn.clicked.connect(self._reset_statuses)
        tier2_bar.addWidget(self.reset_btn)

        self.clear_completed_btn = QPushButton("Clear Completed")
        self.clear_completed_btn.setObjectName("btn-secondary")
        self.clear_completed_btn.setProperty("variant", "secondary")
        self.clear_completed_btn.setMinimumHeight(30)
        self.clear_completed_btn.setToolTip("Remove only completed prompts from the queue")
        self.clear_completed_btn.clicked.connect(self._clear_completed_prompts)
        tier2_bar.addWidget(self.clear_completed_btn)

        self.clear_btn = QPushButton("Clear All")
        self.clear_btn.setObjectName("btn-danger")
        self.clear_btn.setProperty("variant", "danger")
        self.clear_btn.setMinimumHeight(30)
        self.clear_btn.setToolTip("Remove all prompts from the queue")
        self.clear_btn.clicked.connect(self._clear_queue)
        tier2_bar.addWidget(self.clear_btn)

        tb_main_layout.addLayout(tier2_bar)
        self.scroll_area.add_card(toolbar_frame)

        # ─────────────────────────────────────────────────────────────
        # 1b. PARALLEL PROJECTS HUD
        # ─────────────────────────────────────────────────────────────
        self.hud_frame = QFrame()
        self.hud_frame.setStyleSheet(CARD_STYLE)
        hud_layout = QHBoxLayout(self.hud_frame)
        hud_layout.setContentsMargins(10, 6, 10, 6)
        hud_layout.setSpacing(12)

        lbl_hud_title = QLabel("⚡ Active Projects:")
        lbl_hud_title.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.5px;")
        hud_layout.addWidget(lbl_hud_title)

        self.hud_chips_layout = QHBoxLayout()
        self.hud_chips_layout.setSpacing(8)
        hud_layout.addLayout(self.hud_chips_layout)

        self.hud_placeholder_label = QLabel("No active parallel projects currently generating.")
        self.hud_placeholder_label.setStyleSheet(f"color: {COLOR_TEXT_FAINT}; font-size: 11px; font-style: italic;")
        self.hud_chips_layout.addWidget(self.hud_placeholder_label)

        hud_layout.addStretch()
        self.hud_frame.setVisible(False)
        self.scroll_area.add_card(self.hud_frame)

        # ─────────────────────────────────────────────────────────────
        # 2. QUEUE TABLE (Responsive Height & Stretchable Prompt Column)
        # ─────────────────────────────────────────────────────────────
        self.table = ScrollableTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(["#", "Project", "Queue", "Prompt", "Status", "Duration", "Speed", "Details", "Mode"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)

        self.table.setColumnWidth(0, 38)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(4, 110)
        self.table.setColumnWidth(5, 80)
        self.table.setColumnWidth(6, 90)
        self.table.setColumnWidth(7, 140)
        self.table.setColumnWidth(8, 110)

        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setMinimumHeight(320)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.table.doubleClicked.connect(self._edit_selected_prompt)

        # Right-click context menu
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        # Keyboard shortcut: Ctrl+D to duplicate selected prompt
        self._shortcut_duplicate = QShortcut(QKeySequence("Ctrl+D"), self)
        self._shortcut_duplicate.activated.connect(self._duplicate_selected_prompt)

        self.scroll_area.add_card(self.table, stretch=1)

        # ─────────────────────────────────────────────────────────────
        # 3. BOTTOM INFO & METRICS BAR
        # ─────────────────────────────────────────────────────────────
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(10)

        self.lbl_stats = QLabel("Total: 0 (0 pending)")
        self.lbl_stats.setObjectName("lbl-badge")
        bottom_bar.addWidget(self.lbl_stats)

        bottom_bar.addStretch()
        self.scroll_area.add_layout(bottom_bar)

    def _create_toolbar_divider(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet(f"color: {COLOR_BORDER_DEFAULT}; background-color: {COLOR_BORDER_DEFAULT}; width: 1px; margin: 4px 3px;")
        return div

    def get_queue_file_path(self) -> str:
        return os.path.join(get_user_data_dir(), "queue.json")

    def save_queue(self):
        """Persists current prompts, statuses, and durations to queue.json."""
        try:
            export_prompts_to_json(self.prompts, self.get_queue_file_path(), allow_empty=True)
        except Exception as e:
            print(f"[WARN] Auto-saving queue.json failed: {e}")

    def load_initial_prompts(self, default_prompts: List[str]):
        """Loads saved prompts and their completion statuses from queue.json if available, else defaults."""
        queue_path = self.get_queue_file_path()
        loaded = []
        if os.path.exists(queue_path):
            try:
                loaded = import_prompts_from_json(queue_path)
            except Exception as e:
                print(f"[WARN] Failed to load saved queue.json: {e}")

        if loaded:
            with self.prompts.lock:
                self.prompts[:] = loaded
            # 1. Auto-reconcile prompts with conversation_id against Antigravity background state
            # 2. Normalize any remaining orphan Running prompts from abrupt shutdowns to Failed/Interrupted,
            #    and clear any stale conversation_id on Pending prompts
            dirty = False
            from cdp.reconciliation import reconcile_prompt_status_from_conversation
            with self.prompts.lock:
                for p in self.prompts:
                    if p.conversation_id and p.status in (TaskStatus.RUNNING, TaskStatus.FAILED):
                        try:
                            if reconcile_prompt_status_from_conversation(p):
                                dirty = True
                        except Exception as ex:
                            print(f"[WARN] Failed to auto-reconcile prompt {p.id}: {ex}")

                    if p.status == TaskStatus.RUNNING and p.error_message != "Actively generating in background":
                        p.status = TaskStatus.FAILED
                        p.error_message = "Interrupted by unexpected application shutdown/restart"
                        dirty = True
                    elif p.status == TaskStatus.PENDING and p.conversation_id:
                        p.conversation_id = ""
                        dirty = True
            if dirty:
                self.save_queue()
        else:
            with self.prompts.lock:
                for p in default_prompts:
                    self.prompts.append(PromptItem(text=p))
            self.save_queue()

        self.refresh_table()

    def _sync_project_filter_items(self):
        """Populates the project and queue filter combos with distinct values present in queue and config."""
        # 1. Project Filter
        current_selection = self.filter_project_combo.currentText().strip()
        known = set(getattr(config, "known_projects", ["No Project"]))
        for p in self.prompts:
            if p.project.strip():
                known.add(p.project.strip())

        self.filter_project_combo.blockSignals(True)
        self.filter_project_combo.clear()
        self.filter_project_combo.addItem("All Projects")
        for proj in sorted(known):
            self.filter_project_combo.addItem(proj)

        idx = self.filter_project_combo.findText(current_selection)
        if idx != -1:
            self.filter_project_combo.setCurrentIndex(idx)
        else:
            self.filter_project_combo.setCurrentIndex(0)
        self.filter_project_combo.blockSignals(False)

        # 2. Queue Filter
        if hasattr(self, "filter_queue_combo"):
            current_q_selection = self.filter_queue_combo.currentText().strip()
            known_queues = {"Default"}
            for p in self.prompts:
                q_name = (getattr(p, "queue_name", "") or "").strip()
                if q_name:
                    known_queues.add(q_name)

            self.filter_queue_combo.blockSignals(True)
            self.filter_queue_combo.clear()
            self.filter_queue_combo.addItem("All Queues")
            for q in sorted(known_queues):
                self.filter_queue_combo.addItem(q)

            q_idx = self.filter_queue_combo.findText(current_q_selection)
            if q_idx != -1:
                self.filter_queue_combo.setCurrentIndex(q_idx)
            else:
                self.filter_queue_combo.setCurrentIndex(0)
            self.filter_queue_combo.blockSignals(False)

    def _apply_project_filter(self):
        """Filters table rows based on selected project and queue."""
        sel_proj = self.filter_project_combo.currentText().strip()
        sel_queue = self.filter_queue_combo.currentText().strip() if hasattr(self, "filter_queue_combo") else "All Queues"
        default_p = "No Project"
        visible_count = 0
        for row, item in enumerate(self.prompts):
            raw_p = item.project.strip()
            item_proj = raw_p if (raw_p and raw_p.lower() not in ("default", "none")) else default_p
            match_proj = (sel_proj == "All Projects" or item_proj.lower() == sel_proj.lower())

            raw_q = (getattr(item, "queue_name", "") or "").strip() or "Default"
            match_queue = (sel_queue == "All Queues" or raw_q.lower() == sel_queue.lower())

            match = match_proj and match_queue
            self.table.setRowHidden(row, not match)
            if match:
                visible_count += 1

        pending_count = sum(1 for p in self.prompts if p.status == TaskStatus.PENDING)
        if sel_proj != "All Projects" or sel_queue != "All Queues":
            self.lbl_stats.setText(f"Filtered: {visible_count}/{len(self.prompts)} ({pending_count} pending)")
        else:
            self.lbl_stats.setText(f"Total: {len(self.prompts)} ({pending_count} pending)")

    def refresh_table(self):
        """Re-renders the table rows and updates statistics."""
        self._sync_project_filter_items()
        self.table.setRowCount(len(self.prompts))

        default_proj = "Default"
        default_queue = "Default"
        pending_count = 0
        for row, item in enumerate(self.prompts):
            if item.status == TaskStatus.PENDING:
                pending_count += 1

            # 0. Index
            idx_item = QTableWidgetItem(f"{row + 1}")
            idx_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            idx_item.setFlags(idx_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, idx_item)

            # 1. Project
            proj_display = (item.project.strip() or default_proj)
            proj_item = QTableWidgetItem(proj_display)
            proj_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            proj_item.setToolTip(f"Project: {proj_display}")
            proj_item.setFlags(proj_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, proj_item)

            # 2. Queue
            queue_display = (getattr(item, "queue_name", "") or "").strip() or default_queue
            queue_item = QTableWidgetItem(queue_display)
            queue_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            queue_item.setToolTip(f"Queue: {queue_display}")
            queue_item.setFlags(queue_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 2, queue_item)

            # 3. Prompt Text
            text_preview = item.text.replace("\n", " ")
            if len(text_preview) > 100:
                text_preview = text_preview[:97] + "..."
            text_item = QTableWidgetItem(text_preview)
            tooltip_preview = item.text[:1500] + "..." if len(item.text) > 1500 else item.text
            text_item.setToolTip(tooltip_preview)
            text_item.setFlags(text_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 3, text_item)

            # 4. Status Badge
            badge_lbl = QLabel()
            badge_lbl.setTextFormat(Qt.TextFormat.RichText)
            badge_lbl.setText(get_status_badge_html(item.status.value))
            badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setCellWidget(row, 4, badge_lbl)

            # 5. Duration
            dur_str = f"{item.elapsed_time:.1f}s" if item.elapsed_time > 0 else "-"
            dur_item = QTableWidgetItem(dur_str)
            dur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            dur_item.setFlags(dur_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if item.output_tokens > 0:
                dur_tt = (
                    f"Wall-Clock: {item.elapsed_time:.1f}s\n"
                    f"Model Gen: {item.generation_time:.1f}s\n"
                    f"Tools / Non-Gen: {item.tool_time:.1f}s\n"
                    f"Output Tokens: {item.output_tokens:,}\n"
                    f"Speed: {item.tokens_per_second:.1f} tok/s"
                )
                dur_item.setToolTip(dur_tt)
            self.table.setItem(row, 5, dur_item)

            # 6. Speed (Pure Model Generation Speed)
            speed_str = f"{item.tokens_per_second:.1f} t/s" if item.tokens_per_second > 0 else "-"
            speed_item = QTableWidgetItem(speed_str)
            speed_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            speed_item.setFlags(speed_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if item.tokens_per_second > 0:
                speed_item.setForeground(QColor("#38bdf8"))
                speed_item.setToolTip(
                    f"Pure Model Generation Speed: {item.tokens_per_second:.1f} tokens/sec\n"
                    f"Output Tokens: {item.output_tokens:,}\n"
                    f"Generation Duration: {item.generation_time:.1f}s\n"
                    f"(Excludes tool execution and non-generation time)"
                )
            else:
                speed_item.setForeground(QColor("#64748b"))
            self.table.setItem(row, 6, speed_item)

            # 7. Error / Details
            details_str = item.error_message
            if item.is_resumed:
                resumed_tag = f"[Resumed {item.resume_count}x]"
                details_str = f"{resumed_tag} {details_str}".strip()
            err_item = QTableWidgetItem(details_str)
            err_item.setToolTip(details_str)
            if item.error_message:
                err_item.setForeground(Qt.GlobalColor.red)
            elif item.is_resumed:
                err_item.setForeground(Qt.GlobalColor.cyan)
            err_item.setFlags(err_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 7, err_item)

            # 8. Invoke Mode pill badge
            cur_mode = detect_prompt_mode(item.text)
            mode_pill = ModePillWidget(cur_mode, self)

            def make_mode_handler(target_idx):
                def handler(selected_mode):
                    if 0 <= target_idx < len(self.prompts):
                        p_item = self.prompts[target_idx]
                        new_text = ensure_prompt_prefix(p_item.text, selected_mode)
                        if new_text != p_item.text:
                            p_item.text = new_text
                            preview = new_text.replace("\n", " ")
                            if len(preview) > 100:
                                preview = preview[:97] + "..."
                            txt_w = self.table.item(target_idx, 3)
                            if txt_w:
                                txt_w.setText(preview)
                                txt_w.setToolTip(new_text[:1500] + "..." if len(new_text) > 1500 else new_text)
                            self.save_queue()
                            self.sig_queue_changed.emit()
                return handler

            mode_pill.sig_mode_changed.connect(make_mode_handler(row))
            self.table.setCellWidget(row, 8, mode_pill)

        self._apply_project_filter()
        self.sig_queue_changed.emit()

    def refresh_row(self, row: int):
        """Refreshes a single row's badge, duration, error, and mode with immediate repaint."""
        if not (0 <= row < len(self.prompts)):
            return
        item = self.prompts[row]

        # 1. Update Project (Column 1)
        default_proj = "Default"
        proj_display = (item.project.strip() or default_proj)
        p_item = self.table.item(row, 1)
        if p_item:
            p_item.setText(proj_display)
        else:
            p_item = QTableWidgetItem(proj_display)
            p_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            p_item.setFlags(p_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, p_item)

        # 2. Update Queue (Column 2)
        default_queue = "Default"
        queue_display = (getattr(item, "queue_name", "") or "").strip() or default_queue
        q_item = self.table.item(row, 2)
        if q_item:
            q_item.setText(queue_display)
        else:
            q_item = QTableWidgetItem(queue_display)
            q_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            q_item.setFlags(q_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 2, q_item)

        # 3. Update Status badge (Column 4)
        badge_lbl = self.table.cellWidget(row, 4)
        if isinstance(badge_lbl, QLabel):
            badge_lbl.setText(get_status_badge_html(item.status.value))
            badge_lbl.update()
        else:
            new_badge = QLabel()
            new_badge.setTextFormat(Qt.TextFormat.RichText)
            new_badge.setText(get_status_badge_html(item.status.value))
            new_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setCellWidget(row, 4, new_badge)

        # 4. Update Duration (Column 5)
        dur_str = f"{item.elapsed_time:.1f}s" if item.elapsed_time > 0 else "-"
        dur_item = self.table.item(row, 5)
        if dur_item:
            dur_item.setText(dur_str)
        else:
            dur_item = QTableWidgetItem(dur_str)
            dur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            dur_item.setFlags(dur_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 5, dur_item)
        if item.output_tokens > 0:
            dur_tt = (
                f"Wall-Clock: {item.elapsed_time:.1f}s\n"
                f"Model Gen: {item.generation_time:.1f}s\n"
                f"Tools / Non-Gen: {item.tool_time:.1f}s\n"
                f"Output Tokens: {item.output_tokens:,}\n"
                f"Speed: {item.tokens_per_second:.1f} tok/s"
            )
            dur_item.setToolTip(dur_tt)

        # 5. Update Speed (Column 6)
        speed_str = f"{item.tokens_per_second:.1f} t/s" if item.tokens_per_second > 0 else "-"
        speed_item = self.table.item(row, 6)
        if speed_item:
            speed_item.setText(speed_str)
        else:
            speed_item = QTableWidgetItem(speed_str)
            speed_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            speed_item.setFlags(speed_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 6, speed_item)
        if item.tokens_per_second > 0:
            speed_item.setForeground(QColor("#38bdf8"))
            speed_item.setToolTip(
                f"Pure Model Generation Speed: {item.tokens_per_second:.1f} tokens/sec\n"
                f"Output Tokens: {item.output_tokens:,}\n"
                f"Generation Duration: {item.generation_time:.1f}s\n"
                f"(Excludes tool execution and non-generation time)"
            )
        else:
            speed_item.setForeground(QColor("#64748b"))
            speed_item.setToolTip("")

        # 6. Update Error / Details (Column 7)
        details_str = item.error_message
        if item.is_resumed:
            resumed_tag = f"[Resumed {item.resume_count}x]"
            details_str = f"{resumed_tag} {details_str}".strip()
        err_item = self.table.item(row, 7)
        if err_item:
            err_item.setText(details_str)
            if item.error_message:
                err_item.setForeground(Qt.GlobalColor.red)
                err_item.setToolTip(details_str)
            elif item.is_resumed:
                err_item.setForeground(Qt.GlobalColor.cyan)
                err_item.setToolTip(details_str)
            else:
                err_item.setToolTip("")
        else:
            err_item = QTableWidgetItem(details_str)
            err_item.setFlags(err_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if item.error_message:
                err_item.setForeground(Qt.GlobalColor.red)
            elif item.is_resumed:
                err_item.setForeground(Qt.GlobalColor.cyan)
            self.table.setItem(row, 7, err_item)

        # 7. Sync Invoke Mode widget (Column 8)
        mode_w = self.table.cellWidget(row, 8)
        if isinstance(mode_w, ModePillWidget):
            cur_mode = detect_prompt_mode(item.text)
            mode_w.set_mode(cur_mode, notify=False)
        elif isinstance(mode_w, QComboBox):
            cur_mode = detect_prompt_mode(item.text)
            target_idx = 1 if cur_mode == "/plan" else (0 if cur_mode == "/goal" else 2)
            if mode_w.currentIndex() != target_idx:
                mode_w.blockSignals(True)
                mode_w.setCurrentIndex(target_idx)
                mode_w.blockSignals(False)

        # Force immediate Qt viewport repaint for real-time live display
        self.table.viewport().update()

    def update_prompt_status(
        self,
        prompt_id: str,
        status: TaskStatus,
        elapsed: float = 0.0,
        error_msg: str = "",
        row_idx: int = -1,
    ):
        """Updates a specific prompt's status in place with guaranteed real-time UI repaint."""
        target_row = -1
        if prompt_id:
            for row, item in enumerate(self.prompts):
                if item.id == prompt_id:
                    target_row = row
                    break
        if target_row == -1 and 0 <= row_idx < len(self.prompts) and not prompt_id:
            target_row = row_idx

        if target_row == -1:
            return

        item = self.prompts[target_row]
        item.status = status
        if elapsed > 0:
            item.elapsed_time = elapsed
        if error_msg:
            item.error_message = error_msg

        self.refresh_row(target_row)

        # Refresh stats & notify progress
        self._apply_project_filter()
        self.sig_queue_changed.emit()
        self.save_queue()

    def update_prompt_progress(self, prompt_id: str, elapsed: float, row_idx: int = -1):
        """Updates elapsed seconds for running prompt in real-time."""
        target_row = -1
        if prompt_id:
            for row, item in enumerate(self.prompts):
                if item.id == prompt_id:
                    target_row = row
                    break
        if target_row == -1 and 0 <= row_idx < len(self.prompts) and not prompt_id:
            target_row = row_idx

        if target_row != -1:
            item = self.prompts[target_row]
            item.elapsed_time = elapsed
            dur_item = self.table.item(target_row, 5)
            if dur_item:
                dur_item.setText(f"{elapsed:.1f}s")
            self.table.viewport().update()

    def _add_prompt(self):
        cur_filter = self.filter_project_combo.currentText().strip()
        default_proj = cur_filter if cur_filter != "All Projects" else "Default"
        cur_q_filter = self.filter_queue_combo.currentText().strip() if hasattr(self, "filter_queue_combo") else "All Queues"
        default_queue = cur_q_filter if cur_q_filter != "All Queues" else "Default"
        dlg = PromptEditDialog(self, title="Add New Prompt", initial_text="/goal ", initial_project=default_proj, initial_queue=default_queue)
        if dlg.exec():
            text = dlg.get_text()
            proj = dlg.get_project()
            queue_name = dlg.get_queue_name()
            if text:
                self.prompts.append(PromptItem(text=text, project=proj, queue_name=queue_name))
                self.refresh_table()
                self.save_queue()
                self._sync_project_filter_items()
                self.sig_queue_changed.emit()

    def _edit_selected_prompt(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.prompts):
            return
        item = self.prompts[row]
        dlg = PromptEditDialog(
            self,
            initial_text=item.text,
            initial_project=item.project or "Default",
            initial_queue=getattr(item, "queue_name", "Default") or "Default",
            title="Edit Prompt"
        )
        if dlg.exec():
            text = dlg.get_text()
            proj = dlg.get_project()
            queue_name = dlg.get_queue_name()
            if text:
                item.text = text
                item.project = proj
                item.queue_name = queue_name
                self.refresh_table()
                self.save_queue()
                self._sync_project_filter_items()
                self.sig_queue_changed.emit()

    def _duplicate_selected_prompt(self):
        """Duplicates the selected prompt item and inserts it directly below with status Pending."""
        row = self.table.currentRow()
        if row < 0 or row >= len(self.prompts):
            if not self.prompts:
                return
            row = len(self.prompts) - 1

        original_item = self.prompts[row]
        new_item = original_item.duplicate()

        # Insert immediately below the current item
        insert_idx = row + 1
        self.prompts.insert(insert_idx, new_item)
        self.refresh_table()
        self.table.selectRow(insert_idx)
        first_item = self.table.item(insert_idx, 0)
        if first_item:
            self.table.scrollToItem(first_item)
        self.save_queue()
        self._sync_project_filter_items()
        self.sig_queue_changed.emit()

    def _duplicate_multiple_selected_prompt(self, count: int = None):
        """Creates multiple duplicates of the selected prompt and inserts them directly below."""
        row = self.table.currentRow()
        if row < 0 or row >= len(self.prompts):
            if not self.prompts:
                return
            row = len(self.prompts) - 1

        original_item = self.prompts[row]

        if count is None:
            dialog = DuplicateMultipleDialog(parent=self, prompt_item=original_item, default_count=2)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            count = dialog.get_count()

        if count <= 0:
            return

        insert_idx = row + 1
        new_items = [original_item.duplicate() for _ in range(count)]

        for i, new_item in enumerate(new_items):
            self.prompts.insert(insert_idx + i, new_item)

        self.refresh_table()
        self._apply_project_filter()
        self.table.selectRow(insert_idx)
        first_item = self.table.item(insert_idx, 0)
        if first_item:
            self.table.scrollToItem(first_item)
        self.save_queue()
        self._sync_project_filter_items()
        self.sig_queue_changed.emit()

    def _delete_selected_prompt(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.prompts):
            return
        del self.prompts[row]
        self.refresh_table()
        self.save_queue()
        self._sync_project_filter_items()
        self.sig_queue_changed.emit()

    def _move_selected(self, direction: int):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.prompts):
            return

        # Find next visible row in the direction to respect project/queue filtering
        target_row = -1
        curr = row + direction
        while 0 <= curr < len(self.prompts):
            if not self.table.isRowHidden(curr):
                target_row = curr
                break
            curr += direction

        if target_row != -1:
            self.prompts[row], self.prompts[target_row] = self.prompts[target_row], self.prompts[row]
            self.refresh_table()
            self.table.selectRow(target_row)
            self.save_queue()
            self.sig_queue_changed.emit()

    def _clear_queue(self):
        if not self.prompts:
            return
        reply = QMessageBox.question(
            self,
            "Clear Queue",
            "Are you sure you want to remove all prompts from the queue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.prompts.clear()
            self.refresh_table()
            self.save_queue()
            self.sig_queue_changed.emit()

    def _clear_completed_prompts(self, confirm: bool = True) -> int:
        """
        Removes only prompts that have status TaskStatus.COMPLETED from the queue.
        Respects active project and queue filters if applied (or clears all completed if 'All Projects').
        Returns the number of prompts cleared.
        """
        cur_filter = self.filter_project_combo.currentText().strip()
        cur_queue_filter = self.filter_queue_combo.currentText().strip() if hasattr(self, "filter_queue_combo") else "All Queues"

        def _norm_proj(p_val: str) -> str:
            raw = (p_val or "").strip()
            return raw if (raw and raw.lower() not in ("default", "none")) else "No Project"

        def _matches_filters(p) -> bool:
            if p.status != TaskStatus.COMPLETED:
                return False
            if cur_filter and cur_filter != "All Projects":
                if _norm_proj(p.project) != cur_filter and (p.project.strip() or "Default") != cur_filter:
                    return False
            if cur_queue_filter and cur_queue_filter != "All Queues":
                q_val = (getattr(p, "queue_name", "Default") or "Default").strip()
                if q_val != cur_queue_filter:
                    return False
            return True

        completed_to_remove = [p for p in self.prompts if _matches_filters(p)]
        if cur_filter and cur_filter != "All Projects":
            filter_scope_desc = f" in project '{cur_filter}'"
        else:
            filter_scope_desc = ""

        if not completed_to_remove:
            if confirm:
                QMessageBox.information(
                    self,
                    "Clear Completed",
                    f"No completed prompts found to clear{filter_scope_desc}."
                )
            return 0

        if confirm:
            count = len(completed_to_remove)
            reply = QMessageBox.question(
                self,
                "Clear Completed Prompts",
                f"Are you sure you want to remove {count} completed prompt(s){filter_scope_desc} from the queue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return 0

        remove_ids = {p.id for p in completed_to_remove}
        self.prompts[:] = [p for p in self.prompts if p.id not in remove_ids]
        self.refresh_table()
        self.save_queue()
        self._sync_project_filter_items()
        self.sig_queue_changed.emit()
        return len(remove_ids)

    def mark_running_as_interrupted(
        self,
        status: TaskStatus = TaskStatus.FAILED,
        reason: str = "Interrupted by application exit",
    ) -> int:
        """
        Marks any currently Running prompts as Failed/Interrupted and saves to queue.json.
        Called when the tool is closing so active dispatches are recorded with explicit failure.
        Returns the number of prompts updated.
        """
        updated_count = 0
        prompt_lock = getattr(self.prompts, "lock", None)
        if prompt_lock:
            with prompt_lock:
                for item in self.prompts:
                    if item.status == TaskStatus.RUNNING:
                        item.status = status
                        item.error_message = reason
                        updated_count += 1
        else:
            for item in self.prompts:
                if item.status == TaskStatus.RUNNING:
                    item.status = status
                    item.error_message = reason
                    updated_count += 1
        if updated_count > 0:
            self.refresh_table()
            self.save_queue()
            self._sync_project_filter_items()
            self.sig_queue_changed.emit()
        return updated_count

    def mark_running_as_completed(self) -> int:
        """
        Backward compatibility wrapper: delegates to mark_running_as_interrupted.
        """
        return self.mark_running_as_interrupted()

    def reconcile_all_prompts(self, notify: bool = True) -> int:
        """
        Inspects Antigravity's local SQLite database and transcript logs for all prompts
        in the queue that possess a conversation_id, reconciling their completion status.
        Returns the number of prompts updated.
        """
        from cdp.reconciliation import reconcile_prompt_status_from_conversation
        updated_count = 0
        prompt_lock = getattr(self.prompts, "lock", None)
        if prompt_lock:
            with prompt_lock:
                for item in self.prompts:
                    if item.conversation_id:
                        if reconcile_prompt_status_from_conversation(item):
                            updated_count += 1
        else:
            for item in self.prompts:
                if item.conversation_id:
                    if reconcile_prompt_status_from_conversation(item):
                        updated_count += 1

        if updated_count > 0:
            self.refresh_table()
            self.save_queue()
            self._sync_project_filter_items()
            self.sig_queue_changed.emit()

        if notify:
            if updated_count > 0:
                QMessageBox.information(
                    self,
                    "Reconciliation Complete",
                    f"Successfully reconciled {updated_count} prompt(s) from Antigravity background state.",
                )
            else:
                QMessageBox.information(
                    self,
                    "Reconciliation Complete",
                    "All prompts with conversation IDs are already up to date.",
                )
        return updated_count

    def reconcile_selected_prompt(self):
        """Reconciles status for the currently selected prompt row."""
        row = self.table.currentRow()
        if row < 0 or row >= len(self.prompts):
            return
        item = self.prompts[row]
        if not item.conversation_id:
            QMessageBox.information(
                self,
                "No Conversation ID",
                "This prompt does not have a recorded conversation ID to reconcile.",
            )
            return

        from cdp.reconciliation import reconcile_prompt_status_from_conversation
        mod = reconcile_prompt_status_from_conversation(item)
        if mod:
            self.refresh_row(row)
            self.save_queue()
            self._sync_project_filter_items()
            self.sig_queue_changed.emit()
            QMessageBox.information(
                self,
                "Prompt Reconciled",
                f"Prompt #{row + 1} status updated to '{item.status.value}'.",
            )
        else:
            QMessageBox.information(
                self,
                "Prompt Up to Date",
                f"Prompt #{row + 1} status is already up to date ({item.status.value}).",
            )

    def _reset_statuses(self):
        for item in self.prompts:
            item.status = TaskStatus.PENDING
            item.elapsed_time = 0.0
            item.error_message = ""
            item.conversation_id = ""
            item.is_resumed = False
            item.resume_count = 0
        self.refresh_table()
        self.save_queue()
        self.sig_queue_changed.emit()

    def _set_selected_mode(self, mode: str):
        row = self.table.currentRow()
        if 0 <= row < len(self.prompts):
            item = self.prompts[row]
            item.text = ensure_prompt_prefix(item.text, mode)
            self.refresh_table()
            self.table.selectRow(row)
            self.save_queue()
            self.sig_queue_changed.emit()

    def _import_queue(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Import Prompts Queue",
            "",
            "Supported Files (*.json *.txt *.md);;JSON Files (*.json);;Text Files (*.txt);;Markdown Files (*.md)",
        )
        if not filepath:
            return

        try:
            ext = os.path.splitext(filepath)[1].lower()
            if ext == ".json":
                preview_items = import_prompts_from_json(filepath, prefix_mode="keep")
            elif ext == ".md":
                preview_items = import_prompts_from_markdown(filepath, prefix_mode="keep")
            else:
                preview_items = import_prompts_from_txt(filepath, prefix_mode="keep")

            if not preview_items:
                QMessageBox.warning(self, "Import", "No valid prompts found in the selected file.")
                return

            dlg = ImportPromptsDialog(
                self,
                prompt_count=len(preview_items),
                has_existing_prompts=bool(self.prompts),
            )
            if not dlg.exec():
                return

            prefix_mode = dlg.get_prefix_mode()
            dest_mode = dlg.get_destination_mode()
            dest_project = dlg.get_project()

            if ext == ".json":
                new_items = import_prompts_from_json(filepath, prefix_mode=prefix_mode, project=dest_project)
            elif ext == ".md":
                new_items = import_prompts_from_markdown(filepath, prefix_mode=prefix_mode, project=dest_project)
            else:
                new_items = import_prompts_from_txt(filepath, prefix_mode=prefix_mode, project=dest_project)

            if dest_mode == "append" and self.prompts:
                self.prompts.extend(new_items)
            else:
                self.prompts[:] = new_items

            self.refresh_table()
            self.save_queue()
            self._sync_project_filter_items()
            self.sig_queue_changed.emit()

            mode_labels = {
                "/goal": "with '/goal ' prefix",
                "/plan": "with '/plan ' prefix",
                "keep": "preserving file commands",
                "none": "without slash prefixes",
            }
            mode_desc = mode_labels.get(prefix_mode, "")
            QMessageBox.information(
                self,
                "Import Successful",
                f"Successfully loaded {len(new_items)} prompt(s) {mode_desc} and saved to queue.json."
            )
        except Exception as e:
            QMessageBox.critical(self, "Import Failed", f"Could not import file: {e}")

    def _export_queue(self):
        if not self.prompts:
            QMessageBox.warning(self, "Export", "Queue is empty.")
            return

        filepath, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Prompts",
            "prompt_queue.json",
            "JSON Files (*.json);;Text Files (*.txt)",
        )
        if not filepath:
            return

        try:
            if filepath.lower().endswith(".txt"):
                export_prompts_to_txt(self.prompts, filepath)
            else:
                export_prompts_to_json(self.prompts, filepath)
            QMessageBox.information(self, "Export Successful", f"Saved {len(self.prompts)} prompts to {filepath}.")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Could not export file: {e}")

    def _show_context_menu(self, pos):
        # Auto-select row at right-click coordinates if valid
        row_at_pos = self.table.rowAt(pos.y())
        if row_at_pos >= 0:
            self.table.selectRow(row_at_pos)

        has_selection = 0 <= self.table.currentRow() < len(self.prompts)

        menu = QMenu(self)
        inspect_act = menu.addAction("🔍  Inspect Chat Details")
        reconcile_act = menu.addAction("⚡  Reconcile Status from Antigravity")
        menu.addSeparator()
        add_act = menu.addAction("Add Prompt...")
        edit_act = menu.addAction("Edit Prompt...")
        dup_act = menu.addAction("📋  Duplicate Prompt (Ctrl+D)")
        dup_multi_act = menu.addAction("📑  Duplicate Multiple Copies...")
        delete_act = menu.addAction("Delete Prompt")
        menu.addSeparator()
        set_goal_act = menu.addAction("🎯  Set to /goal (Goal Mode)")
        set_plan_act = menu.addAction("📋  Set to /plan (Plan Mode)")
        set_plain_act = menu.addAction("💬  Remove Prefix (Plain Mode)")
        menu.addSeparator()
        complete_act = menu.addAction("✔  Mark as Completed")
        reset_act = menu.addAction("🔄  Reset Status to Pending")
        clear_completed_act = menu.addAction("🗑  Clear Completed Prompts")

        if not has_selection:
            inspect_act.setEnabled(False)
            reconcile_act.setEnabled(False)
            edit_act.setEnabled(False)
            dup_act.setEnabled(False)
            dup_multi_act.setEnabled(False)
            delete_act.setEnabled(False)
            set_goal_act.setEnabled(False)
            set_plan_act.setEnabled(False)
            set_plain_act.setEnabled(False)
            complete_act.setEnabled(False)
            reset_act.setEnabled(False)

        action = menu.exec(self.table.mapToGlobal(pos))
        if action == inspect_act:
            row = self.table.currentRow()
            if 0 <= row < len(self.prompts):
                item = self.prompts[row]
                if item.conversation_id:
                    self.sig_inspect_conversation_requested.emit(item.conversation_id)
                else:
                    QMessageBox.information(
                        self,
                        "No Conversation ID Recorded",
                        "This prompt does not have an associated Conversation ID yet (it has not been executed or was reset to Pending).",
                    )
        elif action == reconcile_act:
            self.reconcile_selected_prompt()
        elif action == add_act:
            self._add_prompt()
        elif action == edit_act:
            self._edit_selected_prompt()
        elif action == dup_act:
            self._duplicate_selected_prompt()
        elif action == dup_multi_act:
            self._duplicate_multiple_selected_prompt()
        elif action == delete_act:
            self._delete_selected_prompt()
        elif action == set_goal_act:
            self._set_selected_mode("/goal")
        elif action == set_plan_act:
            self._set_selected_mode("/plan")
        elif action == set_plain_act:
            self._set_selected_mode("none")
        elif action == clear_completed_act:
            self._clear_completed_prompts()
        elif action == complete_act:
            row = self.table.currentRow()
            if 0 <= row < len(self.prompts):
                item = self.prompts[row]
                item.status = TaskStatus.COMPLETED
                item.error_message = ""
                self.refresh_row(row)
                self._apply_project_filter()
                self.sig_queue_changed.emit()
                self.save_queue()
        elif action == reset_act:
            row = self.table.currentRow()
            if 0 <= row < len(self.prompts):
                item = self.prompts[row]
                item.status = TaskStatus.PENDING
                item.elapsed_time = 0.0
                item.error_message = ""
                item.conversation_id = ""
                item.is_resumed = False
                item.resume_count = 0
                self.refresh_row(row)
                self._apply_project_filter()
                self.sig_queue_changed.emit()
                self.save_queue()

    def update_active_projects_hud(self, active_projects_dict: Dict[str, Any]):
        """
        Refreshes the live HUD chips showing status and independent pause/resume buttons
        for each parallel active project.
        """
        self.hud_frame.setVisible(bool(active_projects_dict))

        # Clear existing items in chips layout
        while self.hud_chips_layout.count() > 0:
            item = self.hud_chips_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if not active_projects_dict:
            placeholder = QLabel("No active parallel projects currently generating.")
            placeholder.setStyleSheet(f"color: {COLOR_TEXT_FAINT}; font-size: 11px; font-style: italic;")
            self.hud_chips_layout.addWidget(placeholder)
            return

        for proj_name, data in active_projects_dict.items():
            status = data.get("status", "IDLE")
            is_paused = data.get("is_paused", False)
            elapsed = data.get("elapsed_time", 0.0)
            pending_count = data.get("pending_count", 0)

            chip = QFrame()
            chip_layout = QHBoxLayout(chip)
            chip_layout.setContentsMargins(8, 3, 8, 3)
            chip_layout.setSpacing(6)

            # Style chip based on status
            if is_paused or status == "PAUSED":
                chip.setStyleSheet(
                    "QFrame { background-color: #292524; border: 1px solid #78716c; border-radius: 6px; }"
                )
                status_icon = "⏸"
                status_color = "#e7e5e4"
                status_text = "Paused"
            elif status == "GENERATING":
                chip.setStyleSheet(
                    "QFrame { background-color: #064e3b; border: 1px solid #059669; border-radius: 6px; }"
                )
                status_icon = "🟢"
                status_color = "#34d399"
                status_text = f"Generating ({int(elapsed)}s)" if elapsed > 0 else "Generating"
            elif status == "HALTED_QUOTA":
                chip.setStyleSheet(
                    "QFrame { background-color: #451a03; border: 1px solid #d97706; border-radius: 6px; }"
                )
                status_icon = "🔒"
                status_color = "#fbbf24"
                status_text = "Quota Halted"
            elif status == "DISPATCHING":
                chip.setStyleSheet(
                    "QFrame { background-color: #0c4a6e; border: 1px solid #0284c7; border-radius: 6px; }"
                )
                status_icon = "⏳"
                status_color = "#38bdf8"
                status_text = "Dispatching"
            elif status == "STABILIZING":
                chip.setStyleSheet(
                    "QFrame { background-color: #0c4a6e; border: 1px solid #0284c7; border-radius: 6px; }"
                )
                status_icon = "⏳"
                status_color = "#38bdf8"
                status_text = "Stabilizing"
            elif status == "COMPLETED":
                chip.setStyleSheet(
                    "QFrame { background-color: #14532d; border: 1px solid #16a34a; border-radius: 6px; }"
                )
                status_icon = "✔"
                status_color = "#4ade80"
                status_text = "Completed"
            elif status == "QUEUED" or (status == "IDLE" and pending_count > 0):
                chip.setStyleSheet(
                    "QFrame { background-color: #2e1065; border: 1px solid #7c3aed; border-radius: 6px; }"
                )
                status_icon = "🟡"
                status_color = "#c084fc"
                status_text = f"Queued ({pending_count} pending)" if pending_count > 0 else "Queued"
            else:
                chip.setStyleSheet(
                    f"QFrame {{ background-color: {COLOR_SURFACE_2}; border: 1px solid {COLOR_BORDER_DEFAULT}; border-radius: 6px; }}"
                )
                status_icon = "⚪"
                status_color = COLOR_TEXT_MUTED
                status_text = "Idle"

            lbl_name = QLabel(f"<b>{proj_name}</b>:")
            lbl_name.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 11px;")
            chip_layout.addWidget(lbl_name)

            lbl_st = QLabel(f"{status_icon} {status_text}")
            lbl_st.setStyleSheet(f"color: {status_color}; font-size: 11px; font-weight: 600;")
            chip_layout.addWidget(lbl_st)

            # Dedicated independent Pause / Resume buttons per project
            if status in ("GENERATING", "DISPATCHING") and not is_paused:
                btn_pause = QPushButton("⏸ Pause")
                btn_pause.setFixedHeight(22)
                btn_pause.setStyleSheet(
                    "QPushButton { background: #44403c; color: #f5f5f4; border: 1px solid #78716c; border-radius: 4px; font-size: 10px; font-weight: 700; padding: 1px 6px; }"
                    "QPushButton:hover { background: #57534e; border-color: #a8a29e; }"
                )
                btn_pause.clicked.connect(lambda _, p=proj_name: self.sig_pause_project_requested.emit(p))
                chip_layout.addWidget(btn_pause)
            elif is_paused or status in ("PAUSED", "HALTED_QUOTA"):
                btn_resume = QPushButton("▶ Resume")
                btn_resume.setFixedHeight(22)
                btn_resume.setStyleSheet(
                    "QPushButton { background: #065f46; color: #ecfdf5; border: 1px solid #10b981; border-radius: 4px; font-size: 10px; font-weight: 700; padding: 1px 6px; }"
                    "QPushButton:hover { background: #047857; border-color: #34d399; }"
                )
                btn_resume.clicked.connect(lambda _, p=proj_name: self.sig_resume_project_requested.emit(p))
                chip_layout.addWidget(btn_resume)

            self.hud_chips_layout.addWidget(chip)

    def on_project_state_changed(self, project_name: str, status: str, prompt_id: str):
        """Called when a single project's state changes."""
        if prompt_id:
            for row, p in enumerate(self.prompts):
                if p.id == prompt_id:
                    self.refresh_row(row)
                    break

