"""
Accounts Tab: Google accounts circular queue management and automated account switching.
Modernized to Linear / Fluent 2 design standards with zero functional regressions.
"""

import os
from typing import List, Optional, Dict, Any
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QLabel,
    QLineEdit,
    QCheckBox,
    QProgressBar,
    QPlainTextEdit,
    QMessageBox,
    QFrame,
    QScrollArea,
    QToolButton,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QTimer
from PyQt6.QtGui import QFont, QColor

from app.ui.widgets import ToggleSwitch, ResponsiveScrollContainer, ScrollableTableWidget
from app.account_manager import account_manager, GoogleAccount
from app.quota_checker import (
    format_remaining_duration,
    format_verbose_remaining_duration,
    build_quota_tooltip,
    build_percentage_tooltip,
    format_time_ago,
)
from app.ui.styles import (
    CARD_STYLE,
    get_account_badge_html,
    get_quota_pill_html,
    COLOR_CANVAS,
    COLOR_SURFACE_0,
    COLOR_SURFACE_1,
    COLOR_SURFACE_2,
    COLOR_SURFACE_HOVER,
    COLOR_BORDER_DEFAULT,
    COLOR_BORDER_SUBTLE,
    COLOR_BORDER_STRONG,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_DANGER,
    COLOR_INFO,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_FAINT,
)


