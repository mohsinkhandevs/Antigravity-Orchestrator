import os
import time
from datetime import datetime
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QTextEdit,
    QFrame,
    QApplication,
    QMenu,
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QRectF, QTimer
from PyQt6.QtGui import (
    QTextCursor,
    QPainter,
    QPixmap,
    QImage,
    QIcon,
    QColor,
    QBrush,
    QPen,
    QFont,
    QConicalGradient,
)
from PyQt6.QtSvg import QSvgRenderer

from app.config import resolve_asset_path


def get_mini_hud_svg_path() -> str:
    """Finds and resolves the mini HUD logo SVG file."""
    # 1. Local project assets directory
    local_asset = resolve_asset_path("automation-hud-mini.svg")
    if os.path.exists(local_asset):
        return local_asset

    # 2. User Downloads directory as requested
    dl_path = r"C:\Users\mohsi\Downloads\automation-hud-mini.svg"
    if os.path.exists(dl_path):
        return dl_path

    return ""


def get_mini_hud_icon(size: int = 64) -> QIcon:
    """Renders the mini HUD SVG into a crisp high-DPI QIcon suitable for windows and system tray."""
    svg_path = get_mini_hud_svg_path()
    if svg_path and os.path.exists(svg_path):
        try:
            renderer = QSvgRenderer(svg_path)
            if renderer.isValid():
                icon = QIcon()
                for sz in [16, 24, 32, 48, 64, 128, 256]:
                    img = QImage(sz, sz, QImage.Format.Format_ARGB32_Premultiplied)
                    img.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(img)
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                    renderer.render(painter, QRectF(0, 0, sz, sz))
                    painter.end()
                    icon.addPixmap(QPixmap.fromImage(img))
                return icon
        except Exception:
            pass
    return QIcon()


