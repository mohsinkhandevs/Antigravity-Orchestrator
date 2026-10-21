"""
Custom Reusable PyQt6 UI Components for Antigravity CDP Studio v2.0.
Includes ToggleSwitch, SegmentedControl, StatefulActionButton, FlowLayout, and ModePillWidget.
Designed to Linear, Raycast, and Microsoft Fluent 2 desktop standards.
"""

from typing import Dict, Any, Optional, List
from PyQt6.QtWidgets import (
    QWidget,
    QFrame,
    QPushButton,
    QButtonGroup,
    QHBoxLayout,
    QVBoxLayout,
    QLayout,
    QLayoutItem,
    QSizePolicy,
    QMenu,
    QApplication,
    QScrollArea,
    QLabel,
    QTableWidget,
)
from PyQt6.QtCore import (
    Qt,
    pyqtSignal,
    pyqtProperty,
    QPoint,
    QRect,
    QSize,
    QVariantAnimation,
    QEasingCurve,
)
from PyQt6.QtGui import (
    QPainter,
    QColor,
    QBrush,
    QPen,
    QFont,
    QIcon,
    QMouseEvent,
    QKeyEvent,
    QPainterPath,
)

from app.ui.styles import (
    COLOR_PRIMARY,
    COLOR_PRIMARY_HOVER,
    COLOR_PRIMARY_ACTIVE,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_DANGER,
    COLOR_SURFACE_1,
    COLOR_SURFACE_2,
    COLOR_SURFACE_HOVER,
    COLOR_SURFACE_ACTIVE,
    COLOR_BORDER_DEFAULT,
    COLOR_BORDER_STRONG,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_SECONDARY,
    COLOR_TEXT_MUTED,
)


class ToggleSwitch(QWidget):
    """
    Animated Fluent 2 / Linear caliber Toggle Switch widget.
    Smoothly interpolates thumb knob position and track fill.
    Keyboard accessible via Space / Enter, fully customizable size.
    """

    toggled = pyqtSignal(bool)

    def __init__(self, parent=None, checked: bool = False):
        super().__init__(parent)
        self._checked = bool(checked)
        self._track_width = 34
        self._track_height = 18
        self._thumb_radius = 6.5
        self._margin = 2.0

        # Thumb X offset: from left margin to right margin
        self._min_x = self._margin + self._thumb_radius
        self._max_x = self._track_width - self._margin - self._thumb_radius
        self._thumb_x = self._max_x if self._checked else self._min_x

        self.setFixedSize(self._track_width + 4, self._track_height + 4)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._anim.valueChanged.connect(self._on_anim_val_changed)

    def _on_anim_val_changed(self, val):
        self._thumb_x = float(val)
        self.update()

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool, animate: bool = True):
        checked = bool(checked)
        if self._checked == checked and self._thumb_x in (self._min_x, self._max_x):
            return
        self._checked = checked

        target_x = self._max_x if self._checked else self._min_x
        if animate and self.isVisible():
            self._anim.stop()
            self._anim.setStartValue(self._thumb_x)
            self._anim.setEndValue(target_x)
            self._anim.start()
        else:
            self._thumb_x = target_x
            self.update()

        self.toggled.emit(self._checked)

    def toggle(self):
        self.setChecked(not self._checked, animate=True)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle()
            event.accept()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.toggle()
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Center track inside widget
        x_offset = 2
        y_offset = 2
        track_rect = QRect(x_offset, y_offset, self._track_width, self._track_height)
        corner_r = self._track_height / 2.0

        # Colors based on state
        if self._checked:
            track_bg = QColor(COLOR_PRIMARY)
            track_border = QColor("#4f46e5")
            thumb_color = QColor("#ffffff")
        else:
            track_bg = QColor("#192238")
            track_border = QColor(COLOR_BORDER_DEFAULT)
            thumb_color = QColor("#94a3b8")

        # Focus ring indicator
        if self.hasFocus():
            focus_pen = QPen(QColor(COLOR_PRIMARY_HOVER), 1.5)
            painter.setPen(focus_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                QRect(0, 0, self.width() - 1, self.height() - 1),
                corner_r + 2,
                corner_r + 2,
            )

        # Draw track pill
        painter.setPen(QPen(track_border, 1.0))
        painter.setBrush(QBrush(track_bg))
        painter.drawRoundedRect(track_rect, corner_r, corner_r)

        # Draw smooth thumb circle
        center_x = x_offset + self._thumb_x
        center_y = y_offset + (self._track_height / 2.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(thumb_color))
        painter.drawEllipse(QPoint(int(center_x), int(center_y)), int(self._thumb_radius), int(self._thumb_radius))
        painter.end()


