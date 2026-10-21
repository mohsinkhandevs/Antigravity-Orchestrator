"""
Dashboard Tab: Live execution dashboard, pipeline stepper HUD, model quota metrics, and real-time logs.
Modernized to Linear / Fluent 2 design standards with zero functional regressions.
"""

from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QProgressBar,
    QTextEdit,
    QFrame,
    QCheckBox,
    QApplication,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QTextCursor

from app.models import ExecutionStep
from app.config import config
from app.ui.widgets import ToggleSwitch, StatefulActionButton, ResponsiveScrollContainer
from app.ui.styles import (
    CARD_STYLE,
    COLOR_CANVAS,
    COLOR_SURFACE_0,
    COLOR_SURFACE_1,
    COLOR_SURFACE_2,
    COLOR_SURFACE_HOVER,
    COLOR_BORDER_DEFAULT,
    COLOR_BORDER_SUBTLE,
    COLOR_BORDER_STRONG,
    COLOR_PRIMARY,
    COLOR_PRIMARY_HOVER,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_DANGER,
    COLOR_INFO,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_FAINT,
)

PIPELINE_STEPS = [
    ("Window", ExecutionStep.FOCUS_WINDOW.value),
    ("New Chat", ExecutionStep.OPEN_CHAT.value),
    ("Project", ExecutionStep.SELECT_PROJECT.value),
    ("Prompt", ExecutionStep.INJECT_PROMPT.value),
    ("Generating", ExecutionStep.GENERATING.value),
    ("Cooldown", ExecutionStep.COOLDOWN.value),
]