class MiniStatusFlyout(QWidget):
    """
    Sleek, instantaneous floating status badge appearing on hover over the mini HUD logo,
    styled like the Windows 11 system battery/charging flyout card.
    """

    def __init__(self, parent=None):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.frame = QFrame(self)
        self.frame.setObjectName("mini-status-flyout-frame")
        self._apply_style(is_paused=False)

        f_layout = QVBoxLayout(self.frame)
        f_layout.setContentsMargins(10, 8, 10, 8)
        f_layout.setSpacing(3)

        self.lbl_status = QLabel("⚡ RUNNING • 0.0s", self.frame)
        self.lbl_status.setStyleSheet("font-weight: 700; font-size: 11px; color: #38bdf8;")

        self.lbl_step = QLabel("Step: Initializing...", self.frame)
        self.lbl_step.setStyleSheet("font-size: 10px; color: #e2e8f0; font-weight: 500;")

        self.lbl_hint = QLabel("Click: Pause  |  2x Click: Open HUD", self.frame)
        self.lbl_hint.setStyleSheet("font-size: 9px; color: #64748b;")

        f_layout.addWidget(self.lbl_status)
        f_layout.addWidget(self.lbl_step)
        f_layout.addWidget(self.lbl_hint)

        layout.addWidget(self.frame)

    def _apply_style(self, is_paused: bool):
        if is_paused:
            self.frame.setStyleSheet(
                """
                #mini-status-flyout-frame {
                    background-color: rgba(22, 18, 10, 0.96);
                    border: 1px solid #d97706;
                    border-radius: 8px;
                }
                """
            )
        else:
            self.frame.setStyleSheet(
                """
                #mini-status-flyout-frame {
                    background-color: rgba(13, 18, 30, 0.96);
                    border: 1px solid #0284c7;
                    border-radius: 8px;
                }
                """
            )

    def update_data(self, is_paused: bool, step: str, timer: str, is_stabilizing: bool = False, remaining: float = 0.0):
        self._apply_style(is_paused)
        if is_stabilizing and remaining > 0:
            rem_str = f"{int(round(remaining))}s"
            if is_paused:
                self.lbl_status.setText(f"⏸ PAUSED (STABILIZING) • {rem_str} left")
                self.lbl_status.setStyleSheet("font-weight: 700; font-size: 11px; color: #fbbf24;")
                self.lbl_hint.setText("Click: Resume  |  2x Click: Open HUD")
            else:
                self.lbl_status.setText(f"⏳ STABILIZING • {rem_str} left")
                self.lbl_status.setStyleSheet("font-weight: 700; font-size: 11px; color: #38bdf8;")
                self.lbl_hint.setText("Click: Pause  |  2x Click: Open HUD")
            self.lbl_step.setText("Step: Verifying completion (Send steady)")
        elif is_paused:
            self.lbl_status.setText(f"⏸ PAUSED • {timer}")
            self.lbl_status.setStyleSheet("font-weight: 700; font-size: 11px; color: #fbbf24;")
            self.lbl_hint.setText("Click: Resume  |  2x Click: Open HUD")
            self.lbl_step.setText(f"Step: {step}")
        else:
            self.lbl_status.setText(f"⚡ RUNNING • {timer}")
            self.lbl_status.setStyleSheet("font-weight: 700; font-size: 11px; color: #38bdf8;")
            self.lbl_hint.setText("Click: Pause  |  2x Click: Open HUD")
            self.lbl_step.setText(f"Step: {step}")

        self.adjustSize()

    def show_near_mini(self, mini_widget: QWidget):
        mini_rect = mini_widget.frameGeometry()
        self.adjustSize()
        w = max(self.width(), 190)
        h = max(self.height(), 58)

        screen = QApplication.screenAt(mini_widget.pos()) or QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
        else:
            geom = mini_rect

        # Horizontal centering over mini widget
        target_x = mini_rect.center().x() - (w // 2)
        target_x = max(geom.left() + 8, min(target_x, geom.right() - w - 8))

        # Vertical: prefer 6px above the mini widget; fallback below if too close to screen top
        target_y = mini_rect.top() - h - 6
        if target_y < geom.top() + 8:
            target_y = mini_rect.bottom() + 6

        self.move(target_x, target_y)
        self.show()
        self.raise_()


class FloatingHudMiniWidget(QWidget):
    """
    Compact, draggable, circular floating mini-widget displaying the custom SVG logo.
    Replaces the full HUD when minimized.
    - Dynamic RGB moving light ring when automation is running.
    - Exact RGB ring freezes static in place when paused.
    - When stabilization timer starts, SVG logo disappears and displays live remaining
      seconds on a crisp high-contrast background directly inside the circle.
    - Single-click: Toggles Pause / Resume.
    - Double-click: Opens / restores the full HUD overlay.
    - Hover: Shows live status mini text box (like the OS battery charging flyout).
    """

    sig_restore_hud = pyqtSignal()
    sig_restore_main_window = pyqtSignal()
    sig_pause_clicked = pyqtSignal()
    sig_resume_clicked = pyqtSignal()
    sig_stop_clicked = pyqtSignal()
    sig_skip_clicked = pyqtSignal()
    sig_exit_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(None)  # Top-level frameless window
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("Antigravity Mini HUD")
        self.setFixedSize(54, 54)

        self._drag_pos = QPoint()
        self._press_pos = QPoint()
        self._is_dragging = False
        self._ignore_next_release = False
        self._current_step = "Initializing..."
        self._current_timer = "0.0s"
        self.is_paused = False
        self._is_running = False

        # Stabilization timer state
        self._is_stabilizing = False
        self._stabilization_remaining = 0.0
        self._stabilization_total = 0.0

        # Dynamic RGB Moving Light state & animation timer
        self._rgb_angle = 0.0
        self._rgb_timer = QTimer(self)
        self._rgb_timer.setInterval(33)  # ~30 FPS smooth rotation
        self._rgb_timer.timeout.connect(self._rotate_rgb)

        # Timer to distinguish single-click (pause/resume) from double-click (restore HUD)
        self._single_click_timer = QTimer(self)
        self._single_click_timer.setSingleShot(True)
        self._single_click_timer.timeout.connect(self._handle_single_click)

        # Hover mini status text box (like battery flyout)
        self.status_flyout = MiniStatusFlyout()

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.container = QFrame(self)
        self.container.setObjectName("hud-mini-container")
        self.container.setStyleSheet(
            """
            #hud-mini-container {
                background: transparent;
                border: none;
            }
            """
        )

        c_layout = QVBoxLayout(self.container)
        c_layout.setContentsMargins(5, 5, 5, 5)
        c_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_lbl = QLabel(self.container)
        self.icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_lbl.setStyleSheet("background: transparent; border: none;")
        self._load_svg_icon()
        c_layout.addWidget(self.icon_lbl)

        layout.addWidget(self.container)

        # Center Pause Badge Overlay (instantly unmistakable pause indicator)
        self.pause_overlay = QLabel(self)
        self.pause_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.pause_overlay.setStyleSheet("background: transparent; border: none;")
        self.pause_overlay.setFixedSize(26, 26)
        self.pause_overlay.move(14, 14)
        self._setup_pause_badge()
        self.pause_overlay.hide()

        self._update_tooltip()

    def _setup_pause_badge(self):
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        scale = max(dpr, 3.0)
        bw, bh = int(round(26 * scale)), int(round(26 * scale))
        img = QImage(bw, bh, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        badge_r = QRectF(1.5 * scale, 1.5 * scale, (26 - 3.0) * scale, (26 - 3.0) * scale)
        p.setBrush(QColor(15, 23, 42, 240))
        p.setPen(QPen(QColor(245, 158, 11), 1.6 * scale))
        p.drawRoundedRect(badge_r, 7 * scale, 7 * scale)

        # Pause bars
        p.setBrush(QColor(245, 158, 11))
        p.setPen(Qt.PenStyle.NoPen)
        bar_w = 2.8 * scale
        bar_h = 10.5 * scale
        p.drawRoundedRect(QRectF(badge_r.center().x() - 3.8 * scale, badge_r.center().y() - bar_h / 2, bar_w, bar_h), 1.2 * scale, 1.2 * scale)
        p.drawRoundedRect(QRectF(badge_r.center().x() + 1.0 * scale, badge_r.center().y() - bar_h / 2, bar_w, bar_h), 1.2 * scale, 1.2 * scale)
        p.end()

        pix = QPixmap.fromImage(img)
        pix.setDevicePixelRatio(scale)
        self.pause_overlay.setPixmap(pix)

    def _load_svg_icon(self):
        svg_path = get_mini_hud_svg_path()
        if svg_path and os.path.exists(svg_path):
            try:
                renderer = QSvgRenderer(svg_path)
                if renderer.isValid():
                    dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
                    scale = max(dpr, 3.0)
                    target_size = 40
                    px_size = int(round(target_size * scale))
                    img = QImage(px_size, px_size, QImage.Format.Format_ARGB32_Premultiplied)
                    img.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(img)
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                    renderer.render(painter, QRectF(0, 0, px_size, px_size))
                    painter.end()
                    pixmap = QPixmap.fromImage(img)
                    pixmap.setDevicePixelRatio(scale)
                    self.icon_lbl.setPixmap(pixmap)
                    return
            except Exception:
                pass

        # Fallback text icon if SVG fails to load
        self.icon_lbl.setText("🤖")
        self.icon_lbl.setStyleSheet("font-size: 20px; background: transparent;")

    def _rotate_rgb(self):
        """Advances the conical gradient angle to produce smooth RGB moving light."""
        self._rgb_angle = (self._rgb_angle + 3.5) % 360.0
        self.update()

    def set_stabilization_progress(self, remaining: float, total: float):
        """
        Updates live stabilization countdown.
        When active (> 0), makes SVG logo disappear and displays remaining seconds
        on a crisp white background inside the circular widget.
        When finished (<= 0), restores the SVG logo.
        """
        if remaining > 0:
            self._is_stabilizing = True
            self._stabilization_remaining = remaining
            self._stabilization_total = total
            if hasattr(self, "icon_lbl") and self.icon_lbl:
                self.icon_lbl.hide()
            if hasattr(self, "pause_overlay") and self.pause_overlay:
                self.pause_overlay.hide()
        else:
            self._is_stabilizing = False
            self._stabilization_remaining = 0.0
            self._stabilization_total = 0.0
            if hasattr(self, "icon_lbl") and self.icon_lbl:
                self.icon_lbl.show()
            if hasattr(self, "pause_overlay") and self.pause_overlay:
                if self.is_paused:
                    self.pause_overlay.show()
                    self.pause_overlay.raise_()
                else:
                    self.pause_overlay.hide()

        self._update_tooltip()
        if self.status_flyout.isVisible():
            self.status_flyout.update_data(
                self.is_paused,
                self._current_step,
                self._current_timer,
                self._is_stabilizing,
                self._stabilization_remaining,
            )
        self.update()

    def paintEvent(self, event):
        """
        Renders the circular mini HUD:
        - Inner circle: Crisp off-white light theme (consistent across running, idle, and stabilizing).
        - Outer border: 360° rotating RGB rainbow gradient when running; solid glowing amber when paused.
        - Center: Bold dark countdown digits (e.g. 45s) when stabilizing.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # Clear transparent buffer to avoid artifacts in translucent window
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.restore()

        w = self.width()
        h = self.height()
        # Ring center and rect: 3px margin to accommodate 3.6px pen inside 54x54
        rect = QRectF(3.0, 3.0, w - 6.0, h - 6.0)
        center = rect.center()

        # 1. Inner Circle Background (Light Theme)
        if self._is_stabilizing and self._stabilization_remaining > 0:
            # Clean contrasting background for timer digits
            painter.setBrush(QColor(248, 250, 252, 245))
        else:
            if self.is_paused:
                painter.setBrush(QColor(255, 251, 235, 245))  # Warm amber tint on pause
            else:
                painter.setBrush(QColor(248, 250, 252, 245))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(rect)

        # 2. Border Ring (RGB Moving Light on Running, Solid Glowing Amber on Pause)
        pen_width = 3.6
        if self.is_paused:
            # Solid Glowing Amber ring - instantly unmistakable to human eye!
            ring_pen = QPen(QColor(245, 158, 11), pen_width)
        elif self._rgb_timer.isActive() or self._is_running:
            # Running: animated rotating RGB rainbow
            gradient = QConicalGradient(center, self._rgb_angle)
            gradient.setColorAt(0.00, QColor(255, 30, 80))    # Vibrant Pink/Red
            gradient.setColorAt(0.15, QColor(255, 130, 0))   # Vivid Orange
            gradient.setColorAt(0.30, QColor(250, 220, 0))   # Warm Yellow
            gradient.setColorAt(0.48, QColor(16, 220, 110))  # Spring Emerald
            gradient.setColorAt(0.65, QColor(6, 182, 240))   # Electric Cyan
            gradient.setColorAt(0.82, QColor(145, 60, 255))  # Neon Violet
            gradient.setColorAt(1.00, QColor(255, 30, 80))   # Wrap to Red
            ring_pen = QPen(QBrush(gradient), pen_width)
        else:
            # Idle: calm cyan border
            ring_pen = QPen(QColor(56, 189, 248), pen_width)

        painter.setPen(ring_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)

        # 3. Live Stabilization Remaining Seconds (drawn in center over white background)
        if self._is_stabilizing and self._stabilization_remaining > 0:
            painter.setPen(QColor(15, 23, 42))  # Deep slate / charcoal for contrast
            font = QFont("Segoe UI", 13, QFont.Weight.Bold)
            font.setStyleHint(QFont.StyleHint.SansSerif)
            painter.setFont(font)
            sec_val = int(round(self._stabilization_remaining))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{sec_val}s")

        painter.end()

    def _update_tooltip(self):
        if self._is_stabilizing and self._stabilization_remaining > 0:
            status_text = f"STABILIZING ({int(round(self._stabilization_remaining))}s left)"
        else:
            status_text = "PAUSED" if self.is_paused else ("RUNNING" if self._is_running else "IDLE")

        self.setToolTip(
            f"Antigravity Live HUD ({status_text})\n"
            f"• Step: {self._current_step}\n"
            f"• Timer: {self._current_timer}\n\n"
            f"👉 Single-click: {'Resume' if self.is_paused else 'Pause'}\n"
            f"👉 Double-click: Open HUD\n"
            f"👉 Drag to reposition"
        )

    def enterEvent(self, event):
        super().enterEvent(event)
        self._show_status_flyout()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hide_status_flyout()

    def _show_status_flyout(self):
        if not self._is_dragging:
            self.status_flyout.update_data(
                self.is_paused,
                self._current_step,
                self._current_timer,
                self._is_stabilizing,
                self._stabilization_remaining,
            )
            self.status_flyout.show_near_mini(self)

    def _hide_status_flyout(self):
        self.status_flyout.hide()

    def set_step(self, step_title: str):
        self._current_step = step_title
        self._update_tooltip()
        if self.status_flyout.isVisible():
            self.status_flyout.update_data(
                self.is_paused,
                self._current_step,
                self._current_timer,
                self._is_stabilizing,
                self._stabilization_remaining,
            )

    def set_timer(self, timer_str: str):
        self._current_timer = timer_str
        self._update_tooltip()
        if self.status_flyout.isVisible():
            self.status_flyout.update_data(
                self.is_paused,
                self._current_step,
                self._current_timer,
                self._is_stabilizing,
                self._stabilization_remaining,
            )

    def set_running_state(self, state: str):
        if state == "RUNNING":
            self.is_paused = False
            self._is_running = True
            if hasattr(self, "pause_overlay") and self.pause_overlay:
                self.pause_overlay.hide()
            if not self._rgb_timer.isActive():
                self._rgb_timer.start()
        elif state == "PAUSED":
            self.is_paused = True
            self._is_running = False
            self._rgb_timer.stop()
            if hasattr(self, "pause_overlay") and self.pause_overlay and not self._is_stabilizing:
                self.pause_overlay.show()
                self.pause_overlay.raise_()
        else:  # IDLE or STOPPED
            self.is_paused = False
            self._is_running = False
            self._rgb_timer.stop()
            self._is_stabilizing = False
            if hasattr(self, "pause_overlay") and self.pause_overlay:
                self.pause_overlay.hide()
            if hasattr(self, "icon_lbl") and self.icon_lbl:
                self.icon_lbl.show()

        self._update_tooltip()
        if self.status_flyout.isVisible():
            self.status_flyout.update_data(
                self.is_paused,
                self._current_step,
                self._current_timer,
                self._is_stabilizing,
                self._stabilization_remaining,
            )
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._is_dragging = False
            self._hide_status_flyout()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            cur_pos = event.globalPosition().toPoint()
            if (cur_pos - self._press_pos).manhattanLength() > 4:
                if not self._is_dragging:
                    self._is_dragging = True
                    self._single_click_timer.stop()
                    self._hide_status_flyout()
                self.move(cur_pos - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self._ignore_next_release:
                self._ignore_next_release = False
                event.accept()
                return

            if not self._is_dragging:
                # Wait for 260ms to differentiate single click from double click
                self._single_click_timer.start(260)
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Cancel single click pause/resume
            self._single_click_timer.stop()
            self._ignore_next_release = True
            self._hide_status_flyout()
            # Double click restores the full HUD!
            self.sig_restore_hud.emit()
            event.accept()

    def _handle_single_click(self):
        # Single click toggles pause or resume!
        if self.is_paused:
            self.sig_resume_clicked.emit()
        else:
            self.sig_pause_clicked.emit()

        if self.status_flyout.isVisible():
            self.status_flyout.update_data(
                self.is_paused,
                self._current_step,
                self._current_timer,
                self._is_stabilizing,
                self._stabilization_remaining,
            )

    def hide(self):
        self._hide_status_flyout()
        return super().hide()

    def close(self):
        self._rgb_timer.stop()
        self._single_click_timer.stop()
        self._hide_status_flyout()
        self.status_flyout.close()
        return super().close()

    def show_at_default_position(self):
        """Shows the mini widget, positioning it at top-right if not already positioned."""
        if self.pos() == QPoint(0, 0):
            screen = QApplication.primaryScreen()
            if screen:
                geom = screen.availableGeometry()
                x = geom.right() - self.width() - 24
                y = geom.top() + 48
                self.move(x, y)
        self.show()
        self.raise_()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: #111827; border: 1px solid #1f293d; color: #f1f5f9; padding: 4px; }"
            "QMenu::item:selected { background-color: #38bdf8; color: #0f172a; font-weight: bold; border-radius: 4px; }"
        )
        act_open = menu.addAction("🖥  Open Studio Window")
        act_restore = menu.addAction("🗖  Expand Full HUD (Double-Click)")
        menu.addSeparator()
        act_pause = menu.addAction("▶  Resume (Single-Click)" if self.is_paused else "⏸  Pause (Single-Click)")
        act_skip = menu.addAction("⏭  Skip Current Prompt")
        act_stop = menu.addAction("⏹  Emergency Stop")
        menu.addSeparator()
        act_hide = menu.addAction("✕  Hide Mini Logo")
        act_exit = menu.addAction("🚪  Exit Application")

        action = menu.exec(event.globalPos())
        if action == act_open:
            self.sig_restore_main_window.emit()
        elif action == act_restore:
            self.sig_restore_hud.emit()
        elif action == act_pause:
            if self.is_paused:
                self.sig_resume_clicked.emit()
            else:
                self.sig_pause_clicked.emit()
        elif action == act_skip:
            self.sig_skip_clicked.emit()
        elif action == act_stop:
            self.sig_stop_clicked.emit()
        elif action == act_hide:
            self.hide()
        elif action == act_exit:
            self.sig_exit_clicked.emit()


class OneLineOverlayWidget(QWidget):
    """
    Sleek, always-on-top, single-line horizontal status overlay pill.
    Displays in real-time what the automation is doing, live timers,
    and highlights immediately whenever a step is waiting, stuck, or retrying.

    Features:
    - Glassmorphism dark pill with dynamic status badge (RUNNING, STABILIZING, WAITING/STUCK, PAUSED, DONE).
    - Real-time action text (current step description or stuck reason).
    - Monospace elapsed / remaining timer pill.
    - Draggable anywhere on screen (remembers position, defaults to top-center).
    - Quick action buttons (Pause/Resume, Studio Window, Close).
    - Right-click context menu (Pause, Skip, Stop, Open Studio, Expand Full HUD, Toggle Click-Through, Hide).
    - Auto stuck/waiting detection: identifies when a step takes unusually long (>15s) or logs retry notices.
    """

    sig_pause_clicked = pyqtSignal()
    sig_resume_clicked = pyqtSignal()
    sig_stop_clicked = pyqtSignal()
    sig_skip_clicked = pyqtSignal()
    sig_restore_main_window = pyqtSignal()
    sig_restore_hud = pyqtSignal()
    sig_exit_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("Antigravity Status Overlay")
        self.setFixedHeight(38)
        self.setMinimumWidth(480)
        self.setMaximumWidth(800)

        self._drag_pos = QPoint()
        self._press_pos = QPoint()
        self._is_dragging = False

        self._current_step = "Initializing..."
        self._current_timer = "0.0s"
        self._running_state = "IDLE"
        self.is_paused = False
        self._is_stabilizing = False
        self._stabilization_remaining = 0.0
        self._is_stuck_or_waiting = False
        self._stuck_reason = ""
        self._click_through = False
        self._last_step_start = time.time()

        # Auto-detect stuck/waiting timer (checks every 1s)
        self._stuck_monitor_timer = QTimer(self)
        self._stuck_monitor_timer.setInterval(1000)
        self._stuck_monitor_timer.timeout.connect(self._check_stuck_condition)
        self._stuck_monitor_timer.start()

        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.frame = QFrame(self)
        self.frame.setObjectName("overlay-bar-frame")
        layout.addWidget(self.frame)

        f_layout = QHBoxLayout(self.frame)
        f_layout.setContentsMargins(12, 4, 10, 4)
        f_layout.setSpacing(8)

        # 1. Status Badge
        self.lbl_status = QLabel("⚡ RUNNING", self.frame)
        self.lbl_status.setObjectName("lbl-status")
        f_layout.addWidget(self.lbl_status)

        # 2. Timer Pill
        self.lbl_timer = QLabel("0.0s", self.frame)
        self.lbl_timer.setObjectName("lbl-timer")
        f_layout.addWidget(self.lbl_timer)

        # 3. Action / What it's doing / Where stuck label
        self.lbl_action = QLabel("Initializing automation...", self.frame)
        self.lbl_action.setObjectName("lbl-action")
        self.lbl_action.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        f_layout.addWidget(self.lbl_action, 1)

        # 4. Quick Mini Action Controls
        self.btn_pause = QPushButton("⏸", self.frame)
        self.btn_pause.setFixedSize(22, 22)
        self.btn_pause.setToolTip("Pause / Resume Automation")
        self.btn_pause.clicked.connect(self._toggle_pause)
        f_layout.addWidget(self.btn_pause)

        self.btn_expand = QPushButton("🗖", self.frame)
        self.btn_expand.setFixedSize(22, 22)
        self.btn_expand.setToolTip("Open Full Live HUD / Studio")
        self.btn_expand.clicked.connect(self.sig_restore_hud.emit)
        f_layout.addWidget(self.btn_expand)

        self.btn_close = QPushButton("✕", self.frame)
        self.btn_close.setFixedSize(22, 22)
        self.btn_close.setToolTip("Hide Overlay Bar")
        self.btn_close.clicked.connect(self.hide)
        f_layout.addWidget(self.btn_close)

        self._apply_style()

    def _apply_style(self):
        if self._is_stuck_or_waiting:
            border_color = "#f59e0b"
            bg_color = "rgba(25, 20, 10, 0.95)"
            badge_bg = "rgba(180, 83, 9, 0.35)"
            badge_fg = "#fbbf24"
            badge_border = "#f59e0b"
            status_text = "⚠️ WAITING"
        elif self._is_stabilizing:
            border_color = "#d97706"
            bg_color = "rgba(22, 18, 12, 0.95)"
            badge_bg = "rgba(217, 119, 6, 0.3)"
            badge_fg = "#fbbf24"
            badge_border = "#d97706"
            status_text = "⏳ STABILIZING"
        elif self.is_paused:
            border_color = "#eab308"
            bg_color = "rgba(24, 20, 10, 0.95)"
            badge_bg = "rgba(161, 98, 7, 0.25)"
            badge_fg = "#fde047"
            badge_border = "#a16207"
            status_text = "⏸ PAUSED"
        elif self._running_state == "RUNNING":
            border_color = "#0284c7"
            bg_color = "rgba(10, 16, 28, 0.95)"
            badge_bg = "rgba(2, 132, 199, 0.25)"
            badge_fg = "#38bdf8"
            badge_border = "#0284c7"
            status_text = "⚡ RUNNING"
        elif self._running_state == "COMPLETED":
            border_color = "#059669"
            bg_color = "rgba(10, 24, 18, 0.95)"
            badge_bg = "rgba(5, 150, 105, 0.25)"
            badge_fg = "#34d399"
            badge_border = "#059669"
            status_text = "✔ DONE"
        else:
            border_color = "#334155"
            bg_color = "rgba(15, 23, 42, 0.95)"
            badge_bg = "rgba(71, 85, 105, 0.25)"
            badge_fg = "#94a3b8"
            badge_border = "#475569"
            status_text = "⏹ IDLE"

        self.lbl_status.setText(status_text)
        self.frame.setStyleSheet(
            f"""
            #overlay-bar-frame {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 18px;
            }}
            #lbl-status {{
                color: {badge_fg};
                background-color: {badge_bg};
                border: 1px solid {badge_border};
                border-radius: 10px;
                padding: 2px 8px;
                font-weight: 700;
                font-size: 10px;
                letter-spacing: 0.5px;
            }}
            #lbl-timer {{
                color: #cbd5e1;
                background-color: rgba(30, 41, 59, 0.75);
                border: 1px solid #334155;
                border-radius: 9px;
                padding: 2px 7px;
                font-family: 'Consolas', 'Segoe UI Monospace', monospace;
                font-size: 10px;
                font-weight: 600;
            }}
            #lbl-action {{
                color: #f8fafc;
                font-size: 11px;
                font-weight: 500;
            }}
            QPushButton {{
                background-color: rgba(30, 41, 59, 0.8);
                color: #cbd5e1;
                border: 1px solid #334155;
                border-radius: 11px;
                font-size: 10px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #38bdf8;
                color: #0f172a;
                border-color: #38bdf8;
            }}
            """
        )

    def set_step(self, step_title: str):
        self._current_step = step_title
        self._last_step_start = time.time()
        self._is_stuck_or_waiting = False
        self._stuck_reason = ""
        self._update_display()

    def set_timer(self, timer_val):
        if isinstance(timer_val, (int, float)):
            sec = float(timer_val)
            if sec >= 3600:
                h = int(sec // 3600)
                m = int((sec % 3600) // 60)
                s = int(sec % 60)
                timer_str = f"{h}h {m}m {s}s"
            elif sec >= 60:
                m = int(sec // 60)
                s = int(sec % 60)
                timer_str = f"{m}m {s}s"
            else:
                timer_str = f"{sec:.1f}s"
        else:
            timer_str = str(timer_val)

        self._current_timer = timer_str
        self._update_display()

    def set_stabilization_progress(self, remaining: float, total: float):
        if remaining > 0:
            self._is_stabilizing = True
            self._stabilization_remaining = remaining
            self._is_stuck_or_waiting = False
            self.lbl_timer.setText(f"⏳ {int(round(remaining))}s left")
            self.lbl_action.setText(f"Verifying completion (Send steady, {int(round(remaining))}s remaining)...")
            self._apply_style()
            if not self.isVisible():
                self.show_at_default_position()
        else:
            self._is_stabilizing = False
            self._update_display()

    def set_running_state(self, state: str):
        if state == "RUNNING":
            self.is_paused = False
            self._running_state = "RUNNING"
            self.btn_pause.setText("⏸")
        elif state == "PAUSED":
            self.is_paused = True
            self._running_state = "PAUSED"
            self.btn_pause.setText("▶")
        elif state == "COMPLETED":
            self.is_paused = False
            self._running_state = "COMPLETED"
            self.btn_pause.setText("⏸")
            self.lbl_action.setText("Automation finished cleanly ✔")
        else:
            self.is_paused = False
            self._running_state = state
            self.btn_pause.setText("⏸")

        self._apply_style()

    def append_log(self, level: str, message: str):
        lvl = level.upper()
        msg_lower = message.lower()
        stuck_keywords = [
            "could not find",
            "not found",
            "retrying",
            "waiting for",
            "stuck",
            "timed out",
            "timeout",
            "quota below",
            "attempting",
            "failed to locate",
            "unresponsive",
        ]
        if lvl in ("WARN", "ERROR") or any(k in msg_lower for k in stuck_keywords):
            clean_msg = message.replace("\n", " ").strip()
            if len(clean_msg) > 75:
                clean_msg = clean_msg[:72] + "..."
            self._is_stuck_or_waiting = True
            self._stuck_reason = clean_msg
            self._update_display()

    def set_stuck_status(self, is_stuck: bool, reason: str = ""):
        self._is_stuck_or_waiting = is_stuck
        self._stuck_reason = reason
        self._update_display()

    def _check_stuck_condition(self):
        if self._running_state == "RUNNING" and not self.is_paused and not self._is_stabilizing:
            elapsed = time.time() - self._last_step_start
            if elapsed > 15.0 and not self._is_stuck_or_waiting:
                self._is_stuck_or_waiting = True
                self._stuck_reason = f"Waiting on: {self._current_step} ({int(elapsed)}s elapsed)"
                self._update_display()

    def _update_display(self):
        if self._is_stabilizing and self._stabilization_remaining > 0:
            self._apply_style()
            return

        if self._is_stuck_or_waiting and self._stuck_reason:
            self.lbl_action.setText(self._stuck_reason)
            self.setToolTip(f"⚠️ Status Alert: {self._stuck_reason}")
        else:
            self.lbl_action.setText(self._current_step)
            self.setToolTip(f"Status: {self._current_step} [{self._current_timer}]")

        self.lbl_timer.setText(self._current_timer)
        self._apply_style()
        self.adjustSize()

    def _toggle_pause(self):
        if self.is_paused:
            self.sig_resume_clicked.emit()
        else:
            self.sig_pause_clicked.emit()

    def toggle_click_through(self, enable: bool = None):
        if enable is None:
            enable = not self._click_through
        self._click_through = enable
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, enable)
        if enable:
            self.setToolTip("Status Overlay (Click-Through ON. Use Tray to disable)")
        else:
            self.setToolTip("Status Overlay (Click-Through OFF)")

    def show_at_default_position(self):
        """Shows the overlay bar centered near the top of the primary screen."""
        self.adjustSize()
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            w = max(self.width(), 520)
            target_x = geom.center().x() - (w // 2)
            target_y = geom.top() + 10
            self.move(target_x, target_y)
        self.show()
        self.raise_()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._is_dragging = False
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            cur_pos = event.globalPosition().toPoint()
            if (cur_pos - self._press_pos).manhattanLength() > 4:
                self._is_dragging = True
                self.move(cur_pos - self._drag_pos)
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.sig_restore_main_window.emit()
            event.accept()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: #111827; border: 1px solid #1f293d; color: #f1f5f9; padding: 4px; }"
            "QMenu::item:selected { background-color: #38bdf8; color: #0f172a; font-weight: bold; border-radius: 4px; }"
        )
        act_pause = menu.addAction("▶  Resume Automation" if self.is_paused else "⏸  Pause Automation")
        act_skip = menu.addAction("⏭  Skip Current Prompt")
        act_stop = menu.addAction("⏹  Emergency Stop")
        menu.addSeparator()
        act_studio = menu.addAction("🖥  Open Studio Window")
        act_hud = menu.addAction("🗖  Open Full Live HUD")
        menu.addSeparator()
        act_clickthrough = menu.addAction("🔓  Disable Click-Through" if self._click_through else "🔒  Enable Click-Through")
        act_hide = menu.addAction("✕  Hide Overlay Bar")

        action = menu.exec(event.globalPos())
        if action == act_pause:
            self._toggle_pause()
        elif action == act_skip:
            self.sig_skip_clicked.emit()
        elif action == act_stop:
            self.sig_stop_clicked.emit()
        elif action == act_studio:
            self.sig_restore_main_window.emit()
        elif action == act_hud:
            self.sig_restore_hud.emit()
        elif action == act_clickthrough:
            self.toggle_click_through()
        elif action == act_hide:
            self.hide()

    def close(self):
        self._stuck_monitor_timer.stop()
        return super().close()


class FloatingHudWidget(QWidget):
    """
    Always-on-top, draggable floating HUD overlay widget.
    Allows the user to see real-time execution steps, live timers, and streaming logs
    while Antigravity or any other window is active and full-screen.
    Supports minimizing to a compact floating SVG mini-logo widget.
    """

    sig_pause_clicked = pyqtSignal()
    sig_resume_clicked = pyqtSignal()
    sig_stop_clicked = pyqtSignal()
    sig_skip_clicked = pyqtSignal()
    sig_restore_main_window = pyqtSignal()
    sig_exit_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(None)  # Top-level window
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("Antigravity Live HUD")
        self.resize(460, 260)

        self._drag_pos = QPoint()
        self.is_paused = False
        self._current_step = "Initializing..."
        self._current_timer = "0.0s"

        # Companion Mini Logo Floating Widget
        self.mini_widget = FloatingHudMiniWidget()
        self.mini_widget.sig_restore_hud.connect(self.restore_from_mini)
        self.mini_widget.sig_restore_main_window.connect(self.sig_restore_main_window.emit)
        self.mini_widget.sig_pause_clicked.connect(self.sig_pause_clicked.emit)
        self.mini_widget.sig_resume_clicked.connect(self.sig_resume_clicked.emit)
        self.mini_widget.sig_stop_clicked.connect(self.sig_stop_clicked.emit)
        self.mini_widget.sig_skip_clicked.connect(self.sig_skip_clicked.emit)
        self.mini_widget.sig_exit_clicked.connect(self.sig_exit_clicked.emit)

        # Companion Single-Line Status Overlay Bar (Dynamic Island style)
        self.overlay_bar = OneLineOverlayWidget()
        self.overlay_bar.sig_restore_hud.connect(self.restore_from_mini)
        self.overlay_bar.sig_restore_main_window.connect(self.sig_restore_main_window.emit)
        self.overlay_bar.sig_pause_clicked.connect(self.sig_pause_clicked.emit)
        self.overlay_bar.sig_resume_clicked.connect(self.sig_resume_clicked.emit)
        self.overlay_bar.sig_stop_clicked.connect(self.sig_stop_clicked.emit)
        self.overlay_bar.sig_skip_clicked.connect(self.sig_skip_clicked.emit)
        self.overlay_bar.sig_exit_clicked.connect(self.sig_exit_clicked.emit)

        # Main container with styled rounded border
        container = QFrame(self)
        container.setObjectName("hud-container")
        container.setStyleSheet(
            """
            #hud-container {
                background-color: rgba(9, 13, 22, 0.96);
                border: 1px solid #212b42;
                border-radius: 10px;
            }
            """
        )

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        inner_layout = QVBoxLayout(container)
        inner_layout.setContentsMargins(12, 10, 12, 10)
        inner_layout.setSpacing(8)

        # 1. Draggable Header Bar
        header = QHBoxLayout()
        header.setSpacing(6)

        title_lbl = QLabel("🤖  Antigravity Live HUD")
        title_lbl.setStyleSheet("font-weight: 700; font-size: 12px; color: #818cf8;")
        header.addWidget(title_lbl)

        header.addStretch()

        btn_base_style = (
            "QPushButton { background-color: #162035; border: 1px solid #243352; border-radius: 5px; font-size: 11px; color: #cbd5e1; }"
            "QPushButton:hover { background-color: #223150; border-color: #3b507d; color: #ffffff; }"
        )

        self.btn_pause = QPushButton("⏸")
        self.btn_pause.setFixedSize(26, 24)
        self.btn_pause.setToolTip("Pause / Resume Automation")
        self.btn_pause.setStyleSheet(btn_base_style)
        self.btn_pause.clicked.connect(self._toggle_pause)
        header.addWidget(self.btn_pause)

        self.btn_skip = QPushButton("⏭")
        self.btn_skip.setFixedSize(26, 24)
        self.btn_skip.setToolTip("Skip to Next Prompt")
        self.btn_skip.setStyleSheet(btn_base_style)
        self.btn_skip.clicked.connect(self.sig_skip_clicked.emit)
        header.addWidget(self.btn_skip)

        self.btn_stop = QPushButton("⏹")
        self.btn_stop.setFixedSize(26, 24)
        self.btn_stop.setToolTip("Emergency Stop Automation")
        self.btn_stop.setStyleSheet(
            "QPushButton { background-color: #3b1116; border: 1px solid #7f1d1d; border-radius: 5px; font-size: 11px; color: #fca5a5; }"
            "QPushButton:hover { background-color: #7f1d1d; color: #ffffff; }"
        )
        self.btn_stop.clicked.connect(self.sig_stop_clicked.emit)
        header.addWidget(self.btn_stop)

        # Minimize Button: docks back to Top Status Bar
        self.btn_minimize = QPushButton("🗕")
        self.btn_minimize.setFixedSize(26, 24)
        self.btn_minimize.setToolTip("Dock into Top Status Bar")
        self.btn_minimize.setStyleSheet(
            "QPushButton { background-color: #162035; border: 1px solid #243352; border-radius: 5px; font-size: 11px; color: #38bdf8; }"
            "QPushButton:hover { background-color: #0284c7; color: #ffffff; }"
        )
        self.btn_minimize.clicked.connect(self._on_minimize_clicked)
        header.addWidget(self.btn_minimize)

        self.btn_expand = QPushButton("🗗")
        self.btn_expand.setFixedSize(26, 24)
        self.btn_expand.setToolTip("Open Studio Window (Main Interface)")
        self.btn_expand.setStyleSheet(btn_base_style)
        self.btn_expand.clicked.connect(self.sig_restore_main_window.emit)
        header.addWidget(self.btn_expand)

        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(26, 24)
        self.btn_close.setToolTip("Close HUD")
        self.btn_close.setStyleSheet(btn_base_style)
        self.btn_close.clicked.connect(self._on_close_clicked)
        header.addWidget(self.btn_close)

        inner_layout.addLayout(header)

        # 2. Status Row with Active Step Pill & Live Timer
        status_row = QHBoxLayout()
        status_row.setSpacing(8)

        self.lbl_step = QLabel("Step: Initializing...")
        self.lbl_step.setStyleSheet(
            "background-color: #171a2e; color: #a5b4fc; border: 1px solid #3730a3; "
            "padding: 3px 8px; border-radius: 5px; font-weight: 700; font-size: 11px;"
        )
        status_row.addWidget(self.lbl_step)

        status_row.addStretch()

        self.lbl_timer = QLabel("0.0s")
        self.lbl_timer.setStyleSheet(
            "font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 11px; font-weight: 700; "
            "background-color: #0c2336; color: #38bdf8; border: 1px solid #0369a1; "
            "padding: 3px 8px; border-radius: 5px;"
        )
        status_row.addWidget(self.lbl_timer)

        inner_layout.addLayout(status_row)

        # 3. Compact Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(
            """
            QProgressBar {
                background-color: #101625;
                border: 1px solid #1f273d;
                border-radius: 4px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6366f1, stop:1 #06b6d4);
                border-radius: 3px;
            }
            """
        )
        inner_layout.addWidget(self.progress_bar)

        # 4. Compact Real-Time Log Viewer
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet(
            """
            QTextEdit {
                background-color: #070a12;
                border: 1px solid #192236;
                border-radius: 6px;
                font-family: 'Cascadia Code', 'Consolas', monospace;
                font-size: 11px;
                color: #cbd5e1;
                padding: 4px;
            }
            """
        )
        inner_layout.addWidget(self.log_text)

        # Position at top-right of primary screen
        self._position_top_right()

    def _position_top_right(self):
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            x = geom.right() - self.width() - 24
            y = geom.top() + 48
            self.move(x, y)

    def _on_minimize_clicked(self):
        """Minimizes the full HUD into the sleek top overlay bar."""
        self.hide()
        self.mini_widget.hide()
        if hasattr(self, "overlay_bar"):
            self.overlay_bar.show_at_default_position()
        else:
            self.minimize_to_mini()

    def minimize_to_mini(self):
        """Minimizes the full HUD into the compact floating mini logo widget."""
        curr_pos = self.pos()
        # Position mini logo at the top-right corner of the full HUD
        mini_x = curr_pos.x() + self.width() - self.mini_widget.width()
        mini_y = curr_pos.y()
        self.mini_widget.move(mini_x, mini_y)
        self.hide()
        self.mini_widget.show()
        self.mini_widget.raise_()

    def restore_from_mini(self):
        """Restores the full HUD from the mini logo widget or top overlay bar."""
        if hasattr(self, "overlay_bar"):
            self.overlay_bar.hide()
        curr_pos = self.mini_widget.pos()
        hud_x = curr_pos.x() - (self.width() - self.mini_widget.width())
        hud_y = curr_pos.y()

        # Constrain within available screen boundaries
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            hud_x = max(geom.left() + 10, min(hud_x, geom.right() - self.width() - 10))
            hud_y = max(geom.top() + 10, min(hud_y, geom.bottom() - self.height() - 10))

        self.move(hud_x, hud_y)
        self.mini_widget.hide()
        self.show()
        self.raise_()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def append_log(self, level: str, message: str):
        now = datetime.now().strftime("%H:%M:%S")
        colors = {
            "INFO": "#38bdf8",
            "SUCCESS": "#34d399",
            "WARN": "#fbbf24",
            "ERROR": "#f87171",
        }
        color = colors.get(level.upper(), "#94a3b8")
        html = (
            f'<div style="margin-bottom: 2px;">'
            f'<span style="color: #64748b; font-size: 10px;">[{now}]</span> '
            f'<span style="color: {color}; font-weight: bold;">[{level.upper()}]</span> '
            f'<span style="color: #e2e8f0;">{message}</span>'
            f'</div>'
        )
        self.log_text.append(html)
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_text.setTextCursor(cursor)
        self.overlay_bar.append_log(level, message)

    def set_step(self, step_title: str):
        self._current_step = step_title
        if not getattr(self.mini_widget, "_is_stabilizing", False):
            self.lbl_step.setText(f"Step: {step_title}")
        self.mini_widget.set_step(step_title)
        self.overlay_bar.set_step(step_title)

    def set_timer(self, seconds: float):
        if seconds >= 3600:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            timer_str = f"{h}h {m}m {s}s"
        elif seconds >= 60:
            m = int(seconds // 60)
            s = int(seconds % 60)
            timer_str = f"{m}m {s}s"
        else:
            timer_str = f"{seconds:.1f}s"

        self._current_timer = timer_str
        if not getattr(self.mini_widget, "_is_stabilizing", False):
            self.lbl_timer.setText(timer_str)
        self.mini_widget.set_timer(timer_str)
        self.overlay_bar.set_timer(timer_str)

    def set_stabilization_progress(self, remaining: float, total: float):
        """Updates live stabilization countdown across mini logo widget, full HUD, and overlay bar."""
        self.mini_widget.set_stabilization_progress(remaining, total)
        self.overlay_bar.set_stabilization_progress(remaining, total)
        if remaining > 0:
            rem_str = f"{int(round(remaining))}s"
            self.lbl_step.setText(f"Step: Stabilizing ({rem_str} left)...")
            self.lbl_timer.setText(f"⏳ {rem_str}")
            # If the full HUD window is hidden, ensure the mini logo is visible on screen to display live countdown!
            if not self.isVisible() and not self.mini_widget.isVisible():
                self.mini_widget.show_at_default_position()
        else:
            self.lbl_step.setText(f"Step: {self._current_step}")
            self.lbl_timer.setText(self._current_timer)

    def set_progress(self, completed: int, total: int):
        if total > 0:
            pct = int((completed / total) * 100)
            self.progress_bar.setValue(pct)
        else:
            self.progress_bar.setValue(0)

    def set_running_state(self, state: str):
        if state == "RUNNING":
            self.btn_pause.setText("⏸")
            self.is_paused = False
        elif state == "PAUSED":
            self.btn_pause.setText("▶")
            self.is_paused = True
        else:
            self.btn_pause.setText("⏸")
            self.is_paused = False

        self.mini_widget.set_running_state(state)
        self.overlay_bar.set_running_state(state)

    def _toggle_pause(self):
        if self.is_paused:
            self.sig_resume_clicked.emit()
        else:
            self.sig_pause_clicked.emit()

    def _on_close_clicked(self):
        """Hides the floating HUD and docks back to the sleek top overlay bar."""
        self.hide()
        self.mini_widget.hide()
        if hasattr(self, "overlay_bar"):
            self.overlay_bar.show_at_default_position()

    def close(self):
        self.mini_widget.close()
        self.overlay_bar.close()
        return super().close()
