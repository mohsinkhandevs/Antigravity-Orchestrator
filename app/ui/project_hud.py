import time
from typing import Dict, Any, Optional
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QFrame,
    QApplication,
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint
from PyQt6.QtGui import QMouseEvent

from app.config import config
from app.ui.styles import (
    COLOR_SURFACE_1,
    COLOR_SURFACE_2,
    COLOR_BORDER_DEFAULT,
    COLOR_BORDER_SUBTLE,
    COLOR_PRIMARY,
    COLOR_PRIMARY_HOVER,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_FAINT,
)


HUD_FIXED_WIDTH = 380
HUD_DEFAULT_HEIGHT = 360


class ForegroundProjectHUD(QWidget):
    """
    Floating, glassmorphic HUD overlay that displays all active project queues
    strictly when Google Antigravity is in the foreground.

    Features:
    - Real-time card display of all running projects with live timers and status pills.
    - Dedicated per-project [⏸ Pause] and [▶ Resume] buttons.
    - Prominent [✕ Hide] button to manually hide, with restore from Windows System Tray.
    - [🖥 Studio] button to restore the full Antigravity Studio window.
    - Draggable anywhere on screen, remembering position across sessions.
    - WA_ShowWithoutActivating enabled to avoid stealing keyboard cursor focus from Antigravity.
    """

    sig_pause_project_requested = pyqtSignal(str)
    sig_resume_project_requested = pyqtSignal(str)
    sig_pause_all_requested = pyqtSignal()
    sig_resume_all_requested = pyqtSignal()
    sig_stop_all_requested = pyqtSignal()
    sig_restore_studio_requested = pyqtSignal()
    sig_hud_hidden = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle("Antigravity Live Queues HUD")
        hud_h = getattr(config, "hud_height", HUD_DEFAULT_HEIGHT) or HUD_DEFAULT_HEIGHT
        self.setFixedWidth(HUD_FIXED_WIDTH)
        self.setFixedHeight(hud_h)

        self._drag_pos = QPoint()
        self._is_dragging = False
        self.is_manually_hidden: bool = False
        self._automation_state: str = "IDLE"

        self._setup_ui()
        self._restore_position()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Outer Glassmorphic Frame
        self.main_frame = QFrame(self)
        self.main_frame.setObjectName("hud-frame")
        self.main_frame.setStyleSheet(
            """
            #hud-frame {
                background-color: rgba(15, 23, 42, 0.94);
                border: 1px solid #334155;
                border-radius: 10px;
            }
            """
        )
        root_layout.addWidget(self.main_frame)

        content_layout = QVBoxLayout(self.main_frame)
        content_layout.setContentsMargins(10, 8, 10, 8)
        content_layout.setSpacing(6)

        # ─────────────────────────────────────────────────────────────
        # Header Bar (Title, Studio shortcut, Manual Hide button)
        # ─────────────────────────────────────────────────────────────
        header_bar = QHBoxLayout()
        header_bar.setContentsMargins(0, 0, 0, 0)
        header_bar.setSpacing(6)

        icon_lbl = QLabel("⚡")
        icon_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #38bdf8;")
        header_bar.addWidget(icon_lbl)

        title_lbl = QLabel("Antigravity Queues")
        title_lbl.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {COLOR_TEXT_PRIMARY}; letter-spacing: 0.4px;")
        header_bar.addWidget(title_lbl)

        self.lbl_queue_badge = QLabel("0 Queued")
        self.lbl_queue_badge.setStyleSheet(
            "background: #1e293b; color: #94a3b8; border: 1px solid #334155; "
            "border-radius: 4px; font-size: 9px; font-weight: 700; padding: 1px 6px;"
        )
        header_bar.addWidget(self.lbl_queue_badge)

        header_bar.addStretch()

        # Studio Window Button
        btn_studio = QPushButton("🖥 Studio")
        btn_studio.setFixedHeight(22)
        btn_studio.setToolTip("Open full Antigravity Studio window")
        btn_studio.setStyleSheet(
            f"QPushButton {{ background: {COLOR_SURFACE_2}; color: #38bdf8; border: 1px solid {COLOR_BORDER_DEFAULT}; "
            f"border-radius: 4px; font-size: 10px; font-weight: 700; padding: 1px 6px; }}"
            f"QPushButton:hover {{ background: #0c4a6e; border-color: #0284c7; color: #ffffff; }}"
        )
        btn_studio.clicked.connect(self.sig_restore_studio_requested.emit)
        header_bar.addWidget(btn_studio)

        # Prominent Hide Button (Requested by User)
        btn_hide = QPushButton("✕ Hide")
        btn_hide.setFixedHeight(22)
        btn_hide.setToolTip("Hide HUD overlay (re-open anytime from System Tray)")
        btn_hide.setStyleSheet(
            f"QPushButton {{ background: #27272a; color: #a1a1aa; border: 1px solid #3f3f46; border-radius: 4px; "
            f"font-size: 10px; font-weight: 700; padding: 1px 6px; }}"
            f"QPushButton:hover {{ background: #3f3f46; color: #f4f4f5; }}"
        )
        btn_hide.clicked.connect(self.hide_hud)
        header_bar.addWidget(btn_hide)

        content_layout.addLayout(header_bar)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"background-color: {COLOR_BORDER_SUBTLE}; max-height: 1px;")
        content_layout.addWidget(divider)

        # ─────────────────────────────────────────────────────────────
        # Active Project Cards Scroll Area & Container
        # ─────────────────────────────────────────────────────────────
        self.scroll_area = QScrollArea(self.main_frame)
        self.scroll_area.setObjectName("hud-scroll-area")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setStyleSheet(
            """
            #hud-scroll-area {
                background: transparent;
                border: none;
            }
            #hud-scroll-area > QWidget > QWidget {
                background: transparent;
            }
            QScrollBar:vertical {
                background: rgba(15, 23, 42, 0.4);
                width: 6px;
                margin: 0px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #475569;
                min-height: 20px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: #0284c7;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
                background: none;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
            """
        )

        self.cards_container = QWidget()
        self.cards_container.setObjectName("hud-cards-container")
        self.cards_container.setStyleSheet("background: transparent;")
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 2, 4, 2)
        self.cards_layout.setSpacing(6)
        self.cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.placeholder_lbl = QLabel("No active projects running.")
        self.placeholder_lbl.setStyleSheet(f"color: {COLOR_TEXT_FAINT}; font-size: 11px; font-style: italic; padding: 4px;")
        self.cards_layout.addWidget(self.placeholder_lbl)

        self.scroll_area.setWidget(self.cards_container)
        content_layout.addWidget(self.scroll_area, 1)

        # ─────────────────────────────────────────────────────────────
        # Footer Action Bar (Stop Automation button, Status pill)
        # ─────────────────────────────────────────────────────────────
        footer_bar = QHBoxLayout()
        footer_bar.setContentsMargins(0, 2, 0, 0)
        footer_bar.setSpacing(6)

        self.lbl_footer_status = QLabel("● RUNNING")
        self.lbl_footer_status.setStyleSheet("font-size: 10px; font-weight: 700; color: #34d399;")
        footer_bar.addWidget(self.lbl_footer_status)

        footer_bar.addStretch()

        self.btn_pause_all = QPushButton("⏸ Pause All")
        self.btn_pause_all.setFixedHeight(22)
        self.btn_pause_all.setToolTip("Pause all running project sub-queues")
        self.btn_pause_all.setStyleSheet(
            "QPushButton { background: #292524; color: #f5f5f4; border: 1px solid #78716c; border-radius: 4px; "
            "font-size: 10px; font-weight: 700; padding: 1px 6px; }"
            "QPushButton:hover { background: #44403c; border-color: #a8a29e; }"
        )
        self.btn_pause_all.clicked.connect(self.sig_pause_all_requested.emit)
        footer_bar.addWidget(self.btn_pause_all)

        self.btn_resume_all = QPushButton("▶ Resume All")
        self.btn_resume_all.setFixedHeight(22)
        self.btn_resume_all.setToolTip("Resume all paused project sub-queues")
        self.btn_resume_all.setStyleSheet(
            "QPushButton { background: #064e3b; color: #ecfdf5; border: 1px solid #059669; border-radius: 4px; "
            "font-size: 10px; font-weight: 700; padding: 1px 6px; }"
            "QPushButton:hover { background: #047857; border-color: #34d399; }"
        )
        self.btn_resume_all.clicked.connect(self.sig_resume_all_requested.emit)
        footer_bar.addWidget(self.btn_resume_all)

        btn_stop = QPushButton("⏹ Stop All")
        btn_stop.setFixedHeight(22)
        btn_stop.setToolTip("Stop all running automations immediately")
        btn_stop.setStyleSheet(
            "QPushButton { background: #450a0a; color: #fca5a5; border: 1px solid #991b1b; border-radius: 4px; "
            "font-size: 10px; font-weight: 700; padding: 1px 8px; }"
            "QPushButton:hover { background: #7f1d1d; color: #ffffff; border-color: #dc2626; }"
        )
        btn_stop.clicked.connect(self.sig_stop_all_requested.emit)
        footer_bar.addWidget(btn_stop)

        content_layout.addLayout(footer_bar)

    def _restore_position(self):
        """Restores last saved HUD position or docks at top-right by default."""
        x = getattr(config, "hud_x", -1)
        y = getattr(config, "hud_y", -1)

        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            if x <= 0 or y <= 0 or x >= geom.width() or y >= geom.height():
                # Default docking: Top-Right with 20px padding
                default_x = geom.width() - self.width() - 25
                default_y = 40
                self.move(default_x, default_y)
                return

        if x > 0 and y > 0:
            self.move(x, y)

    def _save_position(self):
        """Saves current HUD position to config."""
        pos = self.pos()
        config.hud_x = pos.x()
        config.hud_y = pos.y()
        try:
            config.save()
        except Exception:
            pass

    def hide_hud(self):
        """Called when user clicks [✕ Hide]. Sets manual hide flag and hides window."""
        self.is_manually_hidden = True
        self.hide()
        self.sig_hud_hidden.emit()

    def show_hud(self):
        """Restores HUD visibility from system tray or shortcut."""
        self.is_manually_hidden = False
        self.show()
        self.raise_()

    def set_automation_state(self, state: str):
        self._automation_state = state
        if state == "PAUSED":
            self.lbl_footer_status.setText("⏸ PAUSED")
            self.lbl_footer_status.setStyleSheet("font-size: 10px; font-weight: 700; color: #fbbf24;")
        elif state == "RUNNING":
            self.lbl_footer_status.setText("● RUNNING")
            self.lbl_footer_status.setStyleSheet("font-size: 10px; font-weight: 700; color: #34d399;")
        elif state in ("FINISHED", "COMPLETED"):
            self.lbl_footer_status.setText("✔ FINISHED")
            self.lbl_footer_status.setStyleSheet("font-size: 10px; font-weight: 700; color: #34d399;")
        else:
            self.lbl_footer_status.setText(f"● {state}")
            self.lbl_footer_status.setStyleSheet(f"font-size: 10px; font-weight: 700; color: {COLOR_TEXT_MUTED};")

    def update_projects(self, active_projects_dict: Dict[str, Any]):
        """
        Dynamically refreshes the project queue cards.
        Displays each active project with status pill and dedicated Pause/Resume buttons.
        """
        # Clear existing cards
        while self.cards_layout.count() > 0:
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        # Update Header queue badge
        queued_count = sum(1 for d in active_projects_dict.values() if d.get("pending_count", 0) > 0 or d.get("status") in ("GENERATING", "DISPATCHING", "STABILIZING"))
        active_count = sum(1 for d in active_projects_dict.values() if d.get("status") in ("GENERATING", "DISPATCHING", "STABILIZING"))
        if hasattr(self, "lbl_queue_badge"):
            if queued_count > 0:
                self.lbl_queue_badge.setText(f"{queued_count} Queued | {active_count} Active")
                self.lbl_queue_badge.setStyleSheet(
                    "background: #0369a1; color: #e0f2fe; border: 1px solid #0284c7; "
                    "border-radius: 4px; font-size: 9px; font-weight: 700; padding: 1px 6px;"
                )
            else:
                self.lbl_queue_badge.setText("0 Queued")
                self.lbl_queue_badge.setStyleSheet(
                    "background: #1e293b; color: #94a3b8; border: 1px solid #334155; "
                    "border-radius: 4px; font-size: 9px; font-weight: 700; padding: 1px 6px;"
                )

        if not active_projects_dict:
            self.placeholder_lbl = QLabel("No active projects running.")
            self.placeholder_lbl.setStyleSheet(f"color: {COLOR_TEXT_FAINT}; font-size: 11px; font-style: italic; padding: 4px;")
            self.cards_layout.addWidget(self.placeholder_lbl)
            return

        for proj_name, data in active_projects_dict.items():
            status = data.get("status", "IDLE")
            is_paused = data.get("is_paused", False)
            elapsed = data.get("elapsed_time", 0.0)
            prompt_text = data.get("current_prompt_text", "")
            pending_count = data.get("pending_count", 0)
            total_count = data.get("total_count", 0)
            completed_count = data.get("completed_count", 0)
            next_text = data.get("next_prompt_text", "")

            card = QFrame()
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(8, 6, 8, 6)
            card_layout.setSpacing(4)

            # Card background styling based on status
            if is_paused or status == "PAUSED":
                card.setStyleSheet("QFrame { background: #262626; border: 1px solid #52525b; border-radius: 6px; }")
                pill_bg = "#44403c"
                pill_color = "#f5f5f4"
                pill_text = f"⏸ Paused ({pending_count} pending)" if pending_count > 0 else "⏸ Paused"
            elif status == "GENERATING":
                card.setStyleSheet("QFrame { background: #064e3b; border: 1px solid #059669; border-radius: 6px; }")
                pill_bg = "#047857"
                pill_color = "#6ee7b7"
                pill_text = f"🟢 Generating ({int(elapsed)}s)" if elapsed > 0 else "🟢 Generating"
            elif status == "HALTED_QUOTA":
                card.setStyleSheet("QFrame { background: #451a03; border: 1px solid #d97706; border-radius: 6px; }")
                pill_bg = "#78350f"
                pill_color = "#fcd34d"
                pill_text = "🔒 Quota Halted"
            elif status in ("DISPATCHING", "STABILIZING"):
                card.setStyleSheet("QFrame { background: #0c4a6e; border: 1px solid #0284c7; border-radius: 6px; }")
                pill_bg = "#0369a1"
                pill_color = "#7dd3fc"
                pill_text = f"⏳ {status.title()}"
            elif status in ("COMPLETED", "FINISHED") and pending_count == 0:
                card.setStyleSheet("QFrame { background: #14532d; border: 1px solid #16a34a; border-radius: 6px; }")
                pill_bg = "#15803d"
                pill_color = "#86efac"
                pill_text = f"✔ Finished ({completed_count}/{total_count})" if total_count > 0 else "✔ Finished"
            elif pending_count > 0:
                card.setStyleSheet("QFrame { background: #1e1b4b; border: 1px solid #4338ca; border-radius: 6px; }")
                pill_bg = "#3730a3"
                pill_color = "#c7d2fe"
                pill_text = f"🟡 Queued ({pending_count} pending)"
            else:
                card.setStyleSheet(f"QFrame {{ background: {COLOR_SURFACE_2}; border: 1px solid {COLOR_BORDER_DEFAULT}; border-radius: 6px; }}")
                pill_bg = "#334155"
                pill_color = COLOR_TEXT_MUTED
                pill_text = "⚪ Idle"

            # Row 1: Project Name + Status Pill + Dedicated Action Button
            top_row = QHBoxLayout()
            top_row.setContentsMargins(0, 0, 0, 0)
            top_row.setSpacing(6)

            q_name = data.get("queue_name")
            p_name = data.get("project_name", proj_name)
            if q_name and q_name != "Default":
                lbl_pname = QLabel(f"<b>{p_name}</b> <span style='color: #38bdf8; font-size: 10px;'>[{q_name}]</span>")
            else:
                lbl_pname = QLabel(f"<b>{proj_name}</b>")
            lbl_pname.setStyleSheet(f"color: {COLOR_TEXT_PRIMARY}; font-size: 11px;")
            top_row.addWidget(lbl_pname)

            lbl_pill = QLabel(pill_text)
            lbl_pill.setStyleSheet(
                f"background: {pill_bg}; color: {pill_color}; border-radius: 4px; "
                f"padding: 1px 6px; font-size: 10px; font-weight: 700;"
            )
            top_row.addWidget(lbl_pill)

            top_row.addStretch()

            # Dedicated independent Pause / Resume buttons per project
            if status in ("GENERATING", "DISPATCHING") and not is_paused:
                btn_p_pause = QPushButton("⏸ Pause")
                btn_p_pause.setFixedHeight(22)
                btn_p_pause.setToolTip(f"Pause {proj_name} automation independently")
                btn_p_pause.setStyleSheet(
                    "QPushButton { background: #44403c; color: #f5f5f4; border: 1px solid #78716c; border-radius: 4px; "
                    "font-size: 10px; font-weight: 700; padding: 1px 6px; }"
                    "QPushButton:hover { background: #57534e; border-color: #a8a29e; }"
                )
                btn_p_pause.clicked.connect(lambda _, p=proj_name: self.sig_pause_project_requested.emit(p))
                top_row.addWidget(btn_p_pause)
            elif (is_paused or status in ("PAUSED", "HALTED_QUOTA")) and status not in ("COMPLETED", "FINISHED"):
                btn_p_resume = QPushButton("▶ Resume")
                btn_p_resume.setFixedHeight(22)
                btn_p_resume.setToolTip(f"Resume {proj_name} automation independently")
                btn_p_resume.setStyleSheet(
                    "QPushButton { background: #065f46; color: #ecfdf5; border: 1px solid #10b981; border-radius: 4px; "
                    "font-size: 10px; font-weight: 700; padding: 1px 6px; }"
                    "QPushButton:hover { background: #047857; border-color: #34d399; }"
                )
                btn_p_resume.clicked.connect(lambda _, p=proj_name: self.sig_resume_project_requested.emit(p))
                top_row.addWidget(btn_p_resume)

            card_layout.addLayout(top_row)

            # Row 2: Prompt Preview snippet / Queued summary
            if status in ("COMPLETED", "FINISHED") and pending_count == 0:
                lbl_snippet = QLabel(f"✔ All {total_count} prompts finished" if total_count > 0 else "✔ All queued prompts finished")
                lbl_snippet.setStyleSheet("color: #86efac; font-size: 10px; font-weight: 600;")
                card_layout.addWidget(lbl_snippet)
            elif prompt_text:
                preview = prompt_text.replace("\n", " ").strip()
                if len(preview) > 55:
                    preview = preview[:52] + "..."
                lbl_snippet = QLabel(f"💬 {preview}")
                lbl_snippet.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 10px; font-family: monospace;")
                card_layout.addWidget(lbl_snippet)
            elif pending_count > 0:
                preview = (next_text or "Next prompt pending").replace("\n", " ").strip()
                if len(preview) > 50:
                    preview = preview[:47] + "..."
                lbl_snippet = QLabel(f"📋 {pending_count} pending | Next: \"{preview}\"")
                lbl_snippet.setStyleSheet("color: #a5b4fc; font-size: 10px; font-family: monospace;")
                card_layout.addWidget(lbl_snippet)

            self.cards_layout.addWidget(card)

    # ─────────────────────────────────────────────────────────────────
    # Window Dragging & Position Handling
    # ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = True
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_dragging and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = False
            self._save_position()
            event.accept()


