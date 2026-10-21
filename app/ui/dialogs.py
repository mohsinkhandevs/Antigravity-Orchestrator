"""
Dialogs for Antigravity Automation Desktop Studio.
Modernized to Linear / Fluent 2 design standards with zero functional regressions.
"""

import os
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QPlainTextEdit,
    QMessageBox,
    QFrame,
    QScrollArea,
    QListWidget,
    QListWidgetItem,
    QCheckBox,
    QRadioButton,
    QButtonGroup,
    QComboBox,
    QLineEdit,
    QSpinBox,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QIcon, QTextCursor

from app.config import config
from app.models import ensure_prompt_prefix, detect_prompt_mode
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


class PromptEditDialog(QDialog):
    """Dialog for creating or editing a queued prompt with user-selectable /goal, /plan, or Plain mode."""

    def __init__(
        self,
        parent=None,
        initial_text: str = "/goal ",
        initial_project: str = "",
        initial_queue: str = "Default",
        title: str = "Prompt Editor"
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(680, 430)
        self.setMinimumSize(540, 330)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        # Top row: Project and Sub-Queue selection
        proj_row = QHBoxLayout()
        proj_lbl = QLabel("Project:")
        proj_lbl.setStyleSheet(f"font-weight: 600; font-size: 12px; color: {COLOR_TEXT_SECONDARY};")
        proj_row.addWidget(proj_lbl)

        self.project_combo = QComboBox(self)
        self.project_combo.setEditable(True)
        known = list(getattr(config, "known_projects", ["No Project"]))
        if "No Project" not in known:
            known.insert(0, "No Project")
        self.project_combo.addItems(known)
        target_p = (initial_project or "No Project").strip()
        if not target_p or target_p.lower() == "default":
            target_p = "No Project"
        if target_p and self.project_combo.findText(target_p) == -1:
            self.project_combo.addItem(target_p)
        self.project_combo.setCurrentText(target_p)
        self.project_combo.setStyleSheet(
            f"QComboBox {{ background: {COLOR_SURFACE_2}; color: {COLOR_TEXT_PRIMARY}; border: 1px solid {COLOR_BORDER_DEFAULT}; "
            f"border-radius: 6px; padding: 3px 8px; font-size: 12px; min-width: 140px; }}"
            f"QComboBox:hover {{ border-color: #38bdf8; }}"
        )
        proj_row.addWidget(self.project_combo)

        proj_row.addSpacing(10)

        queue_lbl = QLabel("Queue:")
        queue_lbl.setStyleSheet(f"font-weight: 600; font-size: 12px; color: {COLOR_TEXT_SECONDARY};")
        proj_row.addWidget(queue_lbl)

        self.queue_combo = QComboBox(self)
        self.queue_combo.setEditable(True)
        default_queues = ["Default", "Queue 1", "Queue 2", "Queue 3", "Module A", "Module B"]
        target_q = (initial_queue or "Default").strip() or "Default"
        if target_q not in default_queues:
            default_queues.append(target_q)
        self.queue_combo.addItems(default_queues)
        self.queue_combo.setCurrentText(target_q)
        self.queue_combo.setStyleSheet(
            f"QComboBox {{ background: {COLOR_SURFACE_2}; color: {COLOR_TEXT_PRIMARY}; border: 1px solid {COLOR_BORDER_DEFAULT}; "
            f"border-radius: 6px; padding: 3px 8px; font-size: 12px; min-width: 140px; }}"
            f"QComboBox:hover {{ border-color: #38bdf8; }}"
        )
        proj_row.addWidget(self.queue_combo)
        proj_row.addStretch()
        layout.addLayout(proj_row)

        # Header row with command mode selector buttons
        header_row = QHBoxLayout()
        header_lbl = QLabel("Prompt:")
        header_lbl.setStyleSheet(f"font-weight: 700; font-size: 14px; color: {COLOR_TEXT_PRIMARY};")
        header_row.addWidget(header_lbl)
        header_row.addStretch()

        mode_lbl = QLabel("Mode:")
        mode_lbl.setStyleSheet(f"font-weight: 600; font-size: 12px; color: {COLOR_TEXT_MUTED};")
        header_row.addWidget(mode_lbl)

        self.btn_mode_goal = QPushButton("/goal")
        self.btn_mode_goal.setCheckable(True)
        self.btn_mode_goal.setToolTip("Autonomous Goal Execution Mode (/goal)")

        self.btn_mode_plan = QPushButton("/plan")
        self.btn_mode_plan.setCheckable(True)
        self.btn_mode_plan.setToolTip("Architectural Planning Mode (/plan)")

        self.btn_mode_none = QPushButton("Plain")
        self.btn_mode_none.setCheckable(True)
        self.btn_mode_none.setToolTip("Standard Prompt (No slash command prefix)")

        btn_style = (
            f"QPushButton {{ font-size: 11px; font-weight: 700; padding: 4px 12px; border-radius: 6px; "
            f"border: 1px solid {COLOR_BORDER_DEFAULT}; background: {COLOR_SURFACE_2}; color: {COLOR_TEXT_SECONDARY}; }}"
            f"QPushButton:hover {{ background: #263350; border-color: #38bdf8; color: #ffffff; }}"
            f"QPushButton:checked {{ background: {COLOR_PRIMARY}; color: #ffffff; border-color: {COLOR_PRIMARY}; }}"
        )
        for btn in (self.btn_mode_goal, self.btn_mode_plan, self.btn_mode_none):
            btn.setStyleSheet(btn_style)
            header_row.addWidget(btn)

        layout.addLayout(header_row)

        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setPlaceholderText(
            "E.g., /goal Build the feature, or /plan Design the database..."
        )
        self.text_edit.setPlainText(initial_text)
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.text_edit.setTextCursor(cursor)
        layout.addWidget(self.text_edit)

        # Bottom buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        char_count_lbl = QLabel(f"Length: {len(initial_text)} chars")
        char_count_lbl.setObjectName("lbl-muted")
        btn_layout.addWidget(char_count_lbl)
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("btn-secondary")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Save")
        save_btn.setObjectName("btn-primary")
        save_btn.clicked.connect(self._validate_and_accept)
        btn_layout.addWidget(save_btn)

        layout.addLayout(btn_layout)

        # Wire mode button clicks
        self.btn_mode_goal.clicked.connect(lambda: self._apply_mode("/goal"))
        self.btn_mode_plan.clicked.connect(lambda: self._apply_mode("/plan"))
        self.btn_mode_none.clicked.connect(lambda: self._apply_mode("none"))

        self._updating_mode = False
        self.text_edit.textChanged.connect(self._on_text_changed)
        self.text_edit.textChanged.connect(
            lambda: char_count_lbl.setText(f"Length: {len(self.text_edit.toPlainText())} chars")
        )

        initial_mode = detect_prompt_mode(initial_text)
        self._sync_mode_buttons(initial_mode)

    def _apply_mode(self, mode: str):
        self._updating_mode = True
        try:
            curr = self.text_edit.toPlainText()
            updated = ensure_prompt_prefix(curr, mode)
            if updated != curr:
                cursor = self.text_edit.textCursor()
                pos = cursor.position()
                self.text_edit.setPlainText(updated)
                new_cursor = self.text_edit.textCursor()
                new_cursor.setPosition(min(len(updated), max(0, pos + (len(updated) - len(curr)))))
                self.text_edit.setTextCursor(new_cursor)
            self._sync_mode_buttons(mode)
        finally:
            self._updating_mode = False

    def _sync_mode_buttons(self, mode: str):
        self.btn_mode_goal.setChecked(mode == "/goal")
        self.btn_mode_plan.setChecked(mode == "/plan")
        self.btn_mode_none.setChecked(mode not in ("/goal", "/plan"))

    def _on_text_changed(self):
        if self._updating_mode:
            return
        mode = detect_prompt_mode(self.text_edit.toPlainText())
        self._sync_mode_buttons(mode)

    def _validate_and_accept(self):
        text = self.text_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Empty Prompt", "Prompt text cannot be empty.")
            return
        self.accept()

    def get_text(self) -> str:
        return self.text_edit.toPlainText().strip()

    def get_project(self) -> str:
        return self.project_combo.currentText().strip()

    def get_queue_name(self) -> str:
        return (self.queue_combo.currentText() or "").strip() or "Default"


class ImportPromptsDialog(QDialog):
    """
    Dialog displayed when importing prompts, allowing the user to select
    what command to invoke (/goal, /plan, keep from file, plain), target project, and queue destination.
    """

    def __init__(self, parent=None, prompt_count: int = 0, has_existing_prompts: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Import Prompts Queue")
        self.resize(540, 420)
        self.setMinimumSize(460, 320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title_lbl = QLabel(f"Import Prompts ({prompt_count})")
        title_lbl.setStyleSheet(f"font-weight: 700; font-size: 15px; color: {COLOR_TEXT_PRIMARY};")
        layout.addWidget(title_lbl)

        # 1. Project Selection Box
        proj_box = QFrame()
        proj_box.setStyleSheet(CARD_STYLE)
        proj_layout = QVBoxLayout(proj_box)
        proj_layout.setContentsMargins(12, 10, 12, 10)
        proj_layout.setSpacing(8)

        lbl_proj = QLabel("Project:")
        lbl_proj.setStyleSheet(f"font-weight: 700; font-size: 11px; color: {COLOR_TEXT_MUTED}; letter-spacing: 0.5px;")
        proj_layout.addWidget(lbl_proj)

        self.combo_import_project = QComboBox(self)
        self.combo_import_project.setEditable(True)
        known = list(getattr(config, "known_projects", ["No Project"]))
        if "No Project" not in known:
            known.insert(0, "No Project")
        self.combo_import_project.addItems(known)
        default_p = "No Project"
        if default_p and self.combo_import_project.findText(default_p) == -1:
            self.combo_import_project.addItem(default_p)
        self.combo_import_project.setCurrentText(default_p)
        self.combo_import_project.setStyleSheet(
            f"QComboBox {{ background: {COLOR_SURFACE_2}; color: {COLOR_TEXT_PRIMARY}; border: 1px solid {COLOR_BORDER_DEFAULT}; "
            f"border-radius: 6px; padding: 4px 8px; font-size: 12px; }}"
        )
        proj_layout.addWidget(self.combo_import_project)
        layout.addWidget(proj_box)

        # 2. Mode / Prefix selection box
        mode_box = QFrame()
        mode_box.setStyleSheet(CARD_STYLE)
        mode_layout = QVBoxLayout(mode_box)
        mode_layout.setContentsMargins(12, 10, 12, 10)
        mode_layout.setSpacing(8)

        lbl_mode = QLabel("Command Mode:")
        lbl_mode.setStyleSheet(f"font-weight: 700; font-size: 11px; color: {COLOR_TEXT_MUTED}; letter-spacing: 0.5px;")
        mode_layout.addWidget(lbl_mode)

        self.btn_group_mode = QButtonGroup(self)

        self.rb_keep = QRadioButton("Keep file commands")
        self.rb_goal = QRadioButton("/goal")
        self.rb_plan = QRadioButton("/plan")
        self.rb_plain = QRadioButton("Plain text")

        self.rb_keep.setChecked(True)

        for idx, rb in enumerate((self.rb_keep, self.rb_goal, self.rb_plan, self.rb_plain)):
            rb.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 12px;")
            self.btn_group_mode.addButton(rb, idx)
            mode_layout.addWidget(rb)

        layout.addWidget(mode_box)

        # 3. Queue Destination box (only if existing prompts)
        self.has_existing = has_existing_prompts
        if has_existing_prompts:
            dest_box = QFrame()
            dest_box.setStyleSheet(CARD_STYLE)
            dest_layout = QVBoxLayout(dest_box)
            dest_layout.setContentsMargins(12, 10, 12, 10)
            dest_layout.setSpacing(8)

            lbl_dest = QLabel("Destination:")
            lbl_dest.setStyleSheet(f"font-weight: 700; font-size: 11px; color: {COLOR_TEXT_MUTED}; letter-spacing: 0.5px;")
            dest_layout.addWidget(lbl_dest)

            self.btn_group_dest = QButtonGroup(self)
            self.rb_replace = QRadioButton("Replace queue")
            self.rb_append = QRadioButton("Append to queue")
            self.rb_replace.setChecked(True)

            self.rb_replace.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 12px;")
            self.rb_append.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 12px;")
            self.btn_group_dest.addButton(self.rb_replace, 0)
            self.btn_group_dest.addButton(self.rb_append, 1)

            dest_layout.addWidget(self.rb_replace)
            dest_layout.addWidget(self.rb_append)
            layout.addWidget(dest_box)

        # Bottom buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("btn-secondary")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        import_btn = QPushButton("Import")
        import_btn.setObjectName("btn-primary")
        import_btn.clicked.connect(self.accept)
        btn_layout.addWidget(import_btn)

        layout.addLayout(btn_layout)

    def get_prefix_mode(self) -> str:
        if self.rb_goal.isChecked():
            return "/goal"
        elif self.rb_plan.isChecked():
            return "/plan"
        elif self.rb_plain.isChecked():
            return "none"
        return "keep"

    def get_destination_mode(self) -> str:
        if self.has_existing and self.rb_append.isChecked():
            return "append"
        return "replace"

    def get_project(self) -> str:
        return self.combo_import_project.currentText().strip()


class ErrorResolutionDialog(QDialog):
    """
    Dialog prompted when automation encounters a failure, allowing the user
    to Retry, Skip to the next prompt, or Abort the queue.
    """

    def __init__(
        self,
        parent=None,
        prompt_id: str = "",
        title: str = "Automation Error",
        error_detail: str = "",
        screenshot_path: str = ""
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(650, 480)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        self.selected_action = "abort"
        self.screenshot_path = screenshot_path

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Header warning banner
        banner = QFrame()
        banner.setStyleSheet(
            ".QFrame { background-color: #2b0e14; border: 1px solid #7f1d1d; border-radius: 8px; } "
            "QLabel { background-color: transparent; border: none; }"
        )
        banner_layout = QVBoxLayout(banner)
        banner_layout.setContentsMargins(12, 10, 12, 10)
        banner_layout.setSpacing(6)

        warn_title = QLabel("Action Required: Automation Encountered an Issue")
        warn_title.setStyleSheet("color: #fca5a5; font-weight: 700; font-size: 14px;")
        banner_layout.addWidget(warn_title)

        err_desc = QLabel(error_detail)
        err_desc.setWordWrap(True)
        err_desc.setStyleSheet("color: #fecaca; font-size: 13px;")
        banner_layout.addWidget(err_desc)
        layout.addWidget(banner)

        # Screenshot preview section if present
        if screenshot_path and os.path.exists(screenshot_path):
            ss_header = QHBoxLayout()
            lbl_ss = QLabel("Screen State at Failure:")
            lbl_ss.setStyleSheet(f"font-weight: 600; color: {COLOR_TEXT_MUTED}; font-size: 12px;")
            ss_header.addWidget(lbl_ss)
            ss_header.addStretch()

            open_img_btn = QPushButton("Open Full Screenshot")
            open_img_btn.setObjectName("btn-secondary")
            open_img_btn.clicked.connect(self._open_screenshot)
            ss_header.addWidget(open_img_btn)
            layout.addLayout(ss_header)

            img_preview = QLabel()
            img_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            img_preview.setStyleSheet(
                f"background-color: {COLOR_CANVAS}; border: 1px solid {COLOR_BORDER_DEFAULT}; border-radius: 6px; padding: 4px;"
            )
            pix = QPixmap(screenshot_path)
            if not pix.isNull():
                img_preview.setPixmap(
                    pix.scaled(580, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
            layout.addWidget(img_preview)

        help_lbl = QLabel("Choose how you would like to proceed:")
        help_lbl.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-weight: 600; font-size: 12px;")
        layout.addWidget(help_lbl)

        # Buttons (Retry, Skip, Abort)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        abort_btn = QPushButton("Abort Queue")
        abort_btn.setObjectName("btn-danger")
        abort_btn.clicked.connect(lambda: self._choose("abort"))
        btn_layout.addWidget(abort_btn)

        btn_layout.addStretch()

        skip_btn = QPushButton("Skip This Prompt")
        skip_btn.setObjectName("btn-secondary")
        skip_btn.clicked.connect(lambda: self._choose("skip"))
        btn_layout.addWidget(skip_btn)

        retry_btn = QPushButton("Retry Step")
        retry_btn.setObjectName("btn-primary")
        retry_btn.clicked.connect(lambda: self._choose("retry"))
        btn_layout.addWidget(retry_btn)

        layout.addLayout(btn_layout)

    def _open_screenshot(self):
        if self.screenshot_path and os.path.exists(self.screenshot_path):
            try:
                os.startfile(self.screenshot_path)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to open image: {e}")

    def _choose(self, action: str):
        self.selected_action = action
        self.accept()


class ScreenShareDialog(QDialog):
    """
    Google Meet / WhatsApp style target window and screen share selection dialog.
    Allows user to select the Antigravity window, choose manual placement, and enable
    the floating real-time log HUD.
    """

    def __init__(
        self,
        parent=None,
        default_target: str = "Antigravity",
        dialog_title: str = "Select Antigravity Window",
        header_title: str = "Select Antigravity Window",
        header_subtitle: str = "",
        action_button_text: str = "🚀  Start",
        show_automation_options: bool = True,
        show_quota_precheck: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle(dialog_title)
        self.resize(640, 500)
        self.setMinimumSize(540, 420)

        self.selected_hwnd = 0
        self.selected_title = ""
        self.is_manual_mode = False
        self.enable_floating_hud = False
        self.countdown_seconds = 3
        self.show_automation_options = show_automation_options

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Header banner
        header = QHBoxLayout()
        header.setSpacing(12)

        icon_lbl = QLabel("🖥")
        icon_lbl.setStyleSheet("font-size: 28px; padding: 4px;")
        header.addWidget(icon_lbl)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        h_title = QLabel(header_title)
        h_title.setStyleSheet(f"font-weight: 700; font-size: 15px; color: {COLOR_TEXT_PRIMARY};")
        title_col.addWidget(h_title)

        if header_subtitle:
            h_sub = QLabel(header_subtitle)
            h_sub.setObjectName("lbl-muted")
            h_sub.setWordWrap(True)
            title_col.addWidget(h_sub)
        header.addLayout(title_col)
        layout.addLayout(header)

        # Window list selector
        list_lbl = QLabel("Available Windows & Screens:")
        list_lbl.setStyleSheet(f"font-weight: 600; font-size: 12px; color: {COLOR_TEXT_MUTED};")
        layout.addWidget(list_lbl)
        self.window_list = QListWidget()
        self.window_list.setStyleSheet(
            f"""
            QListWidget {{
                background-color: #0c101c;
                border: 1px solid {COLOR_BORDER_DEFAULT};
                border-radius: 8px;
                padding: 6px;
                color: {COLOR_TEXT_PRIMARY};
            }}
            QListWidget::item {{
                padding: 9px 12px;
                border-radius: 6px;
                margin-bottom: 2px;
            }}
            QListWidget::item:selected {{
                background-color: #1a253d;
                border: 1px solid {COLOR_PRIMARY};
                color: #ffffff;
            }}
            QListWidget::item:hover:!selected {{
                background-color: #121828;
            }}
            """
        )
        layout.addWidget(self.window_list)

        self._populate_windows(default_target)

        # Option checkboxes
        opts_box = QVBoxLayout()
        opts_box.setSpacing(6)
        self.chk_floating_hud = None

        self.chk_countdown = QCheckBox("3-second countdown")
        self.chk_countdown.setChecked(True)
        self.chk_countdown.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_MUTED};")
        opts_box.addWidget(self.chk_countdown)

        if show_automation_options:
            self.chk_quota_each_prompt = QCheckBox("Inspect quota before each prompt")
            self.chk_quota_each_prompt.setChecked(getattr(config, "check_quota_each_prompt", True))
            self.chk_quota_each_prompt.setStyleSheet("font-size: 12px; color: #a5b4fc; font-weight: 600;")
            self.chk_quota_each_prompt.setToolTip(
                "When checked, inspects Gemini model limits before every prompt in the queue and auto-switches if low.\n"
                "When unchecked, quota checks are bypassed completely."
            )
            opts_box.addWidget(self.chk_quota_each_prompt)
            self.chk_preflight_quota = self.chk_quota_each_prompt
        else:
            self.chk_quota_each_prompt = None
            self.chk_preflight_quota = None

        if show_quota_precheck:
            self.chk_precheck_quota = QCheckBox("Check quota before switching")
            self.chk_precheck_quota.setChecked(True)
            self.chk_precheck_quota.setStyleSheet("font-size: 12px; color: #a5b4fc; font-weight: 600;")
            self.chk_precheck_quota.setToolTip(
                "When checked, inspects live Gemini model limits first.\n"
                "If quota is healthy, no account switch is performed.\n"
                "If unchecked, forces an immediate switch without checking quota."
            )
            opts_box.addWidget(self.chk_precheck_quota)
        else:
            self.chk_precheck_quota = None

        layout.addLayout(opts_box)

        # Bottom buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.setObjectName("btn-secondary")
        refresh_btn.clicked.connect(lambda: self._populate_windows(default_target))
        btn_layout.addWidget(refresh_btn)

        btn_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("btn-secondary")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        start_btn = QPushButton(action_button_text)
        start_btn.setObjectName("btn-success")
        start_btn.setMinimumHeight(32)
        start_btn.clicked.connect(self._on_start_clicked)
        btn_layout.addWidget(start_btn)

        layout.addLayout(btn_layout)

    def _populate_windows(self, default_target: str):
        import pygetwindow as gw
        self.window_list.clear()

        # Item 0: Manual / Full Screen
        manual_item = QListWidgetItem("🖥  Entire Screen (Manual)")
        manual_item.setData(Qt.ItemDataRole.UserRole, {"hwnd": 0, "title": "__MANUAL__", "is_manual": True})
        self.window_list.addItem(manual_item)

        detected_wins = []
        try:
            for w in gw.getAllWindows():
                title = w.title.strip()
                if not title:
                    continue
                t_lower = title.lower()
                # Exclude runner studio
                if "studio" in t_lower or "runner" in t_lower or "automation runner" in t_lower:
                    continue
                detected_wins.append(w)
        except Exception:
            pass

        recommended_row = 0
        current_row = 1

        try:
            from app.matcher import (
                is_white_logo_antigravity_window,
                is_ide_process,
                is_browser_process,
            )
        except ImportError:
            def is_white_logo_antigravity_window(hwnd):
                return False
            def is_ide_process(hwnd, t):
                return "ide" in t.lower()
            def is_browser_process(hwnd, t):
                return any(b in t.lower() for b in ["chrome", "edge", "firefox", "brave"])

        for w in detected_wins:
            t = w.title
            t_lower = t.lower()
            hwnd = w._hWnd

            if is_white_logo_antigravity_window(hwnd):
                display_text = f"⭐  {t}   (Recommended)"
                is_match = True
            elif is_ide_process(hwnd, t):
                display_text = f"💻  {t}   (IDE Editor)"
                is_match = False
            elif is_browser_process(hwnd, t):
                display_text = f"🌐  {t}   (Browser)"
                is_match = False
            elif t.strip() == "Antigravity" or t_lower == "antigravity" or "google antigravity" in t_lower:
                display_text = f"⭐  {t}   (Recommended)"
                is_match = True
            elif default_target.lower() in t_lower and not is_ide_process(hwnd, t) and not is_browser_process(hwnd, t):
                display_text = f"⭐  {t}   (Target)"
                is_match = True
            else:
                display_text = f"     {t}"
                is_match = False

            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, {"hwnd": w._hWnd, "title": t, "is_manual": False})
            self.window_list.addItem(item)

            if is_match and recommended_row == 0:
                recommended_row = current_row
            current_row += 1

        # Select recommended row or default to manual
        self.window_list.setCurrentRow(recommended_row)

    def _on_start_clicked(self):
        curr_item = self.window_list.currentItem()
        if not curr_item:
            QMessageBox.warning(self, "No Selection", "Please select a target window or 'Entire Screen'.")
            return

        data = curr_item.data(Qt.ItemDataRole.UserRole) or {}
        self.selected_hwnd = data.get("hwnd", 0)
        self.selected_title = data.get("title", "")
        self.is_manual_mode = data.get("is_manual", False)
        self.enable_floating_hud = False
        self.countdown_seconds = 3 if (self.chk_countdown and self.chk_countdown.isChecked()) else 0
        self.force_switch = (not self.chk_precheck_quota.isChecked()) if self.chk_precheck_quota else False
        if hasattr(self, "chk_quota_each_prompt") and self.chk_quota_each_prompt:
            is_checked = self.chk_quota_each_prompt.isChecked()
            config.check_quota_each_prompt = is_checked
            config.check_quota_before_queue = is_checked
            if is_checked:
                # Ensure safety gate & auto-switch are active as promised by dialog tooltip
                if not getattr(config, "auto_switch_on_low_quota", False) and not getattr(config, "pause_if_quota_low", False):
                    config.low_quota_action = "change_account"
                    config.auto_switch_on_low_quota = True
                    config.pause_if_quota_low = True
            try:
                config.save()
            except Exception:
                pass
        self.accept()


class ActiveAgentsStartupDialog(QDialog):
    """
    Startup Conflict Dialog:
    Shown when active agents are detected generating in the target project at automation launch.
    Prompts user whether to proceed immediately in parallel or wait for all active agents to reach 0.
    Includes a 30-second live countdown timer that auto-defaults to 'wait' if unattended.
    """

    def __init__(
        self,
        parent=None,
        project_name: str = "",
        running_agents: list = None,
        queue_count: int = 1,
        timeout_seconds: int = 30,
        active_conversations: list = None,
        **kwargs,
    ):
        super().__init__(parent)
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QProgressBar

        self.project_name = project_name or "Target Project"
        self.running_agents = running_agents if running_agents is not None else (active_conversations or [])
        self.queue_count = queue_count
        self.remaining_seconds = timeout_seconds
        self.total_seconds = timeout_seconds
        self.selected_action = "wait"  # default action on timeout

        busy_projects = list(dict.fromkeys(r.get("project") for r in self.running_agents if r.get("project")))
        has_multiple_projects = len(busy_projects) > 1 or ("," in self.project_name)

        self.setWindowTitle("Active Agents Detected — Start Decision")
        self.resize(560, 380)
        self.setMinimumSize(480, 320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        # Header with warning badge
        header = QHBoxLayout()
        icon_lbl = QLabel("⚡")
        icon_lbl.setStyleSheet("font-size: 26px; padding: 2px;")
        header.addWidget(icon_lbl)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title_text = "Active Agents Detected in Target Projects" if has_multiple_projects else "Active Agents Detected in Target Project"
        self.h_title = QLabel(title_text)
        self.h_title.setStyleSheet(f"font-weight: 700; font-size: 15px; color: {COLOR_WARNING};")
        title_col.addWidget(self.h_title)

        proj_display = self.project_name if self.project_name and self.project_name != "Target Project" else (", ".join(busy_projects) if busy_projects else "Target Project")
        proj_label = "projects" if has_multiple_projects else "project"

        self.h_sub = QLabel(
            f"Antigravity is currently generating responses in <b>{len(self.running_agents)}</b> active conversation(s) "
            f"for {proj_label} <b>'{proj_display}'</b>.<br/>"
            f"Idle target projects start immediately and busy projects will wait until their active agents finish (or can start now)."
        )
        self.h_sub.setWordWrap(True)
        self.h_sub.setStyleSheet(f"color: {COLOR_TEXT_SECONDARY}; font-size: 12px; line-height: 1.4;")
        title_col.addWidget(self.h_sub)
        header.addLayout(title_col)
        layout.addLayout(header)

        # List of running conversations
        list_lbl = QLabel("Currently Running Conversation(s):")
        list_lbl.setStyleSheet(f"font-weight: 600; font-size: 11px; color: {COLOR_TEXT_MUTED}; text-transform: uppercase; letter-spacing: 0.5px;")
        layout.addWidget(list_lbl)

        self.conv_list = QListWidget()
        self.conv_list.setMaximumHeight(120)
        self.conv_list.setStyleSheet(
            f"QListWidget {{ background: #0c101c; border: 1px solid {COLOR_BORDER_DEFAULT}; border-radius: 6px; padding: 4px; color: {COLOR_TEXT_PRIMARY}; font-size: 12px; }}"
        )
        for r in self.running_agents:
            title = r.get("title") or r.get("id") or "Active Conversation"
            cid = r.get("id") or ""
            cid_snippet = f" [{cid[:8]}...]" if cid else ""
            display_title = f"[{r['project']}] {title}" if (has_multiple_projects and r.get("project")) else title
            item = QListWidgetItem(f"● {display_title}{cid_snippet}")
            item.setForeground(Qt.GlobalColor.white)
            self.conv_list.addItem(item)
        layout.addWidget(self.conv_list)

        # Question prompt
        question_lbl = QLabel(
            "Would you like to start the parallel queues immediately, or wait until all running agents finish?"
        )
        question_lbl.setWordWrap(True)
        question_lbl.setStyleSheet(f"font-weight: 600; font-size: 12px; color: {COLOR_TEXT_PRIMARY};")
        layout.addWidget(question_lbl)

        # Countdown & Timer Bar
        self.countdown_lbl = QLabel(f"⏳ Auto-defaulting to 'Wait for Agents' in {self.remaining_seconds}s (Unattended Mode)...")
        self.countdown_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: #38bdf8;")
        layout.addWidget(self.countdown_lbl)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, self.total_seconds)
        self.progress_bar.setValue(self.remaining_seconds)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setStyleSheet(
            "QProgressBar { background: #1e293b; border: none; border-radius: 2px; } "
            "QProgressBar::chunk { background: #0284c7; border-radius: 2px; }"
        )
        layout.addWidget(self.progress_bar)

        layout.addStretch()

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.btn_proceed = QPushButton("⚡ Start Queues Now (Parallel)")
        self.btn_proceed.setToolTip("Start parallel queues immediately without waiting for existing agents to finish")
        self.btn_proceed.setStyleSheet(
            f"QPushButton {{ background: {COLOR_SURFACE_2}; color: #38bdf8; border: 1px solid #0284c7; border-radius: 6px; font-size: 12px; font-weight: 700; padding: 8px 14px; }}"
            f"QPushButton:hover {{ background: #0c4a6e; color: #ffffff; }}"
        )
        self.btn_proceed.clicked.connect(self._on_proceed_clicked)
        btn_layout.addWidget(self.btn_proceed)

        self.btn_wait = QPushButton("⏳ Wait for Agents to Finish (Recommended)")
        self.btn_wait.setObjectName("btn-primary")
        self.btn_wait.setToolTip("Wait until 0 active agents remain in target project before launching parallel queues")
        self.btn_wait.setStyleSheet(
            "QPushButton { background: #0284c7; color: #ffffff; border: 1px solid #0369a1; border-radius: 6px; font-size: 12px; font-weight: 700; padding: 8px 16px; }"
            "QPushButton:hover { background: #0369a1; }"
        )
        self.btn_wait.clicked.connect(self._on_wait_clicked)
        btn_layout.addWidget(self.btn_wait)

        layout.addLayout(btn_layout)

        # Timer setup
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._on_timer_tick)
        self.timer.start()

    def _on_timer_tick(self):
        self.remaining_seconds -= 1
        if self.remaining_seconds <= 0:
            self.timer.stop()
            self.selected_action = "wait"
            self.accept()
        else:
            self.countdown_lbl.setText(f"⏳ Auto-defaulting to 'Wait for Agents' in {self.remaining_seconds}s (Unattended Mode)...")
            self.progress_bar.setValue(self.remaining_seconds)

    def _on_proceed_clicked(self):
        if hasattr(self, "timer") and self.timer:
            self.timer.stop()
        self.selected_action = "proceed"
        self.accept()

    def _on_wait_clicked(self):
        if hasattr(self, "timer") and self.timer:
            self.timer.stop()
        self.selected_action = "wait"
        self.accept()

    def reject(self):
        if hasattr(self, "timer") and self.timer:
            self.timer.stop()
        self.selected_action = "wait"
        super().reject()

    def get_selected_action(self) -> str:
        """Returns the chosen action ('wait' or 'proceed')."""
        return getattr(self, "selected_action", "wait")


class DuplicateMultipleDialog(QDialog):
    """Dialog for creating multiple copies of a queued prompt item."""

    def __init__(
        self,
        parent=None,
        prompt_item=None,
        default_count: int = 2,
    ):
        super().__init__(parent)
        self.setWindowTitle("Duplicate Multiple Prompts")
        self.resize(420, 240)
        self.setMinimumSize(380, 200)

        self.prompt_item = prompt_item

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Header Title & Subtitle
        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)
        title_lbl = QLabel("Duplicate Prompt Copies")
        title_lbl.setStyleSheet(f"font-weight: 700; font-size: 15px; color: {COLOR_TEXT_PRIMARY};")
        header_layout.addWidget(title_lbl)

        sub_lbl = QLabel("Create multiple identical copies of this prompt in the queue.")
        sub_lbl.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_MUTED};")
        header_layout.addWidget(sub_lbl)
        layout.addLayout(header_layout)

        # Prompt Summary Card
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background: {COLOR_SURFACE_2}; border: 1px solid {COLOR_BORDER_DEFAULT}; "
            f"border-radius: 8px; padding: 8px; }}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(4)

        prompt_text = ""
        project_name = "No Project"
        queue_name = "Default"
        if prompt_item:
            prompt_text = getattr(prompt_item, "text", "") or ""
            project_name = (getattr(prompt_item, "project", "") or "").strip() or "No Project"
            queue_name = (getattr(prompt_item, "queue_name", "") or "").strip() or "Default"

        snippet = prompt_text.replace("\n", " ").strip()
        if len(snippet) > 80:
            snippet = snippet[:77] + "..."
        if not snippet:
            snippet = "(Empty prompt)"

        snippet_lbl = QLabel(f"<b>Prompt:</b> {snippet}")
        snippet_lbl.setStyleSheet(f"font-size: 12px; color: {COLOR_TEXT_PRIMARY};")
        snippet_lbl.setWordWrap(True)
        card_layout.addWidget(snippet_lbl)

        meta_lbl = QLabel(f"Project: <span style='color: #38bdf8;'>{project_name}</span>  •  Queue: <span style='color: #34d399;'>{queue_name}</span>")
        meta_lbl.setStyleSheet(f"font-size: 11px; color: {COLOR_TEXT_MUTED};")
        card_layout.addWidget(meta_lbl)
        layout.addWidget(card)

        # Number of copies row
        count_layout = QHBoxLayout()
        count_layout.setSpacing(10)
        lbl_count = QLabel("Number of copies:")
        lbl_count.setStyleSheet(f"font-weight: 600; font-size: 12px; color: {COLOR_TEXT_PRIMARY};")
        count_layout.addWidget(lbl_count)

        self.spin_count = QSpinBox(self)
        self.spin_count.setRange(1, 500)
        self.spin_count.setValue(max(1, default_count))
        self.spin_count.setMinimumWidth(110)
        self.spin_count.setStyleSheet(
            f"QSpinBox {{ background: {COLOR_SURFACE_1}; color: {COLOR_TEXT_PRIMARY}; "
            f"border: 1px solid {COLOR_BORDER_DEFAULT}; border-radius: 6px; padding: 4px 8px; "
            f"font-size: 13px; font-weight: 600; }}"
            f"QSpinBox:focus {{ border-color: {COLOR_PRIMARY}; }}"
        )
        count_layout.addWidget(self.spin_count)
        count_layout.addStretch()
        layout.addLayout(count_layout)

        layout.addStretch()

        # Dialog Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.cancel_btn = QPushButton("Cancel", self)
        self.cancel_btn.setMinimumHeight(32)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {COLOR_TEXT_SECONDARY}; "
            f"border: 1px solid {COLOR_BORDER_DEFAULT}; border-radius: 6px; padding: 4px 16px; font-size: 12px; font-weight: 500; }}"
            f"QPushButton:hover {{ background: {COLOR_SURFACE_2}; color: {COLOR_TEXT_PRIMARY}; }}"
        )
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        btn_layout.addStretch()

        self.duplicate_btn = QPushButton("Duplicate", self)
        self.duplicate_btn.setMinimumHeight(32)
        self.duplicate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.duplicate_btn.setStyleSheet(
            f"QPushButton {{ background: {COLOR_PRIMARY}; color: #ffffff; "
            f"border: 1px solid {COLOR_PRIMARY}; border-radius: 6px; padding: 4px 18px; font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: #4f46e5; }}"
        )
        self.duplicate_btn.setDefault(True)
        self.duplicate_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.duplicate_btn)

        layout.addLayout(btn_layout)

        self.spin_count.valueChanged.connect(self._update_dup_btn_text)
        self._update_dup_btn_text(self.spin_count.value())

        # Auto-focus and select all so user can immediately type a number and press Enter
        self.spin_count.setFocus()
        self.spin_count.selectAll()

    def _update_dup_btn_text(self, val: int):
        self.duplicate_btn.setText(f"Duplicate ({val})")

    def get_count(self) -> int:
        return self.spin_count.value()