class AccountsTab(QWidget):
    """
    Dedicated Google Accounts Management & Rotation Tab.
    Provides complete circular queue management and automated account switching.
    """

    sig_run_switch = pyqtSignal(str)           # target_account_email (or "" for next in queue)
    sig_cancel_switch = pyqtSignal()
    sig_check_quota = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_running_switch = False
        self._setup_ui()
        self.reload_accounts()
        self._countdown_timer = QTimer(self)
        self._countdown_timer.timeout.connect(self._refresh_countdown_display)
        self._countdown_timer.start(30000)  # Refresh countdowns every 30s

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.scroll_area = ResponsiveScrollContainer(self, margin_h=12, margin_v=10, spacing=8)
        root_layout.addWidget(self.scroll_area)

        # ─────────────────────────────────────────────────────────────
        # 1. TOP CARDS: BALANCED CIRCULAR QUEUE OVERVIEW (1:1:1)
        # ─────────────────────────────────────────────────────────────
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(8)

        card_base_style = CARD_STYLE

        # Card A: Current Active Account
        self.card_active = QFrame()
        self.card_active.setStyleSheet(card_base_style)
        card_active_layout = QVBoxLayout(self.card_active)
        card_active_layout.setContentsMargins(12, 10, 12, 10)
        card_active_layout.setSpacing(4)

        lbl_active_title = QLabel("ACTIVE ACCOUNT")
        lbl_active_title.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.5px;")
        self.lbl_active_email = QLabel("None")
        self.lbl_active_email.setStyleSheet("color: #38bdf8; font-size: 13px; font-weight: 700;")
        self.lbl_active_quota = QLabel("5-Hour: —  |  Weekly: —")
        self.lbl_active_quota.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px; font-weight: 500;")
        card_active_layout.addWidget(lbl_active_title)
        card_active_layout.addWidget(self.lbl_active_email)
        card_active_layout.addWidget(self.lbl_active_quota)
        cards_layout.addWidget(self.card_active, 1)

        # Card B: Next in Circular Queue
        self.card_next = QFrame()
        self.card_next.setStyleSheet(card_base_style)
        card_next_layout = QVBoxLayout(self.card_next)
        card_next_layout.setContentsMargins(12, 10, 12, 10)
        card_next_layout.setSpacing(4)

        next_top_row = QHBoxLayout()
        self.lbl_next_title = QLabel("NEXT IN QUEUE")
        self.lbl_next_title.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.5px;")
        next_top_row.addWidget(self.lbl_next_title)
        next_top_row.addStretch()

        self.btn_reset_jump = QPushButton("✕ Clear Jump")
        self.btn_reset_jump.setStyleSheet(
            "QPushButton { background: transparent; color: #f87171; border: none; font-size: 10px; font-weight: 700; padding: 0 4px; }"
            "QPushButton:hover { color: #ffffff; text-decoration: underline; }"
        )
        self.btn_reset_jump.setToolTip("Cancel manual jump override and return to normal circular rotation")
        self.btn_reset_jump.setVisible(False)
        self.btn_reset_jump.clicked.connect(self._clear_jump_clicked)
        next_top_row.addWidget(self.btn_reset_jump)
        card_next_layout.addLayout(next_top_row)

        self.lbl_next_email = QLabel("None")
        self.lbl_next_email.setStyleSheet("color: #a78bfa; font-size: 13px; font-weight: 700;")
        card_next_layout.addWidget(self.lbl_next_email)
        cards_layout.addWidget(self.card_next, 1)

        # Card C: Rotation Pool Count
        self.card_count = QFrame()
        self.card_count.setStyleSheet(card_base_style)
        card_count_layout = QVBoxLayout(self.card_count)
        card_count_layout.setContentsMargins(12, 10, 12, 10)
        card_count_layout.setSpacing(4)

        lbl_count_title = QLabel("POOL HEALTH")
        lbl_count_title.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.5px;")
        self.lbl_count_val = QLabel("1 / 1 enabled")
        self.lbl_count_val.setStyleSheet("color: #34d399; font-size: 13px; font-weight: 700;")
        card_count_layout.addWidget(lbl_count_title)
        card_count_layout.addWidget(self.lbl_count_val)
        cards_layout.addWidget(self.card_count, 1)

        self.scroll_area.add_layout(cards_layout)

        # ─────────────────────────────────────────────────────────────
        # 2. AUTOMATED ACCOUNT SWITCH CONTROLS CARD
        # ─────────────────────────────────────────────────────────────
        switch_card = QFrame()
        switch_card.setStyleSheet(CARD_STYLE)
        switch_layout = QVBoxLayout(switch_card)
        switch_layout.setSpacing(10)
        switch_layout.setContentsMargins(14, 12, 14, 12)

        # Card Header
        sw_header = QHBoxLayout()
        sw_title = QLabel("ACCOUNT SWITCHER")
        sw_title.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.6px;")
        sw_header.addWidget(sw_title)
        sw_header.addStretch()
        switch_layout.addLayout(sw_header)

        # Compact Control Bar (Status & Progress on left, Fixed Action Buttons on right)
        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)

        self.lbl_step_status = QLabel("Ready")
        self.lbl_step_status.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-weight: 600; font-size: 12px;")
        controls_row.addWidget(self.lbl_step_status, 1)

        self.switch_prog_bar = QProgressBar()
        self.switch_prog_bar.setRange(0, 10)
        self.switch_prog_bar.setValue(0)
        self.switch_prog_bar.setTextVisible(True)
        self.switch_prog_bar.setFormat("%v / %m Steps")
        self.switch_prog_bar.setStyleSheet("QProgressBar { min-width: 150px; max-width: 200px; height: 16px; }")
        controls_row.addWidget(self.switch_prog_bar)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setStyleSheet(f"background-color: {COLOR_BORDER_DEFAULT}; width: 1px; margin: 4px 4px;")
        controls_row.addWidget(div)

        self.btn_change_account = QPushButton("🔄 Switch Account")
        self.btn_run_switch = self.btn_change_account  # Alias for backward compatibility
        self.btn_change_account.setObjectName("btn-success")
        self.btn_change_account.setProperty("variant", "success")
        self.btn_change_account.setMinimumHeight(32)
        self.btn_change_account.setMaximumWidth(155)
        self.btn_change_account.setToolTip("Switch to next enabled Google account in circular queue.")
        self.btn_change_account.clicked.connect(self._on_change_account_clicked)
        controls_row.addWidget(self.btn_change_account)

        self.btn_check_quota = QPushButton("⚡ Check Quota")
        self.btn_check_quota.setObjectName("btn-primary")
        self.btn_check_quota.setProperty("variant", "primary")
        self.btn_check_quota.setMinimumHeight(32)
        self.btn_check_quota.setMaximumWidth(130)
        self.btn_check_quota.setToolTip("Inspect current Gemini quota limits.")
        self.btn_check_quota.clicked.connect(lambda: self.sig_check_quota.emit())
        controls_row.addWidget(self.btn_check_quota)

        self.btn_cancel_switch = QPushButton("⏹ Cancel")
        self.btn_cancel_switch.setObjectName("btn-danger")
        self.btn_cancel_switch.setProperty("variant", "danger")
        self.btn_cancel_switch.setMinimumHeight(32)
        self.btn_cancel_switch.setMaximumWidth(90)
        self.btn_cancel_switch.setEnabled(False)
        self.btn_cancel_switch.clicked.connect(self._on_cancel_clicked)
        controls_row.addWidget(self.btn_cancel_switch)

        switch_layout.addLayout(controls_row)
        self.scroll_area.add_card(switch_card)

        # ─────────────────────────────────────────────────────────────
        # 3. GOOGLE ACCOUNTS CIRCULAR QUEUE & TABLE CARD
        # ─────────────────────────────────────────────────────────────
        acc_card = QFrame()
        acc_card.setStyleSheet(CARD_STYLE)
        acc_layout = QVBoxLayout(acc_card)
        acc_layout.setSpacing(10)
        acc_layout.setContentsMargins(14, 12, 14, 12)

        # Header & Add account bar
        acc_header = QHBoxLayout()
        acc_title = QLabel("ACCOUNTS")
        acc_title.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.6px;")
        acc_header.addWidget(acc_title)
        acc_header.addStretch()
        acc_layout.addLayout(acc_header)

        add_bar = QHBoxLayout()
        add_bar.setSpacing(8)

        lbl_add = QLabel("Add:")
        lbl_add.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-weight: 600; font-size: 12px;")
        add_bar.addWidget(lbl_add)

        self.txt_new_email = QLineEdit()
        self.txt_new_email.setPlaceholderText("Email address")
        self.txt_new_email.returnPressed.connect(self._add_account_clicked)
        add_bar.addWidget(self.txt_new_email, 2)

        self.txt_new_name = QLineEdit()
        self.txt_new_name.setPlaceholderText("Display Name")
        self.txt_new_name.returnPressed.connect(self._add_account_clicked)
        add_bar.addWidget(self.txt_new_name, 1)

        self.btn_add_acc = QPushButton("+ Add")
        self.btn_add_acc.setObjectName("btn-primary")
        self.btn_add_acc.clicked.connect(self._add_account_clicked)
        add_bar.addWidget(self.btn_add_acc)

        acc_layout.addLayout(add_bar)

        # Accounts Table (11 Columns: Dual Quota Tracking & Buffered Resets)
        self.table = ScrollableTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels([
            "#",
            "Enabled",
            "Email",
            "Name",
            "5-Hour Quota",
            "5-Hour Reset",
            "Weekly Quota",
            "Weekly Reset",
            "Status",
            "Last Used",
            "Actions",
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)

        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(9, QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(10, QHeaderView.ResizeMode.Fixed)

        self.table.setColumnWidth(0, 32)
        self.table.setColumnWidth(1, 56)
        self.table.setColumnWidth(3, 80)
        self.table.setColumnWidth(4, 90)
        self.table.setColumnWidth(5, 100)
        self.table.setColumnWidth(6, 90)
        self.table.setColumnWidth(7, 100)
        self.table.setColumnWidth(8, 75)
        self.table.setColumnWidth(9, 95)
        self.table.setColumnWidth(10, 130)

        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(280)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        acc_layout.addWidget(self.table)

        self.scroll_area.add_card(acc_card)

        # ─────────────────────────────────────────────────────────────
        # 4. LIVE LOG CONSOLE (DRAWER TERMINAL)
        # ─────────────────────────────────────────────────────────────
        log_card = QFrame()
        log_card.setStyleSheet(CARD_STYLE)
        log_layout = QVBoxLayout(log_card)
        log_layout.setSpacing(6)
        log_layout.setContentsMargins(14, 12, 14, 10)

        log_top_bar = QHBoxLayout()
        lbl_log_title = QLabel("TELEMETRY LOG")
        lbl_log_title.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.6px;")
        log_top_bar.addWidget(lbl_log_title)
        log_top_bar.addStretch()

        self.btn_toggle_log = QPushButton("Hide Log")
        self.btn_toggle_log.setObjectName("btn-secondary")
        self.btn_toggle_log.setMinimumHeight(24)
        self.btn_toggle_log.clicked.connect(self._toggle_log_visibility)
        log_top_bar.addWidget(self.btn_toggle_log)

        btn_clear_log = QPushButton("Clear")
        btn_clear_log.setObjectName("btn-secondary")
        btn_clear_log.setMinimumHeight(24)
        btn_clear_log.clicked.connect(self._clear_log)
        log_top_bar.addWidget(btn_clear_log)
        log_layout.addLayout(log_top_bar)

        self.log_console = QPlainTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setMaximumBlockCount(2000)
        self.log_console.setMinimumHeight(120)
        self.log_console.setMaximumHeight(260)
        self.log_console.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.log_console.setStyleSheet(
            f"QPlainTextEdit {{ background-color: #070a12; font-family: 'Cascadia Code', 'Consolas', monospace; "
            f"font-size: 12px; color: #cbd5e1; border: 1px solid {COLOR_BORDER_SUBTLE}; border-radius: 6px; padding: 6px; }}"
        )
        log_layout.addWidget(self.log_console)

        self.scroll_area.add_card(log_card)

    # ─────────────────────────────────────────────────────────────────────────
    # TABLE & DATA POPULATION
    # ─────────────────────────────────────────────────────────────────────────

    def reload_accounts(self):
        """Reloads accounts from account_manager and renders table rows."""
        self.table.setRowCount(0)
        account_manager.load_accounts()
        accounts = account_manager.accounts

        next_acc = account_manager.get_next_account()
        next_email = next_acc.email if next_acc else "None"
        is_jump = bool(account_manager.jump_next_email and next_acc and account_manager.jump_next_email.lower() == next_acc.email.lower())
        if is_jump:
            self.lbl_next_title.setText("NEXT (JUMP OVERRIDE)")
            self.lbl_next_email.setText(f"{next_email} 🎯")
            self.btn_reset_jump.setVisible(True)
        else:
            self.lbl_next_title.setText("NEXT IN QUEUE")
            self.lbl_next_email.setText(next_email)
            self.btn_reset_jump.setVisible(False)

        curr_email = account_manager.current_email
        if not curr_email and accounts:
            used_candidates = [a for a in accounts if a.last_used]
            if used_candidates:
                used_candidates.sort(key=lambda a: a.last_used, reverse=True)
                curr_email = used_candidates[0].email
            else:
                active_acc = next((a for a in accounts if a.quota_status == "Active"), None)
                curr_email = active_acc.email if active_acc else accounts[0].email
            account_manager.current_email = curr_email

        active_account = next((a for a in accounts if a.email.lower() == (curr_email or "").lower()), None)
        if active_account:
            self.lbl_active_email.setText(curr_email or "None")
            self.lbl_active_email.setStyleSheet("color: #38bdf8; font-size: 13px; font-weight: 700;")
            five_rem = format_remaining_duration(active_account.five_hour_reset_at) if active_account.five_hour_reset_at else ""
            week_rem = format_remaining_duration(active_account.weekly_reset_at) if active_account.weekly_reset_at else ""

            five_str = f"{active_account.five_hour_percent}%" if active_account.five_hour_percent is not None else "—"
            if five_rem and five_rem not in ("—", "Reset now"):
                five_str += f" ({five_rem})"
            elif five_rem == "Reset now":
                five_str += " (Reset now)"

            week_str = f"{active_account.weekly_percent}%" if active_account.weekly_percent is not None else "—"
            if week_rem and week_rem not in ("—", "Reset now"):
                week_str += f" ({week_rem})"
            elif week_rem == "Reset now":
                week_str += " (Reset now)"

            self.lbl_active_quota.setText(f"5-Hour: {five_str}  |  Weekly: {week_str}")
            card_5h_tip = build_quota_tooltip(
                "5-Hour",
                active_account.five_hour_reset_at,
                active_account.five_hour_refresh_raw,
                last_checked_str=active_account.last_quota_checked,
                check_reason=active_account.last_quota_reason,
            )
            card_wk_tip = build_quota_tooltip(
                "Weekly",
                active_account.weekly_reset_at,
                active_account.weekly_refresh_raw,
                last_checked_str=active_account.last_quota_checked,
                check_reason=active_account.last_quota_reason,
            )
            self.lbl_active_quota.setToolTip(f"{card_5h_tip}\n\n{card_wk_tip}")
        elif curr_email:
            self.lbl_active_email.setText(f"{curr_email} (Outside Account)")
            self.lbl_active_email.setStyleSheet("color: #fb923c; font-size: 13px; font-weight: 700;")
            unreg_q = account_manager.get_unregistered_quota(curr_email)
            if unreg_q:
                five_pct = unreg_q.get("five_hour_percent")
                five_res = unreg_q.get("five_hour_reset_at")
                week_pct = unreg_q.get("weekly_percent")
                week_res = unreg_q.get("weekly_reset_at")

                five_rem = format_remaining_duration(five_res) if five_res else (unreg_q.get("five_hour_refresh_raw") or "")
                week_rem = format_remaining_duration(week_res) if week_res else (unreg_q.get("weekly_refresh_raw") or "")

                five_str = f"{five_pct}%" if five_pct is not None else "—"
                if five_rem and five_rem not in ("—", "Reset now", "Unknown"):
                    five_str += f" ({five_rem if five_rem.startswith('in ') else 'in ' + five_rem})"
                elif five_rem == "Reset now":
                    five_str += " (Reset now)"

                week_str = f"{week_pct}%" if week_pct is not None else "—"
                if week_rem and week_rem not in ("—", "Reset now", "Unknown"):
                    week_str += f" ({week_rem if week_rem.startswith('in ') else 'in ' + week_rem})"
                elif week_rem == "Reset now":
                    week_str += " (Reset now)"

                self.lbl_active_quota.setText(f"5-Hour: {five_str}  |  Weekly: {week_str}")
                card_5h_tip = build_quota_tooltip(
                    "5-Hour",
                    five_res,
                    unreg_q.get("five_hour_refresh_raw"),
                    last_checked_str=unreg_q.get("last_quota_checked", ""),
                    check_reason=unreg_q.get("last_quota_reason", ""),
                )
                card_wk_tip = build_quota_tooltip(
                    "Weekly",
                    week_res,
                    unreg_q.get("weekly_refresh_raw"),
                    last_checked_str=unreg_q.get("last_quota_checked", ""),
                    check_reason=unreg_q.get("last_quota_reason", ""),
                )
                self.lbl_active_quota.setToolTip(
                    f"{card_5h_tip}\n\n{card_wk_tip}\n\n"
                    f"Outside account active: prompts will use this account while quota is above threshold, "
                    f"then auto-switch to queue target."
                )
            else:
                self.lbl_active_quota.setText("5-Hour: —  |  Weekly: — (Outside Account - Not in Queue)")
                self.lbl_active_quota.setToolTip(
                    "Outside account detected. Will be used while quota is above threshold, "
                    "then auto-switch to queue target."
                )
        else:
            self.lbl_active_email.setText("None")
            self.lbl_active_email.setStyleSheet("color: #38bdf8; font-size: 13px; font-weight: 700;")
            self.lbl_active_quota.setText("5-Hour: —  |  Weekly: —")
            self.lbl_active_quota.setToolTip("No active account selected.")

        enabled_count = len(account_manager.get_enabled_accounts())
        self.lbl_count_val.setText(f"{enabled_count} / {len(accounts)} enabled")

        self.table.setRowCount(len(accounts))
        needed_height = 42 + (len(accounts) * 36) + 10
        self.table.setMinimumHeight(max(240, needed_height))

        for row, acc in enumerate(accounts):
            # Col 0: Index
            idx_item = QTableWidgetItem(f"{row + 1}")
            idx_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            idx_item.setFlags(idx_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, idx_item)

            # Col 1: Enabled
            chk_widget = QWidget()
            chk_layout = QHBoxLayout(chk_widget)
            chk_layout.setContentsMargins(0, 0, 0, 0)
            chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            toggle = ToggleSwitch(chk_widget, checked=acc.enabled)
            toggle.setToolTip("Enable or disable this account in the circular switching queue.")
            toggle.toggled.connect(lambda checked, a_id=acc.id: self._on_account_toggled(a_id, checked))
            chk_layout.addWidget(toggle)
            self.table.setCellWidget(row, 1, chk_widget)

            # Col 2: Email
            email_item = QTableWidgetItem(acc.email)
            email_item.setFlags(email_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if acc.email.lower() == (curr_email or "").lower():
                email_item.setForeground(QColor("#38bdf8"))
                font = email_item.font()
                font.setBold(True)
                email_item.setFont(font)
            self.table.setItem(row, 2, email_item)

            # Col 3: Display Name
            name_item = QTableWidgetItem(acc.display_name or "—")
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 3, name_item)

            # Tooltips for 5-Hour and Weekly limits
            tip_5h_pct = build_percentage_tooltip(
                "5-Hour",
                percent=acc.five_hour_percent,
                last_checked_str=acc.last_quota_checked,
                check_reason=acc.last_quota_reason,
                reset_at_str=acc.five_hour_reset_at,
                raw_refresh=acc.five_hour_refresh_raw,
            )
            tip_5h_reset = build_quota_tooltip(
                "5-Hour",
                acc.five_hour_reset_at,
                acc.five_hour_refresh_raw,
                last_checked_str=acc.last_quota_checked,
                check_reason=acc.last_quota_reason,
            )

            tip_wk_pct = build_percentage_tooltip(
                "Weekly",
                percent=acc.weekly_percent,
                last_checked_str=acc.last_quota_checked,
                check_reason=acc.last_quota_reason,
                reset_at_str=acc.weekly_reset_at,
                raw_refresh=acc.weekly_refresh_raw,
            )
            tip_wk_reset = build_quota_tooltip(
                "Weekly",
                acc.weekly_reset_at,
                acc.weekly_refresh_raw,
                last_checked_str=acc.last_quota_checked,
                check_reason=acc.last_quota_reason,
            )

            # Col 4: 5-Hour Quota Pill
            quota_5h_lbl = QLabel()
            quota_5h_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            quota_5h_lbl.setText(get_quota_pill_html(acc.five_hour_percent))
            quota_5h_lbl.setToolTip(tip_5h_pct)
            self.table.setCellWidget(row, 4, quota_5h_lbl)

            # Col 5: 5-Hour Reset (Actual minutes + 1)
            ref_5h_text = format_remaining_duration(acc.five_hour_reset_at) if acc.five_hour_reset_at else (acc.five_hour_refresh_raw or "—")
            ref_5h_item = QTableWidgetItem(ref_5h_text)
            ref_5h_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            ref_5h_item.setFlags(ref_5h_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            ref_5h_item.setToolTip(tip_5h_reset)
            self.table.setItem(row, 5, ref_5h_item)

            # Col 6: Weekly Quota Pill
            quota_wk_lbl = QLabel()
            quota_wk_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            quota_wk_lbl.setText(get_quota_pill_html(acc.weekly_percent))
            quota_wk_lbl.setToolTip(tip_wk_pct)
            self.table.setCellWidget(row, 6, quota_wk_lbl)

            # Col 7: Weekly Reset (Actual hours + 1)
            ref_wk_text = format_remaining_duration(acc.weekly_reset_at) if acc.weekly_reset_at else (acc.weekly_refresh_raw or "—")
            ref_wk_item = QTableWidgetItem(ref_wk_text)
            ref_wk_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            ref_wk_item.setFlags(ref_wk_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            ref_wk_item.setToolTip(tip_wk_reset)
            self.table.setItem(row, 7, ref_wk_item)

            # Col 8: Status
            status_lbl = QLabel()
            status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            is_this_jump = bool(account_manager.jump_next_email and account_manager.jump_next_email.lower() == acc.email.lower())
            if not acc.enabled:
                status_text = "Disabled"
            elif account_manager.is_account_frozen(acc):
                status_text = "Frozen"
            elif is_this_jump:
                status_text = "Jump"
            elif acc.email.lower() == (curr_email or "").lower():
                status_text = "Active"
            else:
                status_text = "Ready" if acc.quota_status in ("Active", "Low Quota") else acc.quota_status
            status_lbl.setText(get_account_badge_html(status_text, acc.enabled))
            if status_text == "Frozen":
                freeze_reasons = []
                if acc.five_hour_reset_at:
                    freeze_reasons.append(f"5-Hour resets at {acc.five_hour_reset_at}")
                if acc.weekly_reset_at:
                    freeze_reasons.append(f"Weekly resets at {acc.weekly_reset_at}")
                status_lbl.setToolTip("Frozen: " + " | ".join(freeze_reasons) if freeze_reasons else "Account frozen due to low quota")
            elif is_this_jump:
                status_lbl.setToolTip("Manual Jump Target: Scheduled to run next!")
            self.table.setCellWidget(row, 8, status_lbl)

            # Col 9: Last Used
            last_used_str = acc.last_used or "Never"
            used_item = QTableWidgetItem(last_used_str)
            used_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            used_item.setFlags(used_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 9, used_item)

            # Col 10: Actions
            act_widget = QWidget()
            act_layout = QHBoxLayout(act_widget)
            act_layout.setContentsMargins(4, 2, 4, 2)
            act_layout.setSpacing(4)
            act_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            btn_jump = QToolButton()
            btn_jump.setText("🎯" if is_this_jump else "⏭")
            btn_jump.setFixedSize(26, 26)
            btn_jump.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_jump.setToolTip("Active Jump Target (Click to cancel)" if is_this_jump else "Set as immediate next account to switch to (Jump)")
            if is_this_jump:
                btn_jump.setStyleSheet(
                    "QToolButton { background-color: #581c87; color: #f3e8ff; border: 1px solid #c084fc; "
                    "border-radius: 4px; font-size: 11px; font-weight: 700; }"
                    "QToolButton:hover { background-color: #6b21a8; color: #ffffff; }"
                )
            else:
                btn_jump.setStyleSheet(
                    f"QToolButton {{ background-color: {COLOR_SURFACE_2}; color: {COLOR_TEXT_SECONDARY}; "
                    f"border: 1px solid {COLOR_BORDER_DEFAULT}; border-radius: 4px; font-size: 11px; }}"
                    f"QToolButton:hover {{ background-color: #263350; border-color: #c084fc; color: #c084fc; }}"
                )
            btn_jump.clicked.connect(lambda _, a_email=acc.email: self._toggle_jump_account(a_email))
            act_layout.addWidget(btn_jump)

            btn_up = QToolButton()
            btn_up.setText("▲")
            btn_up.setFixedSize(26, 26)
            btn_up.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_up.setToolTip("Move up in rotation order")
            btn_up.setStyleSheet(
                f"QToolButton {{ background-color: {COLOR_SURFACE_2}; color: {COLOR_TEXT_SECONDARY}; "
                f"border: 1px solid {COLOR_BORDER_DEFAULT}; border-radius: 4px; font-size: 10px; font-weight: 700; }}"
                f"QToolButton:hover {{ background-color: #1f2a45; border-color: #38bdf8; color: #38bdf8; }}"
                f"QToolButton:disabled {{ background-color: transparent; border-color: transparent; color: {COLOR_TEXT_FAINT}; }}"
            )
            btn_up.setEnabled(row > 0)
            btn_up.clicked.connect(lambda _, a_id=acc.id: self._move_account(a_id, "up"))
            act_layout.addWidget(btn_up)

            btn_down = QToolButton()
            btn_down.setText("▼")
            btn_down.setFixedSize(26, 26)
            btn_down.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_down.setToolTip("Move down in rotation order")
            btn_down.setStyleSheet(
                f"QToolButton {{ background-color: {COLOR_SURFACE_2}; color: {COLOR_TEXT_SECONDARY}; "
                f"border: 1px solid {COLOR_BORDER_DEFAULT}; border-radius: 4px; font-size: 10px; font-weight: 700; }}"
                f"QToolButton:hover {{ background-color: #1f2a45; border-color: #38bdf8; color: #38bdf8; }}"
                f"QToolButton:disabled {{ background-color: transparent; border-color: transparent; color: {COLOR_TEXT_FAINT}; }}"
            )
            btn_down.setEnabled(row < len(accounts) - 1)
            btn_down.clicked.connect(lambda _, a_id=acc.id: self._move_account(a_id, "down"))
            act_layout.addWidget(btn_down)

            btn_del = QToolButton()
            btn_del.setText("🗑")
            btn_del.setFixedSize(26, 26)
            btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_del.setToolTip("Remove account from list")
            btn_del.setStyleSheet(
                f"QToolButton {{ background-color: {COLOR_SURFACE_2}; color: #f87171; "
                f"border: 1px solid {COLOR_BORDER_DEFAULT}; border-radius: 4px; font-size: 11px; }}"
                f"QToolButton:hover {{ background-color: #450a0a; border-color: #ef4444; color: #ffffff; }}"
            )
            btn_del.clicked.connect(lambda _, a_id=acc.id, a_email=acc.email: self._remove_account(a_id, a_email))
            act_layout.addWidget(btn_del)

            self.table.setCellWidget(row, 10, act_widget)

    # ─────────────────────────────────────────────────────────────────────────
    # ACCOUNT MANAGEMENT ACTIONS
    # ─────────────────────────────────────────────────────────────────────────

    def _add_account_clicked(self):
        email = self.txt_new_email.text().strip()
        name = self.txt_new_name.text().strip()

        if not email or "@" not in email:
            QMessageBox.warning(self, "Invalid Email", "Please enter a valid Google email address.")
            return

        acc = account_manager.add_account(email, name)
        if acc:
            self.txt_new_email.clear()
            self.txt_new_name.clear()
            self.append_log("INFO", f"Added Google account '{email}' to circular queue.")
            self.reload_accounts()
        else:
            QMessageBox.warning(self, "Error", "Could not add account.")

    def _on_account_toggled(self, acc_id: str, enabled: bool):
        account_manager.toggle_account(acc_id, enabled)
        self.reload_accounts()

    def _move_account(self, target: Any, direction: Any):
        account_manager.move_account(target, direction)
        self.reload_accounts()

    def _remove_account(self, acc_id: str, email: str):
        reply = QMessageBox.question(
            self,
            "Remove Account",
            f"Are you sure you want to remove '{email}' from the circular switching queue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            account_manager.remove_account(acc_id)
            self.append_log("INFO", f"Removed account '{email}' from queue.")
            self.reload_accounts()

    def _toggle_jump_account(self, email: str):
        if account_manager.jump_next_email and account_manager.jump_next_email.lower() == email.lower():
            account_manager.clear_jump_next_account()
            self.append_log("INFO", f"Cleared manual jump target for '{email}'. Returning to normal queue order.")
        else:
            account_manager.set_jump_next_account(email)
            self.append_log("INFO", f"🎯 Set '{email}' as immediate next account to switch to (Jump Target).")
        self.reload_accounts()

    def _clear_jump_clicked(self):
        account_manager.clear_jump_next_account()
        self.append_log("INFO", "Cleared manual jump override. Resumed normal circular queue rotation.")
        self.reload_accounts()

    # ─────────────────────────────────────────────────────────────────────────
    # SWITCH WORKFLOW CONTROLS
    # ─────────────────────────────────────────────────────────────────────────

    def _on_change_account_clicked(self):
        """Dedicated action button clicked to execute 'Change Google Account' pipeline."""
        self._on_run_switch_clicked()

    def _on_run_switch_clicked(self):
        """Dedicated action button clicked to execute full account switch pipeline."""
        enabled_accounts = account_manager.get_enabled_accounts()
        if not enabled_accounts:
            QMessageBox.warning(
                self,
                "No Enabled Accounts",
                "There are no enabled Google accounts in the circular queue.\n"
                "Please add or enable at least one account in the table below.",
            )
            return

        next_acc = account_manager.get_next_account()
        target_display = next_acc.email if next_acc else "next enabled account"

        if not self.log_console.isVisible():
            self._toggle_log_visibility()
        self.append_log("INFO", f"Initiating 'Change Google Account' pipeline (target in queue: '{target_display}')...")
        self.sig_run_switch.emit(next_acc.email if next_acc else "")

    def _on_cancel_clicked(self):
        self.append_log("WARN", "Cancellation requested by user.")
        self.sig_cancel_switch.emit()

    def set_switch_running_state(self, running: bool):
        """Updates UI control availability based on execution state."""
        self._is_running_switch = running
        self.btn_change_account.setEnabled(not running)
        self.btn_check_quota.setEnabled(not running)
        self.btn_cancel_switch.setEnabled(running)

        if running:
            self.switch_prog_bar.setValue(0)
            self.lbl_step_status.setText("Status: Initializing switch workflow...")

    def set_step(self, step_title: str, description: str):
        """Updates step status text."""
        self.lbl_step_status.setText(f"Status: {step_title} — {description}")

    def set_progress(self, current: int, total: int):
        """Updates step progress bar."""
        self.switch_prog_bar.setMaximum(total)
        self.switch_prog_bar.setValue(current)

    def append_log(self, level: str, message: str):
        """Appends formatted message to the live log terminal."""
        import html as _html
        ts = datetime.now().strftime("%H:%M:%S")
        colors = {
            "INFO": "#38bdf8",
            "WARN": "#f59e0b",
            "ERROR": "#ef4444",
            "SUCCESS": "#10b981",
        }
        color = colors.get(level.upper(), "#cbd5e1")
        safe_msg = _html.escape(str(message))
        line = f'<span style="color: #64748b;">[{ts}]</span> <span style="color: {color}; font-weight: bold;">[{level}]</span> <span style="color: #f1f5f9;">{safe_msg}</span>'
        self.log_console.appendHtml(line)
        self.log_console.verticalScrollBar().setValue(
            self.log_console.verticalScrollBar().maximum()
        )

    def _clear_log(self):
        self.log_console.clear()

    def _toggle_log_visibility(self):
        """Toggles the visibility of the telemetry log console."""
        is_visible = self.log_console.isVisible()
        self.log_console.setVisible(not is_visible)
        self.btn_toggle_log.setText("Show Log" if is_visible else "Hide Log")

    def _refresh_countdown_display(self):
        """Periodically refreshes countdown strings and hover tooltips in the table and Card A without rebuilding UI."""
        accounts = account_manager.accounts
        curr_email = account_manager.current_email

        # 1. Update Card A quota countdown and tooltip
        active_account = next((a for a in accounts if a.email.lower() == (curr_email or "").lower()), None)
        if active_account:
            five_rem = format_remaining_duration(active_account.five_hour_reset_at) if active_account.five_hour_reset_at else ""
            week_rem = format_remaining_duration(active_account.weekly_reset_at) if active_account.weekly_reset_at else ""

            five_str = f"{active_account.five_hour_percent}%" if active_account.five_hour_percent is not None else "—"
            if five_rem and five_rem not in ("—", "Reset now"):
                five_str += f" ({five_rem})"
            elif five_rem == "Reset now":
                five_str += " (Reset now)"

            week_str = f"{active_account.weekly_percent}%" if active_account.weekly_percent is not None else "—"
            if week_rem and week_rem not in ("—", "Reset now"):
                week_str += f" ({week_rem})"
            elif week_rem == "Reset now":
                week_str += " (Reset now)"

            self.lbl_active_quota.setText(f"5-Hour: {five_str}  |  Weekly: {week_str}")
            card_5h_tip = build_quota_tooltip(
                "5-Hour",
                active_account.five_hour_reset_at,
                active_account.five_hour_refresh_raw,
                last_checked_str=active_account.last_quota_checked,
                check_reason=active_account.last_quota_reason,
            )
            card_wk_tip = build_quota_tooltip(
                "Weekly",
                active_account.weekly_reset_at,
                active_account.weekly_refresh_raw,
                last_checked_str=active_account.last_quota_checked,
                check_reason=active_account.last_quota_reason,
            )
            self.lbl_active_quota.setToolTip(f"{card_5h_tip}\n\n{card_wk_tip}")
        elif curr_email:
            unreg_q = account_manager.get_unregistered_quota(curr_email)
            if unreg_q:
                five_pct = unreg_q.get("five_hour_percent")
                five_res = unreg_q.get("five_hour_reset_at")
                week_pct = unreg_q.get("weekly_percent")
                week_res = unreg_q.get("weekly_reset_at")

                five_rem = format_remaining_duration(five_res) if five_res else (unreg_q.get("five_hour_refresh_raw") or "")
                week_rem = format_remaining_duration(week_res) if week_res else (unreg_q.get("weekly_refresh_raw") or "")

                five_str = f"{five_pct}%" if five_pct is not None else "—"
                if five_rem and five_rem not in ("—", "Reset now", "Unknown"):
                    five_str += f" ({five_rem if five_rem.startswith('in ') else 'in ' + five_rem})"
                elif five_rem == "Reset now":
                    five_str += " (Reset now)"

                week_str = f"{week_pct}%" if week_pct is not None else "—"
                if week_rem and week_rem not in ("—", "Reset now", "Unknown"):
                    week_str += f" ({week_rem if week_rem.startswith('in ') else 'in ' + week_rem})"
                elif week_rem == "Reset now":
                    week_str += " (Reset now)"

                self.lbl_active_quota.setText(f"5-Hour: {five_str}  |  Weekly: {week_str}")
                card_5h_tip = build_quota_tooltip(
                    "5-Hour",
                    five_res,
                    unreg_q.get("five_hour_refresh_raw"),
                    last_checked_str=unreg_q.get("last_quota_checked", ""),
                    check_reason=unreg_q.get("last_quota_reason", ""),
                )
                card_wk_tip = build_quota_tooltip(
                    "Weekly",
                    week_res,
                    unreg_q.get("weekly_refresh_raw"),
                    last_checked_str=unreg_q.get("last_quota_checked", ""),
                    check_reason=unreg_q.get("last_quota_reason", ""),
                )
                self.lbl_active_quota.setToolTip(f"{card_5h_tip}\n\n{card_wk_tip}")

        # 2. Update table cells (Col 4 & 5: 5-Hour, Col 6 & 7: Weekly, Col 8: Status)
        for row in range(min(self.table.rowCount(), len(accounts))):
            acc = accounts[row]
            tip_5h_pct = build_percentage_tooltip(
                "5-Hour",
                percent=acc.five_hour_percent,
                last_checked_str=acc.last_quota_checked,
                check_reason=acc.last_quota_reason,
                reset_at_str=acc.five_hour_reset_at,
                raw_refresh=acc.five_hour_refresh_raw,
            )
            tip_5h_reset = build_quota_tooltip(
                "5-Hour",
                acc.five_hour_reset_at,
                acc.five_hour_refresh_raw,
                last_checked_str=acc.last_quota_checked,
                check_reason=acc.last_quota_reason,
            )

            tip_wk_pct = build_percentage_tooltip(
                "Weekly",
                percent=acc.weekly_percent,
                last_checked_str=acc.last_quota_checked,
                check_reason=acc.last_quota_reason,
                reset_at_str=acc.weekly_reset_at,
                raw_refresh=acc.weekly_refresh_raw,
            )
            tip_wk_reset = build_quota_tooltip(
                "Weekly",
                acc.weekly_reset_at,
                acc.weekly_refresh_raw,
                last_checked_str=acc.last_quota_checked,
                check_reason=acc.last_quota_reason,
            )

            # Col 4: 5-Hour Quota Pill (re-evaluate & repaint HTML badge dynamically)
            widget_5h = self.table.cellWidget(row, 4)
            if widget_5h and isinstance(widget_5h, QLabel):
                new_5h_pill = get_quota_pill_html(acc.five_hour_percent)
                if widget_5h.text() != new_5h_pill:
                    widget_5h.setText(new_5h_pill)
                widget_5h.setToolTip(tip_5h_pct)

            # Col 5: 5-Hour Reset countdown string
            item_5h = self.table.item(row, 5)
            if item_5h:
                new_5h = format_remaining_duration(acc.five_hour_reset_at) if acc.five_hour_reset_at else (acc.five_hour_refresh_raw or "—")
                if item_5h.text() != new_5h:
                    item_5h.setText(new_5h)
                item_5h.setToolTip(tip_5h_reset)

            # Col 6: Weekly Quota Pill (re-evaluate & repaint HTML badge dynamically)
            widget_wk = self.table.cellWidget(row, 6)
            if widget_wk and isinstance(widget_wk, QLabel):
                new_wk_pill = get_quota_pill_html(acc.weekly_percent)
                if widget_wk.text() != new_wk_pill:
                    widget_wk.setText(new_wk_pill)
                widget_wk.setToolTip(tip_wk_pct)

            # Col 7: Weekly Reset countdown string
            item_wk = self.table.item(row, 7)
            if item_wk:
                new_wk = format_remaining_duration(acc.weekly_reset_at) if acc.weekly_reset_at else (acc.weekly_refresh_raw or "—")
                if item_wk.text() != new_wk:
                    item_wk.setText(new_wk)
                item_wk.setToolTip(tip_wk_reset)

            # Col 8: Status badge (sync if freeze or jump status has changed)
            widget_status = self.table.cellWidget(row, 8)
            if widget_status and isinstance(widget_status, QLabel):
                is_this_jump = bool(account_manager.jump_next_email and account_manager.jump_next_email.lower() == acc.email.lower())
                if not acc.enabled:
                    status_text = "Disabled"
                elif account_manager.is_account_frozen(acc):
                    status_text = "Frozen"
                elif is_this_jump:
                    status_text = "Jump"
                elif acc.email.lower() == (curr_email or "").lower():
                    status_text = "Active"
                else:
                    status_text = "Ready" if acc.quota_status in ("Active", "Low Quota") else acc.quota_status
                new_badge = get_account_badge_html(status_text, acc.enabled)
                if widget_status.text() != new_badge:
                    widget_status.setText(new_badge)
                if status_text == "Frozen":
                    freeze_reasons = []
                    if acc.five_hour_reset_at:
                        freeze_reasons.append(f"5-Hour resets at {acc.five_hour_reset_at}")
                    if acc.weekly_reset_at:
                        freeze_reasons.append(f"Weekly resets at {acc.weekly_reset_at}")
                    widget_status.setToolTip("Frozen: " + " | ".join(freeze_reasons) if freeze_reasons else "Account frozen due to low quota")
                elif is_this_jump:
                    widget_status.setToolTip("Manual Jump Target: Scheduled to run next!")

