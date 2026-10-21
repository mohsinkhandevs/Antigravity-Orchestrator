"""
Settings Tab for Antigravity CDP.
Modernized to Linear / Fluent 2 design standards with zero functional regressions.
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QPushButton,
    QScrollArea,
    QFrame,
    QMessageBox,
    QRadioButton,
    QButtonGroup,
    QCheckBox,
)
from PyQt6.QtCore import Qt, pyqtSignal

from app.config import config
from app.ui.styles import (
    CARD_STYLE,
    COLOR_CANVAS,
    COLOR_SURFACE_0,
    COLOR_SURFACE_1,
    COLOR_SURFACE_2,
    COLOR_BORDER_DEFAULT,
    COLOR_BORDER_SUBTLE,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_DANGER,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_FAINT,
)
from app.ui.widgets import ToggleSwitch, SegmentedControl
from cdp.client import is_port_listening, AntigravityCDPClient


class SettingsTab(QWidget):
    sig_settings_saved = pyqtSignal()
    sig_change_account_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # ─────────────────────────────────────────────────────────────
        # Scrollable Configuration Grid
        # ─────────────────────────────────────────────────────────────
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        cols_layout = QHBoxLayout(container)
        cols_layout.setContentsMargins(0, 0, 0, 0)
        cols_layout.setSpacing(12)

        left_col = QVBoxLayout()
        left_col.setSpacing(12)

        right_col = QVBoxLayout()
        right_col.setSpacing(12)

        # ─────────────────────────────────────────────────────────────
        # 1. CDP CONNECTION & TARGET PROJECT CARD (Left Col)
        # ─────────────────────────────────────────────────────────────
        cdp_card = QFrame()
        cdp_card.setStyleSheet(CARD_STYLE)
        cdp_layout = QVBoxLayout(cdp_card)
        cdp_layout.setContentsMargins(14, 12, 14, 12)
        cdp_layout.setSpacing(10)

        lbl_cdp_title = QLabel("PARALLEL CONCURRENCY & HUD")
        lbl_cdp_title.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.6px;")
        cdp_layout.addWidget(lbl_cdp_title)

        cdp_grid = QGridLayout()
        cdp_grid.setHorizontalSpacing(14)
        cdp_grid.setVerticalSpacing(10)

        # Max Concurrent Projects
        lbl_concurrent = QLabel("Max Concurrent:")
        lbl_concurrent.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-weight: 600; font-size: 12px;")
        cdp_grid.addWidget(lbl_concurrent, 0, 0)

        self.spin_max_concurrent = QSpinBox()
        self.spin_max_concurrent.setRange(1, 20)
        self.spin_max_concurrent.setValue(getattr(config, "max_concurrent_projects", 3))
        self.spin_max_concurrent.setMinimumHeight(32)
        self.spin_max_concurrent.setMaximumWidth(130)
        self.spin_max_concurrent.setToolTip("Maximum number of concurrent projects generating simultaneously")
        cdp_grid.addWidget(self.spin_max_concurrent, 0, 1)

        # Polling Interval
        lbl_poll = QLabel("Polling Interval:")
        lbl_poll.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-weight: 600; font-size: 12px;")
        cdp_grid.addWidget(lbl_poll, 1, 0)

        self.spin_poll_interval = QSpinBox()
        self.spin_poll_interval.setRange(1, 60)
        self.spin_poll_interval.setValue(getattr(config, "polling_interval_seconds", 2))
        self.spin_poll_interval.setSuffix("s")
        self.spin_poll_interval.setMinimumHeight(32)
        self.spin_poll_interval.setMaximumWidth(130)
        self.spin_poll_interval.setToolTip("Seconds between CDP DOM status polling intervals")
        cdp_grid.addWidget(self.spin_poll_interval, 1, 1)

        cdp_layout.addLayout(cdp_grid)
        cdp_layout.addWidget(self._create_divider())

        # Independent Project Resume Toggle
        self.chk_independent_resume = ToggleSwitch(self, checked=getattr(config, "enable_independent_project_resume", True))
        self.chk_independent_resume.setToolTip("Allow individual halted projects to resume once quota recovers without waiting for all to finish")
        row_ind_resume = self._create_toggle_row(
            "Independent Project Resume",
            "Allow individual halted projects to resume as quota recovers without waiting for all to finish",
            self.chk_independent_resume,
        )
        cdp_layout.addWidget(row_ind_resume)

        # Foreground HUD Toggle
        self.chk_foreground_hud = ToggleSwitch(self, checked=getattr(config, "enable_foreground_hud", True))
        self.chk_foreground_hud.setToolTip("Automatically hide floating status HUD when switching away from Antigravity")
        row_fg_hud = self._create_toggle_row(
            "Foreground HUD Guard",
            "Show floating status HUD only when Google Antigravity window is focused",
            self.chk_foreground_hud,
        )
        cdp_layout.addWidget(row_fg_hud)

        left_col.addWidget(cdp_card)

        # ─────────────────────────────────────────────────────────────
        # 2. TIMING & SHORTCUTS CARD (Left Col)
        # ─────────────────────────────────────────────────────────────
        timing_card = QFrame()
        timing_card.setStyleSheet(CARD_STYLE)
        timing_layout = QVBoxLayout(timing_card)
        timing_layout.setContentsMargins(14, 12, 14, 12)
        timing_layout.setSpacing(10)

        lbl_t_title = QLabel("TIMING & SHORTCUTS")
        lbl_t_title.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.6px;")
        timing_layout.addWidget(lbl_t_title)

        timing_grid = QGridLayout()
        timing_grid.setHorizontalSpacing(14)
        timing_grid.setVerticalSpacing(10)

        lbl_em = QLabel("Emergency Stop:")
        lbl_em.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-weight: 600; font-size: 12px;")
        timing_grid.addWidget(lbl_em, 0, 0)

        self.txt_emergency_hotkey = QLineEdit(getattr(config, "emergency_hotkey", "F10"))
        self.txt_emergency_hotkey.setMinimumHeight(32)
        self.txt_emergency_hotkey.setMaximumWidth(160)
        self.txt_emergency_hotkey.setToolTip("Global keyboard hotkey to immediately cancel execution")
        timing_grid.addWidget(self.txt_emergency_hotkey, 0, 1)

        lbl_hud = QLabel("Restore Studio:")
        lbl_hud.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-weight: 600; font-size: 12px;")
        timing_grid.addWidget(lbl_hud, 1, 0)

        self.txt_hud_hotkey = QLineEdit(getattr(config, "hud_hotkey", "Ctrl+Shift+H"))
        self.txt_hud_hotkey.setMinimumHeight(32)
        self.txt_hud_hotkey.setMaximumWidth(160)
        self.txt_hud_hotkey.setToolTip("Global keyboard shortcut to bring Studio back into the foreground")
        timing_grid.addWidget(self.txt_hud_hotkey, 1, 1)

        timing_layout.addLayout(timing_grid)
        left_col.addWidget(timing_card)
        left_col.addStretch()

        # ─────────────────────────────────────────────────────────────
        # 3. GEMINI QUOTA SAFETY GATES CARD (Right Col)
        # ─────────────────────────────────────────────────────────────
        quota_card = QFrame()
        quota_card.setStyleSheet(CARD_STYLE)
        quota_layout = QVBoxLayout(quota_card)
        quota_layout.setContentsMargins(14, 12, 14, 12)
        quota_layout.setSpacing(10)

        lbl_q_title = QLabel("QUOTA GUARDRAILS & RESUME")
        lbl_q_title.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 0.6px;")
        quota_layout.addWidget(lbl_q_title)

        quota_grid = QGridLayout()
        quota_grid.setHorizontalSpacing(14)
        quota_grid.setVerticalSpacing(10)

        # 5-Hour Limit
        lbl_5h = QLabel("Min 5-Hour Quota:")
        lbl_5h.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-weight: 600; font-size: 12px;")
        quota_grid.addWidget(lbl_5h, 0, 0)

        self.spin_min_5h = QSpinBox()
        self.spin_min_5h.setRange(0, 100)
        self.spin_min_5h.setValue(getattr(config, "min_gemini_five_hour_quota", 15))
        self.spin_min_5h.setSuffix("%")
        self.spin_min_5h.setMinimumHeight(32)
        self.spin_min_5h.setMaximumWidth(130)
        self.spin_min_5h.setToolTip("Threshold under which safety gate activates")
        quota_grid.addWidget(self.spin_min_5h, 0, 1)

        # Weekly Limit
        lbl_week = QLabel("Min Weekly Quota:")
        lbl_week.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-weight: 600; font-size: 12px;")
        quota_grid.addWidget(lbl_week, 1, 0)

        self.spin_min_week = QSpinBox()
        self.spin_min_week.setRange(0, 100)
        self.spin_min_week.setValue(getattr(config, "min_gemini_weekly_quota", 4))
        self.spin_min_week.setSuffix("%")
        self.spin_min_week.setMinimumHeight(32)
        self.spin_min_week.setMaximumWidth(130)
        self.spin_min_week.setToolTip("Threshold under which safety gate activates")
        quota_grid.addWidget(self.spin_min_week, 1, 1)

        # Quota Cache Duration
        lbl_cache = QLabel("Quota Cache Time:")
        lbl_cache.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-weight: 600; font-size: 12px;")
        quota_grid.addWidget(lbl_cache, 2, 0)

        self.spin_quota_cache_seconds = QSpinBox()
        self.spin_quota_cache_seconds.setRange(30, 120)
        current_cache = getattr(config, "quota_cache_seconds", 60)
        self.spin_quota_cache_seconds.setValue(max(30, min(120, current_cache)))
        self.spin_quota_cache_seconds.setSuffix("s")
        self.spin_quota_cache_seconds.setMinimumHeight(32)
        self.spin_quota_cache_seconds.setMaximumWidth(130)
        self.spin_quota_cache_seconds.setToolTip("Skip DOM quota check if checked within this duration across queues (30s - 120s)")
        quota_grid.addWidget(self.spin_quota_cache_seconds, 2, 1)

        # Low Quota Policy (Segmented Control)
        lbl_action = QLabel("On Low Quota:")
        lbl_action.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-weight: 600; font-size: 12px;")
        quota_grid.addWidget(lbl_action, 3, 0)

        self.seg_quota_policy = SegmentedControl(self)
        self.btn_policy_switch = self.seg_quota_policy.add_segment("change_account", "🔄 Switch Account")
        self.btn_policy_wait = self.seg_quota_policy.add_segment("wait_for_reset", "⏳ Wait for Reset")
        self.action_group = self.seg_quota_policy._group

        # Backward compatible aliases
        self.rb_change_account = self.btn_policy_switch
        self.rb_wait_reset = self.btn_policy_wait

        initial_policy = getattr(config, "low_quota_action", "change_account")
        self.seg_quota_policy.set_active_segment(initial_policy)
        quota_grid.addWidget(self.seg_quota_policy, 3, 1)

        quota_layout.addLayout(quota_grid)
        quota_layout.addWidget(self._create_divider())

        # Pre-Switch Confirmation HUD Toggle
        self.chk_pre_switch_hud = ToggleSwitch(self, checked=getattr(config, "enable_pre_switch_hud", True))
        self.chk_pre_switch_hud.setToolTip(
            "After clicking Sign Out, pause and show a top-most HUD with a countdown timer and 'Change Now' button "
            "before clicking 'Continue with Google' and taking mouse control. If disabled, switches immediately."
        )
        row_pre_hud = self._create_toggle_row(
            "Account Switch Confirmation HUD",
            "Show countdown HUD with 'Change Now' button before taking mouse control for login",
            self.chk_pre_switch_hud,
        )
        quota_layout.addWidget(row_pre_hud)

        # Confirmation Timer Row
        timer_row = QHBoxLayout()
        timer_row.setContentsMargins(16, 0, 0, 0)
        timer_row.setSpacing(10)
        lbl_timer_sec = QLabel("Confirmation Countdown:")
        lbl_timer_sec.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px; font-weight: 600;")
        timer_row.addWidget(lbl_timer_sec)

        self.spin_pre_switch_seconds = QSpinBox()
        self.spin_pre_switch_seconds.setRange(5, 300)
        self.spin_pre_switch_seconds.setValue(getattr(config, "pre_switch_countdown_seconds", 30))
        self.spin_pre_switch_seconds.setSuffix("s")
        self.spin_pre_switch_seconds.setMinimumHeight(32)
        self.spin_pre_switch_seconds.setMaximumWidth(110)
        self.spin_pre_switch_seconds.setEnabled(self.chk_pre_switch_hud.isChecked())
        self.spin_pre_switch_seconds.setToolTip(
            "Seconds to wait on the confirmation HUD before automatically clicking 'Continue with Google'"
        )
        self.chk_pre_switch_hud.toggled.connect(self.spin_pre_switch_seconds.setEnabled)
        timer_row.addWidget(self.spin_pre_switch_seconds)
        timer_row.addStretch()
        quota_layout.addLayout(timer_row)

        quota_layout.addWidget(self._create_divider())

        # In-Chat Auto-Resume Toggle
        self.chk_auto_resume = ToggleSwitch(self, checked=getattr(config, "enable_auto_resume_same_chat", True))
        self.chk_auto_resume.setToolTip("Inject mini-prompt to pick up where generation was cut off")
        row_auto_resume = self._create_toggle_row(
            "In-Chat Auto-Resume",
            "Auto-resume in same chat on quota interruption via mini-prompt continuation",
            self.chk_auto_resume,
        )
        quota_layout.addWidget(row_auto_resume)

        resume_grid = QGridLayout()
        resume_grid.setHorizontalSpacing(14)
        resume_grid.setVerticalSpacing(10)

        # Resume Mini-Prompt Input
        lbl_mini = QLabel("Resume Mini-Prompt:")
        lbl_mini.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-weight: 600; font-size: 12px;")
        resume_grid.addWidget(lbl_mini, 0, 0)

        self.txt_resume_mini_prompt = QLineEdit(getattr(config, "resume_mini_prompt", "Please resume where you left off and finish the remaining work."))
        self.txt_resume_mini_prompt.setMinimumHeight(32)
        self.txt_resume_mini_prompt.setPlaceholderText("Please resume where you left off and finish the remaining work.")
        self.txt_resume_mini_prompt.setToolTip("Prompt injected automatically when resuming interrupted chats")
        resume_grid.addWidget(self.txt_resume_mini_prompt, 0, 1)

        # Max Resumes Spinbox
        lbl_max_resumes = QLabel("Max Consec Resumes:")
        lbl_max_resumes.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-weight: 600; font-size: 12px;")
        resume_grid.addWidget(lbl_max_resumes, 1, 0)

        self.spin_max_resumes = QSpinBox()
        self.spin_max_resumes.setRange(1, 5)
        self.spin_max_resumes.setValue(getattr(config, "max_resumes_per_prompt", 2))
        self.spin_max_resumes.setMinimumHeight(32)
        self.spin_max_resumes.setMaximumWidth(130)
        self.spin_max_resumes.setToolTip("Maximum consecutive auto-resumes per prompt before completing")
        resume_grid.addWidget(self.spin_max_resumes, 1, 1)

        quota_layout.addLayout(resume_grid)
        right_col.addWidget(quota_card)
        right_col.addStretch()

        cols_layout.addLayout(left_col, 1)
        cols_layout.addLayout(right_col, 1)

        scroll.setWidget(container)
        main_layout.addWidget(scroll, 1)

        # ─────────────────────────────────────────────────────────────
        # 4. PINNED BOTTOM ACTION & CONNECTION FOOTER BAR
        # ─────────────────────────────────────────────────────────────
        action_bar = QFrame()
        action_bar.setStyleSheet(CARD_STYLE)
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(14, 10, 14, 10)
        action_layout.setSpacing(10)

        # CDP status indicator
        self.lbl_cdp_status = QLabel("Detecting connection...")
        self.lbl_cdp_status.setStyleSheet("font-weight: 600; padding: 4px 10px; border-radius: 5px; font-size: 12px;")
        action_layout.addWidget(self.lbl_cdp_status)

        self.btn_refresh_cdp = QPushButton("🔄 Refresh")
        self.btn_refresh_cdp.setObjectName("btn-secondary")
        self.btn_refresh_cdp.setProperty("variant", "secondary")
        self.btn_refresh_cdp.setMinimumHeight(32)
        self.btn_refresh_cdp.setToolTip("Re-scan for active Google Antigravity Chrome DevTools Protocol port")
        self.btn_refresh_cdp.clicked.connect(self._test_cdp_connection)
        action_layout.addWidget(self.btn_refresh_cdp)

        self.btn_launch_cdp = QPushButton("🚀 Launch")
        self.btn_launch_cdp.setObjectName("btn-secondary")
        self.btn_launch_cdp.setProperty("variant", "secondary")
        self.btn_launch_cdp.setMinimumHeight(32)
        self.btn_launch_cdp.setToolTip("Launch Antigravity executable with remote debugging enabled")
        self.btn_launch_cdp.clicked.connect(self._launch_antigravity)
        action_layout.addWidget(self.btn_launch_cdp)

        action_layout.addStretch()

        self.btn_save = QPushButton("💾 Save Settings")
        self.btn_save.setObjectName("btn-primary")
        self.btn_save.setProperty("variant", "primary")
        self.btn_save.setMinimumHeight(32)
        self.btn_save.setMinimumWidth(150)
        self.btn_save.clicked.connect(self._save_settings)
        action_layout.addWidget(self.btn_save)

        main_layout.addWidget(action_bar)

        self._test_cdp_connection()

    def _create_divider(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setFrameShadow(QFrame.Shadow.Plain)
        div.setStyleSheet(f"background-color: {COLOR_BORDER_SUBTLE}; max-height: 1px; border: none; margin: 4px 0;")
        return div

    def _create_toggle_row(self, title: str, description: str, toggle: ToggleSwitch) -> QWidget:
        row_w = QWidget()
        row_l = QHBoxLayout(row_w)
        row_l.setContentsMargins(0, 3, 0, 3)
        row_l.setSpacing(12)

        text_l = QVBoxLayout()
        text_l.setContentsMargins(0, 0, 0, 0)
        text_l.setSpacing(2)

        lbl_title = QLabel(title)
        lbl_title.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 12px; font-weight: 600;")
        text_l.addWidget(lbl_title)

        if description:
            lbl_desc = QLabel(description)
            lbl_desc.setStyleSheet(f"color: {COLOR_TEXT_MUTED}; font-size: 11px;")
            lbl_desc.setWordWrap(True)
            text_l.addWidget(lbl_desc)

        row_l.addLayout(text_l, 1)
        row_l.addWidget(toggle, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return row_w

    def _test_cdp_connection(self):
        from cdp.client import auto_discover_antigravity_port, verify_cdp_port, is_port_listening
        discovered = auto_discover_antigravity_port()
        if discovered:
            config.cdp_port = discovered
            self.lbl_cdp_status.setText(f"🟢 Connected (Port {discovered})")
            self.lbl_cdp_status.setStyleSheet("background-color: #064e3b; color: #34d399; font-weight: 600; padding: 4px 10px; border-radius: 5px;")
            return

        port = getattr(config, "cdp_port", 9222)
        if is_port_listening(port) and verify_cdp_port(port):
            self.lbl_cdp_status.setText(f"🟢 Connected (Port {port})")
            self.lbl_cdp_status.setStyleSheet("background-color: #064e3b; color: #34d399; font-weight: 600; padding: 4px 10px; border-radius: 5px;")
        else:
            self.lbl_cdp_status.setText("🔴 Not Connected")
            self.lbl_cdp_status.setStyleSheet("background-color: #450a0a; color: #f87171; font-weight: 600; padding: 4px 10px; border-radius: 5px;")

    def _launch_antigravity(self):
        client = AntigravityCDPClient()
        ok = client.launch_antigravity_with_debugging(timeout=10.0)
        if ok:
            QMessageBox.information(self, "Launched", "Google Antigravity launched successfully with debugging.")
            self._test_cdp_connection()
        else:
            QMessageBox.warning(self, "Launch Note", "Could not start Antigravity automatically. Please launch via 'launch-antigravity.bat'.")
            self._test_cdp_connection()

    def _save_settings(self):
        config.min_gemini_five_hour_quota = self.spin_min_5h.value()
        config.min_gemini_weekly_quota = self.spin_min_week.value()
        config.low_quota_action = self.seg_quota_policy.current_id() or ("change_account" if self.rb_change_account.isChecked() else "wait_for_reset")
        config.auto_switch_on_low_quota = (config.low_quota_action == "change_account")
        config.completion_stabilization_seconds = 3
        config.emergency_hotkey = self.txt_emergency_hotkey.text().strip() or "F10"
        config.hud_hotkey = self.txt_hud_hotkey.text().strip() or "Ctrl+Shift+H"
        config.enable_auto_resume_same_chat = self.chk_auto_resume.isChecked()
        config.resume_mini_prompt = self.txt_resume_mini_prompt.text().strip() or "Please resume where you left off and finish the remaining work."
        config.max_resumes_per_prompt = self.spin_max_resumes.value()
        config.max_concurrent_projects = self.spin_max_concurrent.value()
        config.polling_interval_seconds = self.spin_poll_interval.value()
        config.enable_independent_project_resume = self.chk_independent_resume.isChecked()
        config.enable_foreground_hud = self.chk_foreground_hud.isChecked()
        config.enable_pre_switch_hud = self.chk_pre_switch_hud.isChecked()
        config.pre_switch_countdown_seconds = self.spin_pre_switch_seconds.value()
        config.quota_cache_seconds = self.spin_quota_cache_seconds.value()
        config.save()

        self.sig_settings_saved.emit()
        QMessageBox.information(self, "Settings Saved", "Preferences saved successfully!")
