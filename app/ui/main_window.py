import os
import sys
from PyQt6.QtWidgets import (
    QMainWindow,
    QTabWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QStatusBar,
    QLabel,
    QMessageBox,
    QSystemTrayIcon,
    QMenu,
    QApplication,
)
from PyQt6.QtCore import Qt, pyqtSlot, pyqtSignal, QEvent, QThread
from PyQt6.QtGui import QKeySequence, QShortcut, QIcon

from app.config import config
from app.models import TaskStatus, ExecutionStep
from app.worker import AutomationWorker
from app.hotkeys import hotkey_manager
from app.ui.styles import (
    DARK_THEME_QSS,
    COLOR_SURFACE_0,
    COLOR_SURFACE_2,
    COLOR_BORDER_DEFAULT,
    COLOR_BORDER_SUBTLE,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
    COLOR_PRIMARY_HOVER,
)
from app.ui.dashboard_tab import DashboardTab
from app.ui.queue_tab import QueueTab
from app.ui.chats_tab import ChatsTab
from app.ui.tokens_tab import TokensTab
from app.ui.accounts_tab import AccountsTab
from app.ui.settings_tab import SettingsTab
from app.ui.dialogs import ErrorResolutionDialog
from app.ui.floating_hud import get_mini_hud_icon
from app.ui.project_hud import ForegroundProjectHUD, AccountSwitchCountdownHUD
from app.ui.foreground_watcher import ForegroundWatcher
from app.account_switcher import AccountSwitchWorker
from app.account_manager import account_manager


DEFAULT_SAMPLE_PROMPTS = []