class DashboardTab(QWidget):
    """Live execution dashboard, status HUD, pipeline breadcrumbs, and real-time logs."""

    sig_start_requested = pyqtSignal()
    sig_pause_requested = pyqtSignal()
    sig_resume_requested = pyqtSignal()
    sig_stop_requested = pyqtSignal()
    sig_skip_requested = pyqtSignal()
    sig_check_quota_requested = pyqtSignal()
    sig_change_account_requested = pyqtSignal()
    sig_quota_checked = pyqtSignal(dict)
    sig_quota_log = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_paused = False
        self.step_widgets = {}

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.scroll_area = ResponsiveScrollContainer(self, margin_h=12, margin_v=10, spacing=8)
        root_layout.addWidget(self.scroll_area)

        # ─────────────────────────────────────────────────────────────
        # 1. TOP ACTION TOOLBAR (CLUSTERED ACTION HIERARCHY)
        # ─────────────────────────────────────────────────────────────
        control_card = QFrame()
        control_card.setStyleSheet(CARD_STYLE)
        ctrl_layout = QHBoxLayout(control_card)
        ctrl_layout.setContentsMargins(12, 8, 12, 8)
        ctrl_layout.setSpacing(8)

        # Cluster A: Primary Automation Controls (Unified Stateful Toggle)
        self.start_pause_btn = StatefulActionButton(self)
        self.start_pause_btn.setMinimumHeight(32)
        self.start_pause_btn.setMinimumWidth(140)
        self.start_pause_btn.register_state("IDLE", "Start Queue", icon="▶", variant="success", tooltip="Start queue automation")
        self.start_pause_btn.register_state("RUNNING", "Pause Queue", icon="⏸", variant="warning", tooltip="Pause queue automation")
        self.start_pause_btn.register_state("PAUSED", "Resume Queue", icon="▶", variant="primary", tooltip="Resume prompt queue execution")
        self.start_pause_btn.set_state("IDLE")
        self.start_pause_btn.clicked.connect(self._on_start_pause_toggle_clicked)
        ctrl_layout.addWidget(self.start_pause_btn)

        # Aliases for backward compatibility
        self.start_btn = self.start_pause_btn
        self.pause_btn = self.start_pause_btn

        self.skip_btn = QPushButton("⏭  Next Prompt")
        self.skip_btn.setObjectName("btn-secondary")
        self.skip_btn.setProperty("variant", "secondary")
        self.skip_btn.setMinimumHeight(32)
        self.skip_btn.setEnabled(False)
        self.skip_btn.setToolTip("Immediately finish current prompt and advance to next")
        self.skip_btn.clicked.connect(self.sig_skip_requested.emit)
        ctrl_layout.addWidget(self.skip_btn)

        self.stop_btn = QPushButton("⏹  Stop")
        self.stop_btn.setObjectName("btn-danger")
        self.stop_btn.setProperty("variant", "danger")
        self.stop_btn.setMinimumHeight(32)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.sig_stop_requested.emit)
        ctrl_layout.addWidget(self.stop_btn)

        # Subtle Vertical Divider
        div1 = self._create_toolbar_divider()
        ctrl_layout.addWidget(div1)

        # Cluster B: Quick Diagnostic & Account Utilities
        self.btn_quota = QPushButton("⚡ Check Limits")
        self.btn_quota.setObjectName("btn-secondary")
        self.btn_quota.setProperty("variant", "secondary")
        self.btn_quota.setMinimumHeight(32)
        self.btn_quota.setToolTip("Inspects live remaining quotas directly from Antigravity")
        self.btn_quota.clicked.connect(self._on_btn_quota_clicked)
        ctrl_layout.addWidget(self.btn_quota)

        self.btn_change_account = QPushButton("🔄 Switch Account")
        self.btn_change_account.setObjectName("btn-secondary")
        self.btn_change_account.setProperty("variant", "secondary")
        self.btn_change_account.setMinimumHeight(32)
        self.btn_change_account.setToolTip("Switch to next Google account.")
        self.btn_change_account.clicked.connect(self.sig_change_account_requested.emit)
        ctrl_layout.addWidget(self.btn_change_account)

        ctrl_layout.addStretch()
        self.scroll_area.add_card(control_card)

        # ─────────────────────────────────────────────────────────────
        # 2. CONNECTED PIPELINE STEPPER HUD CARD
        # ─────────────────────────────────────────────────────────────
        hud_card = QFrame()
        hud_card.setStyleSheet(CARD_STYLE)
        hud_layout = QVBoxLayout(hud_card)
        hud_layout.setSpacing(8)
        hud_layout.setContentsMargins(14, 10, 14, 10)

        # Card Header
        hud_header = QHBoxLayout()
        hud_title = QLabel("EXECUTION PIPELINE")
        hud_title.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.6px;"
        )
        hud_header.addWidget(hud_title)
        hud_header.addStretch()
        hud_layout.addLayout(hud_header)

        # Connected Step Stepper Row (Compact Horizontal Flex Layout)
        steps_row = QHBoxLayout()
        steps_row.setSpacing(4)

        for i, (label, step_enum_val) in enumerate(PIPELINE_STEPS):
            step_frame = QFrame()
            step_frame.setStyleSheet(
                f"background-color: #101625; border: 1px solid {COLOR_BORDER_SUBTLE}; border-left: 3px solid {COLOR_BORDER_DEFAULT}; border-radius: 6px;"
            )
            step_hl = QHBoxLayout(step_frame)
            step_hl.setContentsMargins(6, 4, 6, 4)
            step_hl.setSpacing(5)

            # Step number badge
            num_badge = QLabel(f"{i + 1}")
            num_badge.setStyleSheet(
                f"background-color: {COLOR_SURFACE_2}; color: {COLOR_TEXT_MUTED}; border-radius: 8px; "
                f"font-size: 10px; font-weight: 700; min-width: 16px; max-width: 16px; min-height: 16px; max-height: 16px;"
            )
            num_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            step_hl.addWidget(num_badge)

            step_text_box = QVBoxLayout()
            step_text_box.setSpacing(1)
            step_text_box.setContentsMargins(0, 0, 0, 0)

            title_lbl = QLabel(label)
            title_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {COLOR_TEXT_SECONDARY};")
            step_text_box.addWidget(title_lbl)

            status_lbl = QLabel("Waiting")
            status_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)
            status_lbl.setStyleSheet(f"font-size: 9px; font-weight: 600; color: {COLOR_TEXT_FAINT};")
            step_text_box.addWidget(status_lbl)

            step_hl.addLayout(step_text_box)
            steps_row.addWidget(step_frame, 1)

            self.step_widgets[step_enum_val] = {
                "frame": step_frame,
                "title": title_lbl,
                "status": status_lbl,
                "num": num_badge,
            }

        hud_layout.addLayout(steps_row)

        # Active Prompt Description & Timer Status Row
        status_row = QHBoxLayout()
        status_row.setSpacing(8)

        self.lbl_active_prompt = QLabel("Idle")
        self.lbl_active_prompt.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {COLOR_TEXT_PRIMARY};")
        status_row.addWidget(self.lbl_active_prompt)

        self.lbl_quota_summary = QLabel("Limits: Auto-check")
        self.lbl_quota_summary.setStyleSheet(
            f"font-size: 11px; color: {COLOR_TEXT_MUTED}; background-color: {COLOR_SURFACE_2}; "
            f"padding: 2px 8px; border-radius: 4px; border: 1px solid {COLOR_BORDER_SUBTLE};"
        )
        status_row.addWidget(self.lbl_quota_summary)

        status_row.addStretch()

        timeout_text = "Timeout: Infinite" if getattr(config, "infinite_generation_timeout", True) else f"Timeout: {config.generation_timeout}s"
        self.lbl_timeout_mode = QLabel(timeout_text)
        self.lbl_timeout_mode.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: #a5b4fc; background-color: #171b30; "
            f"padding: 2px 8px; border-radius: 4px; border: 1px solid #3730a3;"
        )
        status_row.addWidget(self.lbl_timeout_mode)

        self.lbl_timer = QLabel("0.0s")
        self.lbl_timer.setStyleSheet(
            "font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 11px; font-weight: 700; "
            "color: #38bdf8; background-color: #0b2538; padding: 2px 8px; border-radius: 4px; border: 1px solid #0284c7;"
        )
        status_row.addWidget(self.lbl_timer)
        hud_layout.addLayout(status_row)

        # Overall Queue Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(16)
        self.progress_bar.setFormat("0 / 0 (0%)")
        hud_layout.addWidget(self.progress_bar)

        self.scroll_area.add_card(hud_card)

        # ─────────────────────────────────────────────────────────────
        # 3. REAL-TIME LOG VIEWER (CONSOLE TERMINAL STYLE)
        # ─────────────────────────────────────────────────────────────
        log_card = QFrame()
        log_card.setStyleSheet(CARD_STYLE)
        log_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        log_layout = QVBoxLayout(log_card)
        log_layout.setSpacing(8)
        log_layout.setContentsMargins(14, 12, 14, 10)

        # Log Header Bar
        log_header = QHBoxLayout()
        log_title = QLabel("LOGS")
        log_title.setStyleSheet(
            f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.6px;"
        )
        log_header.addWidget(log_title)
        log_header.addStretch()

        lbl_auto = QLabel("Auto-scroll")
        lbl_auto.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 600;")
        log_header.addWidget(lbl_auto)

        self.chk_autoscroll = ToggleSwitch(self, checked=True)
        self.chk_autoscroll.setToolTip("Toggle automatic scrolling to latest log message")
        log_header.addWidget(self.chk_autoscroll)

        clear_log_btn = QPushButton("🗑 Clear")
        clear_log_btn.setObjectName("btn-secondary")
        clear_log_btn.setProperty("variant", "ghost")
        clear_log_btn.setMinimumHeight(24)
        clear_log_btn.clicked.connect(self.log_text_clear)
        log_header.addWidget(clear_log_btn)

        copy_log_btn = QPushButton("📋 Copy")
        copy_log_btn.setObjectName("btn-secondary")
        copy_log_btn.setProperty("variant", "ghost")
        copy_log_btn.setMinimumHeight(24)
        copy_log_btn.clicked.connect(self._copy_logs)
        log_header.addWidget(copy_log_btn)

        log_layout.addLayout(log_header)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(180)
        self.log_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        try:
            self.log_text.document().setMaximumBlockCount(2000)
        except Exception:
            pass
        self.log_text.setStyleSheet(
            "background-color: #070a12; border: 1px solid #192236; border-radius: 6px; "
            "font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 12px; color: #cbd5e1; padding: 6px;"
        )
        log_layout.addWidget(self.log_text)

        self.scroll_area.add_card(log_card, stretch=1)

        self.update_quota_banner_status()

    def _create_toolbar_divider(self) -> QFrame:
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setStyleSheet(
            f"color: {COLOR_BORDER_DEFAULT}; background-color: {COLOR_BORDER_DEFAULT}; width: 1px; margin: 4px 2px;"
        )
        return divider

    def log_text_clear(self):
        self.log_text.clear()

    def update_quota_banner_status(self):
        if not getattr(config, "check_quota_each_prompt", True):
            self.lbl_quota_summary.setText("Model Limits: Auto-check Disabled")
            self.lbl_quota_summary.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
        else:
            self.lbl_quota_summary.setText("Model Limits: Auto-check Enabled")
            self.lbl_quota_summary.setStyleSheet("color: #38bdf8; font-size: 11px;")

    def append_log(self, level: str, message: str):
        """Appends a color-coded log message to the viewer."""
        import html as _html
        now = datetime.now().strftime("%H:%M:%S")
        color_map = {
            "INFO": "#38bdf8",     # Sky blue
            "SUCCESS": "#34d399",  # Mint green
            "WARN": "#fbbf24",     # Amber yellow
            "ERROR": "#f87171",    # Coral red
        }
        color = color_map.get(level.upper(), "#94a3b8")
        safe_msg = _html.escape(str(message))

        html = (
            f'<div style="margin-bottom: 2px; line-height: 1.35;">'
            f'<span style="color: #64748b; font-size: 11px;">[{now}]</span> '
            f'<span style="color: {color}; font-weight: 700;">[{level.upper()}]</span> '
            f'<span style="color: #e2e8f0;">{safe_msg}</span>'
            f'</div>'
        )
        self.log_text.append(html)

        if self.chk_autoscroll.isChecked():
            cursor = self.log_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.log_text.setTextCursor(cursor)

    def set_execution_step(self, step_value: str, description: str):
        """Updates the pipeline HUD visual state."""
        self.lbl_active_prompt.setText(f"Status: {description}")

        is_active_found = False
        for label, step_val in PIPELINE_STEPS:
            w = self.step_widgets.get(step_val)
            if not w:
                continue

            if step_val == step_value:
                # Active step
                w["frame"].setStyleSheet(
                    "background-color: #1e1b4b; border: 1px solid #3730a3; border-left: 3px solid #6366f1; border-radius: 6px;"
                )
                w["title"].setStyleSheet("font-size: 11px; font-weight: 700; color: #ffffff;")
                w["status"].setText("ACTIVE ●")
                w["status"].setStyleSheet("font-size: 10px; font-weight: 700; color: #a5b4fc;")
                if "num" in w:
                    w["num"].setStyleSheet(
                        "background-color: #6366f1; color: #ffffff; border-radius: 8px; "
                        "font-size: 10px; font-weight: 700; min-width: 16px; max-width: 16px; min-height: 16px; max-height: 16px;"
                    )
                is_active_found = True
            elif not is_active_found:
                # Prior step (Done)
                w["frame"].setStyleSheet(
                    "background-color: #0b1e17; border: 1px solid #064e3b; border-left: 3px solid #10b981; border-radius: 6px;"
                )
                w["title"].setStyleSheet("font-size: 11px; font-weight: 600; color: #34d399;")
                w["status"].setText("Done ✓")
                w["status"].setStyleSheet("font-size: 10px; font-weight: 600; color: #6ee7b7;")
                if "num" in w:
                    w["num"].setStyleSheet(
                        "background-color: #059669; color: #ffffff; border-radius: 8px; "
                        "font-size: 10px; font-weight: 700; min-width: 16px; max-width: 16px; min-height: 16px; max-height: 16px;"
                    )
            else:
                # Future step (Waiting)
                w["frame"].setStyleSheet(
                    f"background-color: #101625; border: 1px solid {COLOR_BORDER_SUBTLE}; border-left: 3px solid {COLOR_BORDER_DEFAULT}; border-radius: 6px;"
                )
                w["title"].setStyleSheet(f"font-size: 11px; font-weight: 600; color: {COLOR_TEXT_SECONDARY};")
                w["status"].setText("Waiting")
                w["status"].setStyleSheet(f"font-size: 10px; color: {COLOR_TEXT_FAINT};")
                if "num" in w:
                    w["num"].setStyleSheet(
                        f"background-color: {COLOR_SURFACE_2}; color: {COLOR_TEXT_MUTED}; border-radius: 8px; "
                        f"font-size: 10px; font-weight: 700; min-width: 16px; max-width: 16px; min-height: 16px; max-height: 16px;"
                    )

    def reset_pipeline_hud(self):
        """Resets all breadcrumbs to waiting."""
        for label, step_val in PIPELINE_STEPS:
            w = self.step_widgets.get(step_val)
            if w:
                w["frame"].setStyleSheet(
                    f"background-color: #101625; border: 1px solid {COLOR_BORDER_SUBTLE}; border-left: 3px solid {COLOR_BORDER_DEFAULT}; border-radius: 6px;"
                )
                w["title"].setStyleSheet(f"font-size: 11px; font-weight: 600; color: {COLOR_TEXT_SECONDARY};")
                w["status"].setText("Waiting")
                w["status"].setStyleSheet(f"font-size: 10px; color: {COLOR_TEXT_FAINT};")
                if "num" in w:
                    w["num"].setStyleSheet(
                        f"background-color: {COLOR_SURFACE_2}; color: {COLOR_TEXT_MUTED}; border-radius: 8px; "
                        f"font-size: 10px; font-weight: 700; min-width: 16px; max-width: 16px; min-height: 16px; max-height: 16px;"
                    )
        self.lbl_timer.setText("0.0s")

    def update_queue_progress(self, completed: int, total: int):
        """Updates overall progress bar."""
        if total > 0:
            pct = int((completed / total) * 100)
            self.progress_bar.setValue(pct)
            self.progress_bar.setFormat(f"{completed} / {total} ({pct}%)")
        else:
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("0 / 0 (0%)")

    def update_timeout_display(self):
        timeout_text = "Timeout: Infinite" if getattr(config, "infinite_generation_timeout", True) else f"Timeout: {config.generation_timeout}s"
        self.lbl_timeout_mode.setText(timeout_text)

    def update_timer(self, seconds: float):
        s = int(seconds)
        if s >= 3600:
            h = s // 3600
            m = (s % 3600) // 60
            sec = s % 60
            dur_str = f"{h}h {m:02d}m {sec:02d}s"
        elif s >= 60:
            m = s // 60
            sec = s % 60
            dur_str = f"{m}m {sec:02d}s"
        else:
            dur_str = f"{seconds:.1f}s"
        self.lbl_timer.setText(dur_str)

    def _on_start_pause_toggle_clicked(self):
        curr = str(self.start_pause_btn.current_state() if callable(self.start_pause_btn.current_state) else self.start_pause_btn.current_state)
        if curr == "IDLE":
            self.sig_start_requested.emit()
        elif curr == "RUNNING":
            self.sig_pause_requested.emit()
        elif curr == "PAUSED":
            self.sig_resume_requested.emit()

    def set_running_state(self, state: str):
        """Toggles button availability based on worker state."""
        if state == "RUNNING":
            self.start_pause_btn.setEnabled(True)
            self.start_pause_btn.set_state("RUNNING")
            self.skip_btn.setEnabled(True)
            self.stop_btn.setEnabled(True)
            self.btn_quota.setEnabled(False)
            self.btn_change_account.setEnabled(False)
            self.is_paused = False
        elif state == "PAUSED":
            self.start_pause_btn.setEnabled(True)
            self.start_pause_btn.set_state("PAUSED")
            self.skip_btn.setEnabled(True)
            self.stop_btn.setEnabled(True)
            self.btn_quota.setEnabled(False)
            self.btn_change_account.setEnabled(False)
            self.is_paused = True
        else:  # IDLE or STOPPED
            self.start_pause_btn.setEnabled(True)
            self.start_pause_btn.set_state("IDLE")
            self.skip_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.btn_quota.setEnabled(True)
            self.btn_change_account.setEnabled(True)
            self.is_paused = False
            self.reset_pipeline_hud()

    def _toggle_pause(self):
        if self.is_paused:
            self.sig_resume_requested.emit()
        else:
            self.sig_pause_requested.emit()

    def _copy_logs(self):
        QApplication.clipboard().setText(self.log_text.toPlainText())
        self.append_log("INFO", "Logs copied to clipboard.")

    def _on_btn_quota_clicked(self):
        self.sig_check_quota_requested.emit()

    def run_quota_inspection(
        self,
        target_hwnd: int = 0,
        is_manual_mode: bool = False,
        countdown_seconds: int = 0,
    ):
        """Public method to start quota inspection."""
        self._run_quota_check(
            target_hwnd=target_hwnd,
            is_manual_mode=is_manual_mode,
            countdown_seconds=countdown_seconds,
        )

    def _run_quota_check(
        self,
        target_hwnd: int = 0,
        is_manual_mode: bool = False,
        countdown_seconds: int = 0,
    ):
        """Runs on-demand Antigravity model quota inspection in a non-blocking background thread."""
        self.btn_quota.setEnabled(False)
        self.btn_quota.setText("⏳ Checking...")
        self.append_log("INFO", "Starting on-demand Antigravity model quota inspection (Settings -> Models -> Refresh)...")

        class QuotaWorker(QThread):
            sig_log = pyqtSignal(str, str)
            sig_finished = pyqtSignal(dict)

            def __init__(self, target_hwnd=0, is_manual_mode=False, countdown_seconds=0, parent=None):
                super().__init__(parent)
                self.target_hwnd = target_hwnd
                self.is_manual_mode = is_manual_mode
                self.countdown_seconds = countdown_seconds

            def run(self):
                try:
                    import time
                    if self.countdown_seconds > 0:
                        for c in range(self.countdown_seconds, 0, -1):
                            self.sig_log.emit("INFO", f"⏳ Inspecting quota in {c}s... Arrange Google Antigravity in front!")
                            time.sleep(1.0)
                    from app.quota_checker import inspect_model_quotas
                    res = inspect_model_quotas(
                        log_callback=lambda lvl, msg: self.sig_log.emit(lvl, msg),
                        is_manual_mode=self.is_manual_mode,
                        target_hwnd=self.target_hwnd,
                    )
                    self.sig_finished.emit(res)
                except Exception as e:
                    self.sig_finished.emit({"success": False, "models": [], "error": str(e)})

        self._quota_thread = QuotaWorker(
            target_hwnd=target_hwnd,
            is_manual_mode=is_manual_mode,
            countdown_seconds=countdown_seconds,
            parent=self,
        )
        self._quota_thread.sig_log.connect(self.append_log)
        self._quota_thread.sig_log.connect(self.sig_quota_log)
        self._quota_thread.sig_finished.connect(self._on_quota_finished)
        self._quota_thread.start()

    def update_quota_display(self, res: dict):
        gem = res.get("gemini", {})
        five_pct = gem.get("five_hour_percent")
        five_ref = gem.get("five_hour_refresh", "Unknown")
        week_pct = gem.get("weekly_percent")
        week_ref = gem.get("weekly_refresh", "Unknown")
        is_safe = res.get("is_safe_to_prompt", True)

        if five_pct is not None or week_pct is not None:
            five_str = f"{five_pct}%" if five_pct is not None else "N/A"
            week_str = f"{week_pct}%" if week_pct is not None else "N/A"
            banner_text = f"Gemini Quotas: 5-Hour: {five_str} (in {five_ref}) | Weekly: {week_str} (in {week_ref})"
            if not is_safe:
                self.lbl_quota_summary.setText(f"⚠️ {banner_text} — BELOW SAFETY THRESHOLD!")
                self.lbl_quota_summary.setStyleSheet("color: #f87171; font-weight: 700; font-size: 11px;")
            else:
                self.lbl_quota_summary.setText(f"✓ {banner_text}")
                self.lbl_quota_summary.setStyleSheet("color: #34d399; font-weight: 600; font-size: 11px;")
        elif res.get("summary"):
            self.lbl_quota_summary.setText(res.get("summary"))
            self.lbl_quota_summary.setStyleSheet("color: #38bdf8; font-size: 11px;")
        else:
            self.lbl_quota_summary.setText("Model Limits: Inspection completed (see logs)")
            self.lbl_quota_summary.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 11px;")

    def _on_quota_finished(self, res: dict):
        self.btn_quota.setEnabled(True)
        self.btn_quota.setText("⚡ Check Model Limits")
        self.update_quota_display(res)
        self.sig_quota_checked.emit(res)