class AccountSwitchCountdownHUD(QWidget):
    """
    Always-on-top mini HUD displayed for 30 seconds before clicking 'Continue with Google'
    so the user is warned before the external browser opens and OCR mouse automation runs.

    Features:
    - Shows over any active application without stealing keyboard focus (WA_ShowWithoutActivating).
    - Live 30-second countdown badge and progress bar.
    - Prominent '⚡ Change Now' button to immediately proceed with account switch.
    - Automatically proceeds to click 'Continue with Google' when countdown reaches 0s.
    """

    sig_change_now_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle("Antigravity Account Switch Countdown HUD")
        self.setFixedWidth(360)

        self._drag_pos = QPoint()
        self._is_dragging = False
        self.current_email: str = ""
        self.target_email: str = ""
        self.remaining_seconds: int = 30
        self.total_seconds: int = 30

        self._setup_ui()
        self._position_top_center()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.main_frame = QFrame(self)
        self.main_frame.setObjectName("switch-countdown-frame")
        self.main_frame.setStyleSheet(
            """
            #switch-countdown-frame {
                background-color: rgba(15, 23, 42, 0.96);
                border: 1.5px solid #f59e0b;
                border-radius: 10px;
            }
            """
        )
        root_layout.addWidget(self.main_frame)

        content = QVBoxLayout(self.main_frame)
        content.setContentsMargins(12, 10, 12, 10)
        content.setSpacing(8)

        # Header Row: Title + Countdown Badge
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)

        self.lbl_title = QLabel("🔄 Switching Google Account")
        self.lbl_title.setStyleSheet(
            f"font-size: 12px; font-weight: 700; color: {COLOR_TEXT_PRIMARY};"
        )
        header_row.addWidget(self.lbl_title)
        header_row.addStretch()

        self.lbl_timer_badge = QLabel("⏳ 30s")
        self.lbl_timer_badge.setStyleSheet(
            "background: #78350f; color: #fde68a; border: 1px solid #d97706; "
            "border-radius: 5px; padding: 2px 8px; font-size: 11px; font-weight: 700;"
        )
        header_row.addWidget(self.lbl_timer_badge)
        content.addLayout(header_row)

        # Target Account Row
        self.lbl_account_info = QLabel("Target: —")
        self.lbl_account_info.setWordWrap(True)
        self.lbl_account_info.setStyleSheet(
            "color: #38bdf8; font-size: 11px; font-weight: 600;"
        )
        content.addWidget(self.lbl_account_info)

        # Notice / Subtitle Row
        self.lbl_subtext = QLabel(
            "Clicking 'Continue with Google' in 30s (will open browser)..."
        )
        self.lbl_subtext.setWordWrap(True)
        self.lbl_subtext.setStyleSheet(
            f"color: {COLOR_TEXT_SECONDARY}; font-size: 10px;"
        )
        content.addWidget(self.lbl_subtext)

        # Countdown Progress Bar
        self.progress_bar = QProgressBar(self.main_frame)
        self.progress_bar.setRange(0, 30)
        self.progress_bar.setValue(30)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setStyleSheet(
            """
            QProgressBar {
                background-color: #1e293b;
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background-color: #f59e0b;
                border-radius: 3px;
            }
            """
        )
        content.addWidget(self.progress_bar)

        # Action Button Row: Prominent "Change Now" button
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 2, 0, 0)
        action_row.setSpacing(8)

        self.btn_change_now = QPushButton("⚡ Change Now")
        self.btn_change_now.setFixedHeight(28)
        self.btn_change_now.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_change_now.setToolTip(
            "Immediately click 'Continue with Google' and switch account now"
        )
        self.btn_change_now.setStyleSheet(
            "QPushButton { background: #0284c7; color: #ffffff; border: 1px solid #38bdf8; "
            "border-radius: 6px; font-size: 11px; font-weight: 700; padding: 3px 14px; }"
            "QPushButton:hover { background: #0369a1; border-color: #7dd3fc; }"
        )
        self.btn_change_now.clicked.connect(self._on_change_now_clicked)
        action_row.addWidget(self.btn_change_now)

        content.addLayout(action_row)

    def _position_top_center(self):
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            self.adjustSize()
            x = geom.left() + (geom.width() - self.width()) // 2
            y = geom.top() + 28
            self.move(max(10, x), max(10, y))

    def _enforce_win32_topmost(self):
        """Forces the HUD to the top of the Z-order over all Windows applications without stealing focus."""
        import sys
        if sys.platform == "win32":
            try:
                import ctypes
                hwnd = int(self.winId())
                # HWND_TOPMOST = -1, SWP_NOSIZE = 0x0001, SWP_NOMOVE = 0x0002, SWP_NOACTIVATE = 0x0010, SWP_SHOWWINDOW = 0x0040
                ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
            except Exception:
                pass

    def start_countdown(self, current_email: str, target_email: str, total_seconds: int = 30):
        self.current_email = (current_email or "").strip()
        self.target_email = (target_email or "").strip()
        self.total_seconds = max(1, int(total_seconds))
        self.remaining_seconds = int(total_seconds)

        if self.current_email and self.target_email and self.current_email.lower() != self.target_email.lower():
            self.lbl_account_info.setText(f"{self.current_email}  ➔  <b>{self.target_email}</b>")
        elif self.target_email:
            self.lbl_account_info.setText(f"Target Account: <b>{self.target_email}</b>")
        else:
            self.lbl_account_info.setText("Preparing next Google account...")

        self.progress_bar.setRange(0, self.total_seconds)
        self._position_top_center()
        self.show()
        self.raise_()
        self._enforce_win32_topmost()
        self.update_countdown(self.remaining_seconds, self.total_seconds)

    def update_countdown(self, remaining_seconds: int, total_seconds: int = 30):
        rem = max(0, int(remaining_seconds))
        tot = max(1, int(total_seconds))
        self.remaining_seconds = rem
        self.total_seconds = tot
        self.lbl_timer_badge.setText(f"⏳ {rem}s")
        self.lbl_subtext.setText(
            f"Signed out. Auto-clicking 'Continue with Google' in {rem}s (or click Change Now)..."
        )
        self.progress_bar.setRange(0, tot)
        self.progress_bar.setValue(rem)
        if self.isVisible():
            self.raise_()
            self._enforce_win32_topmost()

    def stop_and_hide(self):
        self.hide()

    def _on_change_now_clicked(self):
        if not self.isVisible():
            return
        self.stop_and_hide()
        self.sig_change_now_requested.emit()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            btn_pos = self.btn_change_now.mapFrom(self, event.position().toPoint())
            if self.btn_change_now.rect().contains(btn_pos):
                self._on_change_now_clicked()
                event.accept()
                return
            self._is_dragging = True
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_dragging and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = False
            event.accept()