class MainWindow(QMainWindow):
    """Central Application Window for Antigravity Automation Desktop Studio."""

    # Thread-safe signals to marshal external hotkey calls to main GUI thread
    sig_emergency_stop_requested = pyqtSignal()
    sig_restore_window_requested = pyqtSignal()

    def __init__(self, app_icon: QIcon = None):
        super().__init__()
        self.app_icon = app_icon or QIcon()
        self.setWindowTitle("Studio — Antigravity Automation Runner")
        self.resize(1120, 740)
        self.setMinimumSize(900, 600)
        self.setStyleSheet(DARK_THEME_QSS)

        self.worker: AutomationWorker = None
        self.switch_worker: AccountSwitchWorker = None

        # Central widget and layout
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(12, 10, 12, 6)
        main_layout.setSpacing(8)

        # ─── Sleek Modern App Header Bar ───
        header_bar = QWidget(self)
        header_bar.setObjectName("app-header-bar")
        header_bar.setStyleSheet(
            f"QWidget#app-header-bar {{ background-color: {COLOR_SURFACE_0}; border: 1px solid {COLOR_BORDER_DEFAULT}; "
            f"border-radius: 8px; }}"
        )
        h_layout = QHBoxLayout(header_bar)
        h_layout.setContentsMargins(12, 6, 12, 6)
        h_layout.setSpacing(10)

        brand_icon = QLabel()
        if hasattr(self, "app_icon") and not self.app_icon.isNull():
            brand_icon.setPixmap(self.app_icon.pixmap(20, 20))
        else:
            brand_icon.setText("⚡")
        h_layout.addWidget(brand_icon)

        title_lbl = QLabel("Antigravity CDP Studio")
        title_lbl.setStyleSheet(f"font-weight: 700; font-size: 13px; color: {COLOR_TEXT_PRIMARY}; letter-spacing: 0.3px;")
        h_layout.addWidget(title_lbl)

        ver_badge = QLabel("v2.0")
        ver_badge.setStyleSheet(
            f"background-color: {COLOR_SURFACE_2}; color: {COLOR_PRIMARY_HOVER}; border: 1px solid {COLOR_BORDER_SUBTLE}; "
            f"border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: 700;"
        )
        h_layout.addWidget(ver_badge)

        h_layout.addStretch()

        self.lbl_port_chip = QLabel("CDP: Auto-Detect")
        self.lbl_port_chip.setStyleSheet(
            f"background-color: #0b221c; color: #34d399; border: 1px solid #065f46; "
            f"border-radius: 5px; padding: 3px 9px; font-size: 11px; font-weight: 600;"
        )
        h_layout.addWidget(self.lbl_port_chip)

        main_layout.addWidget(header_bar)

        # Tab Widget
        self.tabs = QTabWidget(self)
        self.dashboard_tab = DashboardTab(self)
        self.queue_tab = QueueTab(self)
        self.chats_tab = ChatsTab(self)
        self.tokens_tab = TokensTab(self)
        self.accounts_tab = AccountsTab(self)
        self.settings_tab = SettingsTab(self)

        self.tabs.addTab(self.dashboard_tab, "Dashboard")
        self.tabs.addTab(self.queue_tab, "Queue")
        self.tabs.addTab(self.chats_tab, "Chats")
        self.tabs.addTab(self.tokens_tab, "Tokens")
        self.tabs.addTab(self.accounts_tab, "Accounts")
        self.tabs.addTab(self.settings_tab, "Settings")

        self.queue_tab.sig_pause_project_requested.connect(self._on_pause_project_requested)
        self.queue_tab.sig_resume_project_requested.connect(self._on_resume_project_requested)
        self.queue_tab.sig_inspect_conversation_requested.connect(self._on_inspect_conversation_requested)
        self.chats_tab.sig_open_in_antigravity_requested.connect(self._on_open_chat_in_antigravity)
        self.chats_tab.sig_send_prompt_in_chat_requested.connect(self._on_send_prompt_in_chat)
        # Cross-tab conversation synchronization
        self.chats_tab.sig_conversation_selected.connect(self.tokens_tab.sync_conversation)
        self.tokens_tab.sig_conversation_selected.connect(self.chats_tab.sync_conversation)

        main_layout.addWidget(self.tabs)

        # Status Bar
        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)

        self.status_label = QLabel("● Ready")
        self.status_label.setStyleSheet("font-weight: 600; font-size: 12px;")
        self.status_bar.addWidget(self.status_label, 1)

        self.hotkey_status_lbl = QLabel(
            f"Stop: [{config.emergency_hotkey}]  |  HUD: [Ctrl+Shift+H]"
        )
        self.hotkey_status_lbl.setObjectName("lbl-badge")
        self.status_bar.addPermanentWidget(self.hotkey_status_lbl)

        # Foreground-Conditional Multi-Project Floating HUD
        self.hud = ForegroundProjectHUD()
        self.foreground_watcher = ForegroundWatcher(hud_hwnd=int(self.hud.winId()))
        self.foreground_watcher.sig_antigravity_focus_changed.connect(self._on_antigravity_focus_changed)

        self.hud.sig_pause_project_requested.connect(self._on_pause_project_requested)
        self.hud.sig_resume_project_requested.connect(self._on_resume_project_requested)
        self.hud.sig_pause_all_requested.connect(self.pause_automation)
        self.hud.sig_resume_all_requested.connect(self.resume_automation)
        self.hud.sig_stop_all_requested.connect(self.stop_automation)
        self.hud.sig_restore_studio_requested.connect(self.restore_and_bring_to_front)
        self.hud.sig_hud_hidden.connect(self._update_tray_menu_state)

        # Always-On-Top 30s Pre-Switch Countdown Mini HUD
        self.switch_countdown_hud = AccountSwitchCountdownHUD()
        self.switch_countdown_hud.sig_change_now_requested.connect(self._on_switch_countdown_change_now)

        # System Tray Icon (displaying the custom HUD mini logo)
        self._setup_tray_icon()

        # Connect tab signals
        self._connect_signals()

        # Populate initial queue
        self.queue_tab.load_initial_prompts(DEFAULT_SAMPLE_PROMPTS)
        self._update_progress_display()

        # Register Emergency & Restore Hotkeys
        self._setup_emergency_hotkeys()

        self.dashboard_tab.append_log("INFO", "Antigravity Automation Studio initialized.")
        self.dashboard_tab.append_log(
            "INFO", f"Loaded {len(self.queue_tab.prompts)} queued prompts. Target window: '{config.window_title}'."
        )

    def _setup_tray_icon(self):
        """Creates the Windows system tray icon displaying the crisp HUD mini SVG logo."""
        self.tray_icon = QSystemTrayIcon(self)
        mini_icon = get_mini_hud_icon(64)
        if not mini_icon.isNull():
            self.tray_icon.setIcon(mini_icon)
        elif hasattr(self, "app_icon") and not self.app_icon.isNull():
            self.tray_icon.setIcon(self.app_icon)
        else:
            self.tray_icon.setIcon(self.windowIcon())

        self.tray_icon.setToolTip("Antigravity Automation Studio")

        self.tray_menu = QMenu()

        act_open = self.tray_menu.addAction("🖥  Open Studio Window")
        act_open.triggered.connect(self.restore_and_bring_to_front)

        # Foreground HUD Toggle (Show / Hide Live HUD)
        self.act_tray_toggle_hud = self.tray_menu.addAction("⚡  Show Live HUD")
        self.act_tray_toggle_hud.triggered.connect(self._toggle_hud_visibility)

        self.tray_menu.addSeparator()

        # Dynamic Single Toggle: "⏸ Pause Automation" <-> "▶ Resume Automation"
        self.act_tray_pause_resume = self.tray_menu.addAction("⏸  Pause Automation")
        self.act_tray_pause_resume.triggered.connect(self._toggle_tray_pause_resume)

        self.act_tray_skip = self.tray_menu.addAction("⏭  Skip Current Prompt")
        self.act_tray_skip.triggered.connect(self.skip_current_prompt)

        self.act_tray_stop = self.tray_menu.addAction("⏹  Stop Automation")
        self.act_tray_stop.triggered.connect(self.stop_automation)

        self.tray_menu.addSeparator()

        act_exit = self.tray_menu.addAction("🚪  Exit Application")
        act_exit.triggered.connect(lambda: self._stop_and_exit(None))

        self.tray_menu.aboutToShow.connect(self._update_tray_menu_state)
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()
        self._update_tray_menu_state()

    def _toggle_hud_visibility(self):
        """Toggles the floating HUD on or off from the system tray."""
        if self.hud.isVisible():
            self.hud.hide_hud()
        else:
            self.hud.show_hud()
        self._update_tray_menu_state()

    def _on_antigravity_focus_changed(self, is_foreground: bool):
        """
        Called by ForegroundWatcher when Antigravity gains or loses active window focus.
        Displays HUD only when Antigravity is in foreground and automation is running (if foreground-only is enabled),
        or always when automation is running (if foreground-only is disabled).
        """
        is_running = bool(self.worker and self.worker.isRunning())
        fg_only = getattr(config, "enable_foreground_hud", True)

        if is_running and not self.hud.is_manually_hidden:
            if not fg_only or is_foreground:
                self.hud.show()
                self.hud.raise_()
            else:
                self.hud.hide()
        else:
            self.hud.hide()
        self._update_tray_menu_state()

    def _toggle_tray_pause_resume(self):
        """Toggles between pause and resume from system tray."""
        if self.worker and self.worker.isRunning():
            if getattr(self.worker, "is_paused", False):
                self.resume_automation()
            else:
                self.pause_automation()

    def _update_tray_menu_state(self, state: str = None):
        """Updates the tray menu actions so only the relevant Pause or Resume toggle is displayed."""
        if hasattr(self, "act_tray_toggle_hud") and self.act_tray_toggle_hud:
            if self.hud.isVisible():
                self.act_tray_toggle_hud.setText("⚡  Hide Live HUD")
            else:
                self.act_tray_toggle_hud.setText("⚡  Show Live HUD")

        if not hasattr(self, "act_tray_pause_resume") or not self.act_tray_pause_resume:
            return

        is_running = bool(self.worker and self.worker.isRunning())
        is_paused = bool(is_running and getattr(self.worker, "is_paused", False))

        if state == "PAUSED" or is_paused:
            self.act_tray_pause_resume.setText("▶  Resume Automation")
            self.act_tray_pause_resume.setEnabled(True)
            if hasattr(self, "act_tray_skip"):
                self.act_tray_skip.setEnabled(True)
            if hasattr(self, "act_tray_stop"):
                self.act_tray_stop.setEnabled(True)
        elif is_running or state == "RUNNING":
            self.act_tray_pause_resume.setText("⏸  Pause Automation")
            self.act_tray_pause_resume.setEnabled(True)
            if hasattr(self, "act_tray_skip"):
                self.act_tray_skip.setEnabled(True)
            if hasattr(self, "act_tray_stop"):
                self.act_tray_stop.setEnabled(True)
        else:
            self.act_tray_pause_resume.setText("⏸  Pause Automation")
            self.act_tray_pause_resume.setEnabled(False)
            if hasattr(self, "act_tray_skip"):
                self.act_tray_skip.setEnabled(False)
            if hasattr(self, "act_tray_stop"):
                self.act_tray_stop.setEnabled(False)

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.restore_and_bring_to_front()

    def changeEvent(self, event):
        super().changeEvent(event)

    @pyqtSlot()
    def restore_and_bring_to_front(self):
        """Restores the main studio window to the front."""
        self.show()
        self.setWindowState(
            self.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive
        )
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if sys.platform == "win32":
            try:
                import ctypes
                hwnd = int(self.winId())
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # 9 = SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                # AttachThreadInput bypass for Windows foreground lock
                cur_thread = ctypes.windll.kernel32.GetCurrentThreadId()
                fore_hwnd = ctypes.windll.user32.GetForegroundWindow()
                fore_thread = ctypes.windll.user32.GetWindowThreadProcessId(fore_hwnd, None)
                if fore_thread and fore_thread != cur_thread:
                    ctypes.windll.user32.AttachThreadInput(cur_thread, fore_thread, True)
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
                    ctypes.windll.user32.AttachThreadInput(cur_thread, fore_thread, False)
            except Exception:
                pass


    def _restore_main_window(self):
        self.restore_and_bring_to_front()

    def _connect_signals(self):
        # Dashboard controls
        self.dashboard_tab.sig_start_requested.connect(self.start_automation)
        self.dashboard_tab.sig_pause_requested.connect(self.pause_automation)
        self.dashboard_tab.sig_resume_requested.connect(self.resume_automation)
        self.dashboard_tab.sig_skip_requested.connect(self.skip_current_prompt)
        self.dashboard_tab.sig_stop_requested.connect(self.stop_automation)

        # Settings
        self.settings_tab.sig_settings_saved.connect(self._on_settings_saved)

        # Queue changes & sub-queue controls
        self.queue_tab.sig_queue_changed.connect(self._on_queue_modified)

        # Accounts tab controls
        self.accounts_tab.sig_run_switch.connect(self.run_account_switch_test)
        self.accounts_tab.sig_cancel_switch.connect(self.cancel_account_switch)
        self.accounts_tab.sig_check_quota.connect(self.check_live_quota)
        self.dashboard_tab.sig_change_account_requested.connect(lambda: self.run_account_switch_test())
        self.settings_tab.sig_change_account_requested.connect(lambda: self.run_account_switch_test())
        self.dashboard_tab.sig_check_quota_requested.connect(self.check_live_quota)
        self.dashboard_tab.sig_quota_log.connect(self.accounts_tab.append_log)
        self.dashboard_tab.sig_quota_checked.connect(self._on_dashboard_quota_checked)

    @pyqtSlot()
    def start_automation(self):
        """Starts background automation worker."""
        if (self.worker and self.worker.isRunning()) or (self.switch_worker and self.switch_worker.isRunning()):
            self.dashboard_tab.append_log("WARN", "Automation or account switch is already active.")
            return

        if not self.queue_tab.prompts:
            QMessageBox.warning(self, "Empty Queue", "Please add at least one prompt to the queue.")
            self.tabs.setCurrentIndex(1)  # Switch to queue tab
            return

        # Check queue status
        completed_items = [
            p for p in self.queue_tab.prompts if p.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)
        ]
        pending_items = [
            p for p in self.queue_tab.prompts if p.status in (TaskStatus.PENDING, TaskStatus.FAILED)
        ]
        if not pending_items:
            reply = QMessageBox.question(
                self,
                "All Prompts Completed",
                "All prompts in the queue are already marked as Completed or Skipped.\n\n"
                "Would you like to reset all prompts to Pending and run strictly from the beginning (Prompt #1)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.queue_tab._reset_statuses()
            else:
                return
        elif completed_items:
            reply = QMessageBox.question(
                self,
                "Resume or Restart Queue?",
                f"The queue currently has {len(completed_items)} completed and {len(pending_items)} pending prompt(s).\n\n"
                "Would you like to restart from the beginning (Prompt #1)?\n\n"
                "• Click 'Yes' to reset all prompts to Pending and start strictly from Prompt #1.\n"
                "• Click 'No' to resume execution from the first pending prompt.\n"
                "• Click 'Cancel' to abort.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.queue_tab._reset_statuses()
            elif reply == QMessageBox.StandardButton.Cancel:
                return

        self.dashboard_tab.update_quota_banner_status()

        # Switch to dashboard tab to watch live progress
        self.tabs.setCurrentIndex(0)

        # Minimize main window so Antigravity takes focus cleanly (clean screen, no HUD)
        self.showMinimized()

        # Instantiate Worker with automatic targeting (target_hwnd=0 resolves white logo window)
        self.worker = AutomationWorker(
            queue=self.queue_tab.prompts,
            target_hwnd=0,
            is_manual_mode=False,
            countdown_seconds=0,
        )

        # Wire worker signals to dashboard tab
        self.worker.sig_step_changed.connect(self._on_worker_step_changed)
        self.worker.sig_prompt_started.connect(self._on_worker_prompt_started)
        self.worker.sig_prompt_progress.connect(self._on_worker_prompt_progress)
        self.worker.sig_prompt_finished.connect(self._on_worker_prompt_finished)
        self.worker.sig_queue_finished.connect(self._on_worker_queue_finished)
        self.worker.sig_log.connect(self.dashboard_tab.append_log)
        self.worker.sig_state_changed.connect(self._on_worker_state_changed)
        self.worker.sig_error_prompt.connect(self._on_worker_error_prompt)
        self.worker.sig_quota_updated.connect(self._on_dashboard_quota_checked)
        self.worker.sig_quota_updated.connect(self.dashboard_tab.update_quota_display)
        self.worker.sig_active_projects_updated.connect(self.queue_tab.update_active_projects_hud)
        self.worker.sig_active_projects_updated.connect(self.chats_tab.update_worker_active_projects)
        self.worker.sig_active_projects_updated.connect(self.tokens_tab.update_worker_active_projects)
        self.worker.sig_project_state_changed.connect(self.queue_tab.on_project_state_changed)
        self.worker.sig_subqueue_state_changed.connect(self.queue_tab.on_project_state_changed)
        self.worker.sig_startup_active_agents_detected.connect(self._on_startup_active_agents_detected)

        # Wire worker signals to Foreground HUD & Pre-Switch Countdown Mini HUD
        self.worker.sig_active_projects_updated.connect(self.hud.update_projects)
        self.worker.sig_state_changed.connect(self.hud.set_automation_state)
        self.worker.sig_pre_switch_countdown_started.connect(self.switch_countdown_hud.start_countdown)
        self.worker.sig_pre_switch_countdown_tick.connect(self.switch_countdown_hud.update_countdown)
        self.worker.sig_pre_switch_countdown_finished.connect(self.switch_countdown_hud.stop_and_hide)
        self.hud.is_manually_hidden = False
        self.hud.set_automation_state("RUNNING")
        self.foreground_watcher.start()

        self.worker.start()
        self.status_label.setText("Automation active...")
        self._update_tray_menu_state("RUNNING")

    @pyqtSlot()
    def _on_switch_countdown_change_now(self):
        """Called when the user clicks '⚡ Change Now' on the 30s Pre-Switch Mini HUD."""
        if self.worker and self.worker.isRunning():
            self.worker.trigger_switch_now()
        if self.switch_worker and self.switch_worker.isRunning():
            self.switch_worker.trigger_switch_now()

    @pyqtSlot(list, int)
    def _on_startup_active_agents_detected(self, active_agents: list, queue_count: int):
        """Displays interactive modal dialog when active agents are detected running in target project at startup."""
        from app.ui.dialogs import ActiveAgentsStartupDialog
        self.restore_and_bring_to_front()
        projects = list(dict.fromkeys(r.get("project") for r in active_agents if r.get("project")))
        proj_name = ", ".join(projects) if projects else "Target Project"
        dlg = ActiveAgentsStartupDialog(
            project_name=proj_name,
            running_agents=active_agents,
            queue_count=queue_count,
            parent=self,
        )
        dlg.exec()
        action = dlg.get_selected_action()  # "wait" or "proceed"
        if self.worker:
            self.worker.resolve_startup_decision(action)

    @pyqtSlot()
    def pause_automation(self):
        if self.worker and self.worker.isRunning():
            self.worker.pause()
            self.status_label.setText("Automation paused")
            self._update_tray_menu_state("PAUSED")

    @pyqtSlot()
    def resume_automation(self):
        if self.worker and self.worker.isRunning():
            self.worker.resume()
            self.status_label.setText("Automation running...")
            self._update_tray_menu_state("RUNNING")

    @pyqtSlot()
    def stop_automation(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.status_label.setText("Stopping automation...")
            self._update_tray_menu_state("STOPPED")

    @pyqtSlot()
    def skip_current_prompt(self):
        if self.worker and self.worker.isRunning():
            self.worker.skip_current()
            self.status_label.setText("Overriding prompt -> Marked COMPLETED & advancing to next...")
            self._update_tray_menu_state("RUNNING")

    @pyqtSlot()
    def emergency_stop(self):
        """Triggered by global hotkey or failsafe."""
        self.dashboard_tab.append_log("ERROR", f"EMERGENCY KILL [{config.emergency_hotkey}] ACTIVATED!")
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.status_label.setText("EMERGENCY STOP TRIGGERED")
            self._update_tray_menu_state("STOPPED")

    # Worker signal handlers
    def _on_worker_step_changed(self, step_value: str, description: str):
        self.dashboard_tab.set_execution_step(step_value, description)
        self.status_label.setText(description)

    def _on_worker_prompt_started(self, prompt_id: str, index: int, total: int):
        self.queue_tab.update_prompt_status(prompt_id, TaskStatus.RUNNING, row_idx=index)
        self._update_progress_display()
        self._update_tray_menu_state("RUNNING")

    def _on_worker_prompt_progress(self, prompt_id: str, index: int, elapsed: float):
        self.dashboard_tab.update_timer(elapsed)
        self.queue_tab.update_prompt_progress(prompt_id, elapsed, row_idx=index)

    def _on_worker_prompt_finished(self, prompt_id: str, index: int, status_val: str, error_msg: str):
        try:
            status = TaskStatus(status_val)
        except ValueError:
            status = TaskStatus.COMPLETED
        self.queue_tab.update_prompt_status(prompt_id, status, error_msg=error_msg, row_idx=index)
        self._update_progress_display()

    def _on_worker_queue_finished(self, cleanly: bool):
        self.dashboard_tab.set_running_state("IDLE")
        self._update_tray_menu_state("IDLE")
        self.status_label.setText("Queue finished" if cleanly else "Queue stopped")
        self._update_progress_display()
        self.queue_tab.update_active_projects_hud({})
        self.chats_tab.update_worker_active_projects({})
        self.tokens_tab.update_worker_active_projects({})
        self.foreground_watcher.stop()
        self.hud.hide()
        self.switch_countdown_hud.stop_and_hide()
        self.hud.set_automation_state("IDLE")
        self.hud.update_projects({})
        self._restore_main_window()

    @pyqtSlot(str)
    def _on_pause_project_requested(self, project_name: str):
        if self.worker and self.worker.isRunning():
            self.worker.pause_project(project_name)

    @pyqtSlot(str)
    def _on_resume_project_requested(self, project_name: str):
        if self.worker and self.worker.isRunning():
            self.worker.resume_project(project_name)

    @pyqtSlot(str)
    def _on_inspect_conversation_requested(self, conversation_id: str):
        """Switches to the Chats tab and loads the requested conversation ID."""
        if not conversation_id:
            return
        self.tabs.setCurrentWidget(self.chats_tab)
        self.chats_tab.inspect_conversation(conversation_id)

    @pyqtSlot(str)
    def _on_open_chat_in_antigravity(self, conversation_id: str):
        """Switches Antigravity IDE directly to the specified conversation in a background thread."""
        if not conversation_id:
            return
        if hasattr(self, "_open_chat_thread") and self._open_chat_thread and self._open_chat_thread.isRunning():
            return
        self.status_label.setText(f"Switching Antigravity to chat {conversation_id[:8]}...")
        self.dashboard_tab.append_log("INFO", f"[ChatInspector] Switching Antigravity to chat {conversation_id}...")

        # Immediately minimize automation window so it disappears from foreground and reveals Antigravity
        self.showMinimized()

        class _OpenChatThread(QThread):
            sig_done = pyqtSignal(bool, str)

            def __init__(self, cid: str, parent=None):
                from PyQt6.QtCore import QObject
                real_parent = parent if (isinstance(parent, QObject) and type(parent).__name__ != "MagicMock") else None
                super().__init__(real_parent)
                self.cid = cid

            def run(self):
                client = None
                try:
                    from cdp.client import AntigravityCDPClient
                    from cdp.dom_controller import AntigravityDOMController
                    client = AntigravityCDPClient()
                    ok_conn, msg = client.connect()
                    if not ok_conn:
                        self.sig_done.emit(False, f"CDP connection failed: {msg}")
                        return
                    page = client.get_page()
                    dom = AntigravityDOMController(page)
                    ok = dom.switch_to_conversation(self.cid)
                    if ok:
                        try:
                            page.bring_to_front()
                        except Exception:
                            pass
                    self.sig_done.emit(bool(ok), "" if ok else "Could not switch to conversation.")
                except Exception as e:
                    self.sig_done.emit(False, str(e))
                finally:
                    if client:
                        try:
                            client.disconnect()
                        except Exception:
                            pass

        self._open_chat_thread = _OpenChatThread(conversation_id, self)

        def _finished(ok: bool, err: str):
            if ok:
                self.status_label.setText(f"Opened chat {conversation_id[:8]} in Antigravity")
                self.dashboard_tab.append_log("SUCCESS", f"[ChatInspector] Switched to chat {conversation_id[:8]}.")
                self.showMinimized()
            else:
                self.status_label.setText(f"Could not switch to chat {conversation_id[:8]}")
                self.dashboard_tab.append_log("WARN", f"[ChatInspector] Could not switch to chat {conversation_id[:8]}: {err}")
                self.restore_and_bring_to_front()

        self._open_chat_thread.sig_done.connect(_finished)
        self._open_chat_thread.start()

    @pyqtSlot(str, str)
    def _on_send_prompt_in_chat(self, conversation_id: str, prompt_text: str):
        """Switches to the specified conversation and injects a follow-up or resume prompt."""
        if not conversation_id or not prompt_text.strip():
            return
        if hasattr(self, "_send_in_chat_thread") and self._send_in_chat_thread and self._send_in_chat_thread.isRunning():
            return
        self.status_label.setText(f"Sending prompt in chat {conversation_id[:8]}...")
        self.dashboard_tab.append_log(
            "INFO", f"[ChatInspector] Sending prompt in chat {conversation_id[:8]}: '{prompt_text[:60]}'"
        )

        class _SendInChatThread(QThread):
            sig_done = pyqtSignal(bool, str)

            def __init__(self, cid: str, text: str, parent=None):
                from PyQt6.QtCore import QObject
                real_parent = parent if (isinstance(parent, QObject) and type(parent).__name__ != "MagicMock") else None
                super().__init__(real_parent)
                self.cid = cid
                self.text = text

            def run(self):
                client = None
                try:
                    from cdp.client import AntigravityCDPClient
                    from cdp.dom_controller import AntigravityDOMController
                    client = AntigravityCDPClient()
                    ok_conn, msg = client.connect()
                    if not ok_conn:
                        self.sig_done.emit(False, f"CDP connection failed: {msg}")
                        return
                    page = client.get_page()
                    dom = AntigravityDOMController(page)
                    if dom.is_conversation_generating(conversation_id=self.cid):
                        self.sig_done.emit(
                            False,
                            "An agent is currently generating in this conversation. Wait for it to finish before sending a follow-up prompt.",
                        )
                        return
                    dom.switch_to_conversation(self.cid)
                    ok = dom.inject_and_submit_prompt(self.text)
                    if ok:
                        try:
                            page.bring_to_front()
                        except Exception:
                            pass
                    self.sig_done.emit(bool(ok), "" if ok else "Failed to inject or submit prompt into chat box.")
                except Exception as e:
                    self.sig_done.emit(False, str(e))
                finally:
                    if client:
                        try:
                            client.disconnect()
                        except Exception:
                            pass

        self._send_in_chat_thread = _SendInChatThread(conversation_id, prompt_text.strip(), self)

        def _finished(ok: bool, err: str):
            if ok:
                self.status_label.setText(f"Prompt submitted in chat {conversation_id[:8]}")
                self.dashboard_tab.append_log(
                    "SUCCESS", f"[ChatInspector] Submitted prompt in chat {conversation_id[:8]}."
                )
                self.chats_tab.inspect_conversation(conversation_id, preserve_selection=True)
                self.chats_tab.refresh_active_chats()
                self.tokens_tab.refresh_recent_conversations()
            else:
                self.status_label.setText("Follow-up prompt not sent")
                self.dashboard_tab.append_log("WARN", f"[ChatInspector] Follow-up prompt failed: {err}")
                QMessageBox.warning(self, "Could Not Send Prompt in Chat", err)

        self._send_in_chat_thread.sig_done.connect(_finished)
        self._send_in_chat_thread.start()

    def _on_worker_state_changed(self, state: str):
        self.dashboard_tab.set_running_state(state)
        self._update_tray_menu_state(state)

    def _on_worker_error_prompt(self, prompt_id: str, title: str, error_detail: str, screenshot_path: str):
        """Displays modal ErrorResolutionDialog to give user Retry/Skip/Abort options."""
        dlg = ErrorResolutionDialog(
            parent=self,
            prompt_id=prompt_id,
            title=title,
            error_detail=error_detail,
            screenshot_path=screenshot_path,
        )
        dlg.exec()
        action = dlg.selected_action
        if self.worker:
            self.worker.resolve_error(action)

        # Setup emergency hotkeys
        self._setup_emergency_hotkeys()

    def _setup_emergency_hotkeys(self):
        # 1. Thread-safe Qt signal connections to marshal global background threads to main GUI thread
        try:
            self.sig_emergency_stop_requested.disconnect()
        except Exception:
            pass
        try:
            self.sig_restore_window_requested.disconnect()
        except Exception:
            pass
        self.sig_emergency_stop_requested.connect(self.emergency_stop, Qt.ConnectionType.QueuedConnection)
        self.sig_restore_window_requested.connect(self.restore_and_bring_to_front, Qt.ConnectionType.QueuedConnection)

        # 2. Global hotkeys across Windows (emits thread-safe Qt signals)
        hotkey_manager.register(config.emergency_hotkey, self.sig_emergency_stop_requested.emit, name="emergency")
        hud_key = getattr(config, "hud_hotkey", "Ctrl+Shift+H") or "Ctrl+Shift+H"
        hotkey_manager.register(hud_key.lower(), self.sig_restore_window_requested.emit, name="restore")

        # 3. In-app Qt shortcuts (reuse existing QShortcut instances if already created)
        if getattr(self, "qt_shortcut", None) is None:
            self.qt_shortcut = QShortcut(QKeySequence(config.emergency_hotkey), self)
            self.qt_shortcut.activated.connect(self.emergency_stop)
        else:
            self.qt_shortcut.setKey(QKeySequence(config.emergency_hotkey))

        if getattr(self, "restore_shortcut", None) is None:
            self.restore_shortcut = QShortcut(QKeySequence(hud_key), self)
            self.restore_shortcut.activated.connect(self.restore_and_bring_to_front)
        else:
            self.restore_shortcut.setKey(QKeySequence(hud_key))

        # Secondary emergency key (Escape)
        if getattr(self, "esc_shortcut", None) is None:
            self.esc_shortcut = QShortcut(QKeySequence("Escape"), self)
            self.esc_shortcut.activated.connect(self._on_esc_pressed)

    def _on_esc_pressed(self):
        if self.worker and self.worker.isRunning():
            self.emergency_stop()

    def _on_settings_saved(self):
        self.hotkey_status_lbl.setText(
            f"Stop: [{config.emergency_hotkey}]  |  HUD: [Ctrl+Shift+H]"
        )
        if hasattr(self, "lbl_port_chip"):
            self.lbl_port_chip.setText("CDP: Auto-Detect")
        self._setup_emergency_hotkeys()
        self.dashboard_tab.update_timeout_display()
        self.dashboard_tab.update_quota_banner_status()
        self.dashboard_tab.append_log("INFO", f"Settings updated (Target Window: '{config.window_title}').")
        if self.worker and self.worker.isRunning():
            self.worker.notify_settings_changed()

    def _on_queue_modified(self):
        """Called whenever prompts are added, edited, reordered, or removed."""
        self._update_progress_display()
        if self.worker and self.worker.isRunning():
            self.worker.notify_queue_changed(self.queue_tab.prompts)

    def _update_progress_display(self):
        total = len(self.queue_tab.prompts)
        completed = sum(
            1 for p in self.queue_tab.prompts if p.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)
        )
        self.dashboard_tab.update_queue_progress(completed, total)

    # ─────────────────────────────────────────────────────────────────────────
    # DEDICATED ACCOUNT SWITCHER TEST RUNNER SLOTS
    # ─────────────────────────────────────────────────────────────────────────

    @pyqtSlot(str)
    def run_account_switch_test(self, target_account_email: str = ""):
        """Executes the 11-step account switch automation via dedicated test button."""
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(
                self,
                "Prompt Automation Active",
                "Cannot run account switch test while prompt queue automation is active.\n\n"
                "Please pause or stop prompt processing first.",
            )
            self.accounts_tab.set_switch_running_state(False)
            return

        if self.switch_worker and self.switch_worker.isRunning():
            QMessageBox.information(
                self,
                "Switch In Progress",
                "An account switch automation workflow is already in progress.",
            )
            return

        self.accounts_tab.set_switch_running_state(True)
        self.status_label.setText("Change Google Account automation running (10 steps)...")
        self.dashboard_tab.append_log("INFO", "Initiating 'Change Google Account' pipeline (10 steps)...")

        self.switch_worker = AccountSwitchWorker(
            target_hwnd=0,
            target_account_email=target_account_email,
            is_manual_mode=False,
            countdown_seconds=0,
            force_switch=True,
            parent=self,
        )
        self.switch_worker.sig_step_changed.connect(self._on_switch_step_changed)
        self.switch_worker.sig_progress.connect(self.accounts_tab.set_progress)
        self.switch_worker.sig_log.connect(self._on_switch_log)
        self.switch_worker.sig_quota_updated.connect(self._on_dashboard_quota_checked)
        self.switch_worker.sig_quota_updated.connect(self.dashboard_tab.update_quota_display)
        self.switch_worker.sig_pre_switch_countdown_started.connect(self.switch_countdown_hud.start_countdown)
        self.switch_worker.sig_pre_switch_countdown_tick.connect(self.switch_countdown_hud.update_countdown)
        self.switch_worker.sig_pre_switch_countdown_finished.connect(self.switch_countdown_hud.stop_and_hide)
        self.switch_worker.sig_finished.connect(self._on_switch_finished)
        self.switch_worker.start()

    @pyqtSlot()
    def cancel_account_switch(self):
        cancelled = False
        if self.switch_worker and self.switch_worker.isRunning():
            self.switch_worker.cancel()
            cancelled = True

        self.switch_countdown_hud.stop_and_hide()
        if cancelled:
            self.status_label.setText("Cancelling account switch...")
            self.accounts_tab.append_log("WARN", "Account switch cancellation requested...")

    @pyqtSlot()
    def check_live_quota(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(
                self,
                "Prompt Automation Active",
                "Cannot inspect quota limits while queue automation is active.\n\n"
                "Please pause or stop prompt processing first.",
            )
            return

        self.status_label.setText("Checking live Gemini quota on Antigravity...")
        self.accounts_tab.append_log("INFO", "Starting live Gemini quota check...")
        self.dashboard_tab.run_quota_inspection(
            target_hwnd=0,
            is_manual_mode=False,
            countdown_seconds=0,
        )

    def _on_dashboard_quota_checked(self, res: dict):
        self.status_label.setText("Ready")
        gem = res.get("gemini", {})
        five = gem.get("five_hour_percent")
        five_ref = gem.get("five_hour_refresh", "")
        week = gem.get("weekly_percent")
        week_ref = gem.get("weekly_refresh", "")

        # 1. Authoritatively resolve detected active account from DOM
        detected_email = (res.get("current_email") or "").strip().lower()
        if not detected_email:
            raw_ui = (self.accounts_tab.lbl_active_email.text() or "").strip()
            clean_ui = raw_ui.split("(")[0].strip().lower()
            if clean_ui and clean_ui not in ("none", "unknown", "—"):
                detected_email = clean_ui
            elif account_manager.current_email:
                detected_email = account_manager.current_email.split("(")[0].strip().lower()

        if not detected_email and account_manager.accounts:
            active_acc = next((a for a in account_manager.accounts if a.quota_status == "Active"), None)
            detected_email = (active_acc.email if active_acc else account_manager.accounts[0].email).strip().lower()

        if detected_email:
            account_manager.set_active_account(detected_email)
            if not account_manager.is_registered_account(detected_email):
                next_acc = account_manager.get_next_account(detected_email)
                next_disp = next_acc.email if next_acc else "first ready account"
                self.accounts_tab.append_log(
                    "WARN",
                    f"⚠️ Outside account detected: '{detected_email}' (not in queue pool). "
                    f"Next in queue is '{next_disp}'. Click 'Switch Account' to log into queue.",
                )

        if five is not None or week is not None:
            five_str = f"{five}%" if five is not None else "N/A"
            week_str = f"{week}%" if week is not None else "N/A"
            self.accounts_tab.append_log("INFO", f"Gemini Quotas: 5-Hour: {five_str} ({five_ref}), Weekly: {week_str} ({week_ref})")
            if detected_email:
                chk_reason = res.get("check_reason") or "Manual on-demand check"
                account_manager.update_account_quota(
                    email=detected_email,
                    five_hour_percent=five,
                    five_hour_refresh_raw=five_ref,
                    weekly_percent=week,
                    weekly_refresh_raw=week_ref,
                    reason=chk_reason,
                )

        if res.get("is_safe_to_prompt") is False:
            reason = res.get("safety_reason", "Quota limit is below safety threshold.")
            self.accounts_tab.append_log("WARN", f"⚠️ Quota Alert: {reason}")
        elif res.get("error"):
            self.accounts_tab.append_log("ERROR", f"Quota check error: {res.get('error')}")
        else:
            self.accounts_tab.append_log("SUCCESS", "Gemini quota inspection completed: within safe thresholds.")

        self.accounts_tab.reload_accounts()

    def _on_switch_step_changed(self, step_title: str, description: str):
        self.accounts_tab.set_step(step_title, description)
        self.status_label.setText(f"{step_title}: {description}")

    def _on_switch_log(self, level: str, message: str):
        self.accounts_tab.append_log(level, message)
        self.dashboard_tab.append_log(level, f"[AccountSwitch] {message}")

    def _on_switch_finished(self, success: bool, switched_email: str, error_detail: str):
        self.switch_countdown_hud.stop_and_hide()
        self.accounts_tab.set_switch_running_state(False)
        self.accounts_tab.reload_accounts()

        if success:
            if switched_email:
                self.status_label.setText(f"Switched account to {switched_email}")
                self.dashboard_tab.append_log("SUCCESS", f"Google Account switch finished successfully: '{switched_email}'")
                QMessageBox.information(
                    self,
                    "Account Switch Complete",
                    f"Successfully switched to Google account:\n\n{switched_email}\n\nAntigravity is authenticated and ready to resume.",
                )
            else:
                self.status_label.setText("Quota limit pass — No switch needed")
                self.dashboard_tab.append_log("SUCCESS", "Quota limit pass — Current account quota is healthy. No account switch needed.")
                QMessageBox.information(
                    self,
                    "Quota Limit Pass — No Switch Needed",
                    "Your current Gemini quota is within safe limits!\n\nNo account switch was required.",
                )
        else:
            self.status_label.setText("Account switch failed / stopped")
            self.dashboard_tab.append_log("ERROR", f"Google Account switch did not complete: {error_detail}")
            QMessageBox.warning(
                self,
                "Account Switch Incomplete",
                f"The automated account switch encountered an issue:\n\n{error_detail}\n\n"
                "Please check the live logs in the Google Accounts tab for diagnostic details.",
            )

    def closeEvent(self, event):
        """Ensure background workers are cleanly cancelled upon window close or kept running in tray."""
        active = (
            (self.worker and self.worker.isRunning())
            or (self.switch_worker and self.switch_worker.isRunning())
        )
        if active:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Automation in Progress")
            msg_box.setText("Automation is currently active in the background.")
            msg_box.setInformativeText("What would you like to do?")

            btn_tray = msg_box.addButton("Keep Running in Tray", QMessageBox.ButtonRole.AcceptRole)
            btn_exit = msg_box.addButton("Stop & Exit", QMessageBox.ButtonRole.DestructiveRole)
            btn_cancel = msg_box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            msg_box.setDefaultButton(btn_tray)

            msg_box.exec()
            clicked = msg_box.clickedButton()

            if clicked == btn_cancel:
                event.ignore()
                return
            elif clicked == btn_tray:
                event.ignore()
                self.hide()
                if hasattr(self, "tray_icon") and self.tray_icon and self.tray_icon.isVisible():
                    self.tray_icon.showMessage(
                        "Antigravity Automation Studio",
                        "Automation is running in the background.\nClick this tray icon or press Ctrl+Shift+H anytime to restore.",
                        QSystemTrayIcon.MessageIcon.Information,
                        3000,
                    )
                return
            elif clicked == btn_exit:
                self._stop_and_exit(event)
                return

        self._stop_and_exit(event)

    def _stop_and_exit(self, event=None):
        # Automatically mark any currently Running prompts as Interrupted/Failed and persist to queue.json
        if hasattr(self, "queue_tab") and self.queue_tab:
            try:
                self.queue_tab.mark_running_as_interrupted()
            except Exception as e:
                print(f"[WARN] Failed to mark running prompts as interrupted on exit: {e}")

        try:
            if self.worker and self.worker.isRunning():
                self.worker.cancel()
                if not self.worker.wait(1500):
                    self.worker.terminate()
                    self.worker.wait(500)
        except Exception:
            pass

        try:
            if self.switch_worker and self.switch_worker.isRunning():
                self.switch_worker.cancel()
                if not self.switch_worker.wait(1500):
                    self.switch_worker.terminate()
                    self.switch_worker.wait(500)
        except Exception:
            pass

        for attr_name in ("_open_chat_thread", "_send_in_chat_thread"):
            try:
                th = getattr(self, attr_name, None)
                if th and th.isRunning():
                    th.wait(500)
            except Exception:
                pass

        try:
            from app.power_manager import power_manager
            power_manager.release()
        except Exception:
            pass

        try:
            hotkey_manager.unregister()
        except Exception:
            pass

        try:
            if hasattr(self, "foreground_watcher") and self.foreground_watcher:
                self.foreground_watcher.stop()
        except Exception:
            pass

        try:
            if hasattr(self, "hud") and self.hud:
                self.hud.close()
        except Exception:
            pass

        try:
            if hasattr(self, "switch_countdown_hud") and self.switch_countdown_hud:
                self.switch_countdown_hud.close()
        except Exception:
            pass

        try:
            if hasattr(self, "tray_icon") and self.tray_icon:
                self.tray_icon.hide()
        except Exception:
            pass

        if event:
            try:
                event.accept()
            except Exception:
                pass

        QApplication.quit()
        if "pytest" not in sys.modules:
            import os
            os._exit(0)