class SegmentedControl(QFrame):
    """
    Linear / Raycast caliber contiguous segmented pill toggle group.
    Encapsulates a styled container frame and an exclusive QButtonGroup.
    Emits sig_segment_changed(str) with the selected segment ID.
    """

    sig_segment_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("segmented-container")
        self._buttons: Dict[str, QPushButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.setSpacing(2)

    def add_segment(self, segment_id: str, text: str, icon: Optional[QIcon] = None) -> QPushButton:
        """Adds a new segment option to the control."""
        btn = QPushButton(text, self)
        btn.setObjectName("segmented-item")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if icon:
            btn.setIcon(icon)

        self._buttons[segment_id] = btn
        self._group.addButton(btn)
        self._layout.addWidget(btn)

        # Connect click to notify parent
        def on_toggled(checked: bool):
            if checked:
                self.sig_segment_changed.emit(segment_id)

        btn.toggled.connect(on_toggled)

        # Default check first item if none checked
        if len(self._buttons) == 1:
            btn.setChecked(True)

        return btn

    def set_current_id(self, segment_id: str):
        """Sets the active segment by its ID."""
        btn = self._buttons.get(segment_id)
        if btn and not btn.isChecked():
            btn.setChecked(True)

    def set_active_segment(self, segment_id: str):
        """Alias for set_current_id."""
        self.set_current_id(segment_id)

    def current_id(self) -> str:
        """Returns the ID of the currently selected segment."""
        for seg_id, btn in self._buttons.items():
            if btn.isChecked():
                return seg_id
        return ""

    def get_button(self, segment_id: str) -> Optional[QPushButton]:
        return self._buttons.get(segment_id)


class CallableState(str):
    """Subclass of str that is also callable, ensuring compatibility whether accessed as property or method."""
    def __call__(self) -> str:
        return str(self)


class StatefulActionButton(QPushButton):
    """
    Adaptive action button that dynamically swaps label, icon, and QSS variant
    based on operational state (e.g. IDLE -> RUNNING -> PAUSED).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._states: Dict[str, Dict[str, Any]] = {}
        self._current_state_key: str = ""

    def register_state(
        self,
        state_key: str,
        label: str,
        icon: str = "",
        variant: str = "secondary",
        tooltip: str = "",
    ):
        """Registers configuration for a named state."""
        self._states[state_key] = {
            "label": label,
            "icon": icon,
            "variant": variant,
            "tooltip": tooltip,
        }

    def set_state(self, state_key: str):
        """Applies a registered state configuration and repolishes QSS."""
        cfg = self._states.get(state_key)
        if not cfg:
            return
        self._current_state_key = state_key

        icon_str = cfg.get("icon", "")
        label_str = cfg.get("label", "")
        display_text = f"{icon_str}  {label_str}".strip() if icon_str else label_str
        self.setText(display_text)

        if cfg.get("tooltip"):
            self.setToolTip(cfg["tooltip"])

        variant = cfg.get("variant", "secondary")
        self.setProperty("variant", variant)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    @property
    def current_state(self) -> CallableState:
        return CallableState(self._current_state_key)


class FlowLayout(QLayout):
    """
    Standard Qt FlowLayout that lays out child items horizontally and wraps
    them onto the next line when width is constrained. Eliminates horizontal text clipping.
    """

    def __init__(self, parent=None, margin: int = 0, h_spacing: int = 6, v_spacing: int = 6):
        super().__init__(parent)
        self._item_list: List[QLayoutItem] = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)

    def addItem(self, item: QLayoutItem):
        self._item_list.append(item)

    def horizontalSpacing(self) -> int:
        return self._h_spacing

    def verticalSpacing(self) -> int:
        return self._v_spacing

    def count(self) -> int:
        return len(self._item_list)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:
        if 0 <= index < len(self._item_list):
            return self._item_list[index]
        return None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:
        if 0 <= index < len(self._item_list):
            return self._item_list.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._item_list:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool = False) -> int:
        m = self.contentsMargins()
        effective_rect = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective_rect.x()
        y = effective_rect.y()
        line_height = 0

        for item in self._item_list:
            wid = item.widget()
            if wid and not wid.isVisible():
                continue

            space_x = self._h_spacing
            space_y = self._v_spacing
            item_size = item.sizeHint()

            next_x = x + item_size.width() + space_x
            if next_x - space_x > effective_rect.right() and line_height > 0:
                x = effective_rect.x()
                y = y + line_height + space_y
                next_x = x + item_size.width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item_size))

            x = next_x
            line_height = max(line_height, item_size.height())

        return y + line_height - rect.y() + m.bottom()


class ModePillWidget(QWidget):
    """
    Lightweight, interactive table cell pill badge for Prompt Invocation Mode.
    Replaces bulky QComboBox dropdowns with a sleek Raycast-style badge that pops
    open a concise dark menu or rotates mode on click.
    """

    sig_mode_changed = pyqtSignal(str)

    def __init__(self, mode: str = "/goal", parent=None):
        super().__init__(parent)
        self._current_mode = mode or "/goal"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_pill = QPushButton(self)
        self.btn_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pill.setFixedHeight(24)
        self.btn_pill.clicked.connect(self._show_mode_menu)
        layout.addWidget(self.btn_pill)

        self.set_mode(self._current_mode, notify=False)

    def current_mode(self) -> str:
        return self._current_mode

    def set_mode(self, mode: str, notify: bool = False):
        m = (mode or "").lower().strip()
        if "/plan" in m:
            self._current_mode = "/plan"
            text = "📋  /plan  ▾"
            bg = "#0c4a6e"
            fg = "#38bdf8"
            border = "#0284c7"
            tip = "Mode: /plan (Architectural Planning)\nClick to change"
        elif "/goal" in m:
            self._current_mode = "/goal"
            text = "🎯  /goal  ▾"
            bg = "#1e1b4b"
            fg = "#a5b4fc"
            border = "#4338ca"
            tip = "Mode: /goal (Autonomous Goal Execution)\nClick to change"
        else:
            self._current_mode = "none"
            text = "💬  Plain  ▾"
            bg = "#182136"
            fg = "#cbd5e1"
            border = "#222e47"
            tip = "Mode: Plain Text (Standard prompt)\nClick to change"

        self.btn_pill.setText(text)
        self.btn_pill.setToolTip(tip)
        self.btn_pill.setStyleSheet(
            f"QPushButton {{ background-color: {bg}; color: {fg}; border: 1px solid {border}; "
            f"border-radius: 12px; padding: 2px 10px; font-size: 11px; font-weight: 700; min-width: 82px; }}"
            f"QPushButton:hover {{ border-color: #ffffff; }}"
        )

        if notify:
            self.sig_mode_changed.emit(self._current_mode)

    def _show_mode_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background-color: {COLOR_SURFACE_1}; color: {COLOR_TEXT_PRIMARY}; "
            f"border: 1px solid {COLOR_BORDER_STRONG}; border-radius: 6px; padding: 4px; font-size: 12px; }}"
            f"QMenu::item {{ padding: 6px 14px; border-radius: 4px; }}"
            f"QMenu::item:selected {{ background-color: {COLOR_SURFACE_ACTIVE}; color: #ffffff; }}"
        )
        act_goal = menu.addAction("🎯  /goal (Autonomous Goal)")
        act_plan = menu.addAction("📋  /plan (Architectural Plan)")
        act_plain = menu.addAction("💬  Plain (Standard Prompt)")

        pos = self.btn_pill.mapToGlobal(QPoint(0, self.btn_pill.height() + 2))
        selected_act = menu.exec(pos)

        if selected_act == act_goal:
            self.set_mode("/goal", notify=True)
        elif selected_act == act_plan:
            self.set_mode("/plan", notify=True)
        elif selected_act == act_plain:
            self.set_mode("none", notify=True)


class ResponsiveScrollContainer(QScrollArea):
    """
    Fluid, borderless scroll container that lets pages scroll smoothly
    while child cards intelligently expand or compact based on viewport geometry.
    """

    def __init__(self, parent=None, margin_h: int = 12, margin_v: int = 10, spacing: int = 8):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.content_widget = QWidget(self)
        self.content_widget.setStyleSheet("background: transparent;")
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(margin_h, margin_v, margin_h, margin_v)
        self.content_layout.setSpacing(spacing)
        self.setWidget(self.content_widget)

    def add_card(self, widget: QWidget, stretch: int = 0):
        self.content_layout.addWidget(widget, stretch)

    def add_layout(self, layout: QLayout, stretch: int = 0):
        self.content_layout.addLayout(layout, stretch)

    def add_stretch(self, stretch: int = 1):
        self.content_layout.addStretch(stretch)


class ScrollableTableWidget(QTableWidget):
    """
    QTableWidget that seamlessly bubbles wheel events up to the parent QScrollArea
    when its vertical scrollbar is not needed or hits top/bottom boundaries,
    preventing user mouse wheel trapping in nested layouts.
    """

    def wheelEvent(self, event):
        v_bar = self.verticalScrollBar()
        if not v_bar.isVisible() or v_bar.maximum() == 0:
            event.ignore()
        else:
            delta = event.angleDelta().y()
            if (delta > 0 and v_bar.value() == 0) or (delta < 0 and v_bar.value() == v_bar.maximum()):
                event.ignore()
            else:
                super().wheelEvent(event)


