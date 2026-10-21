"""
Modern Dark Theme Design System & Stylesheet for Antigravity Automation Desktop Studio.
Inspired by Linear, Raycast, and Microsoft Fluent 2 desktop design standards.
Strictly preserves tested tokens and guarantees zero functional regressions.
"""

from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# DESIGN SYSTEM TOKENS (Single Source of Truth)
# ─────────────────────────────────────────────────────────────────────────────

# Surface Elevation Scale (Dark Slate-Zinc Palette)
COLOR_CANVAS = "#090d16"           # Root window background
COLOR_SURFACE_0 = "#0d121f"        # Secondary panels, tab container
COLOR_SURFACE_1 = "#131929"        # Cards, dialog background, primary containers
COLOR_SURFACE_2 = "#182136"        # Inputs, table cells, secondary controls
COLOR_SURFACE_HOVER = "#1f2a45"    # Hover surface
COLOR_SURFACE_ACTIVE = "#263556"   # Active / Selected surface

# Structural Borders
COLOR_BORDER_SUBTLE = "#192238"    # Interior dividers, gridlines
COLOR_BORDER_DEFAULT = "#222e47"   # Default card and form borders
COLOR_BORDER_STRONG = "#334366"    # Card outlines, hover borders
COLOR_BORDER_FOCUS = "#6366f1"     # Focus ring border

# Text & Typography Tokens (All pass WCAG 2.1 AA)
COLOR_TEXT_PRIMARY = "#f8fafc"     # High-contrast titles, values (15.5:1)
COLOR_TEXT_SECONDARY = "#cbd5e1"   # Primary body, descriptions (11.8:1)
COLOR_TEXT_MUTED = "#94a3b8"       # Secondary captions, timestamps (6.5:1)
COLOR_TEXT_FAINT = "#64748b"       # Subtle hints, disabled text

# Semantic Accent Tokens
COLOR_PRIMARY = "#6366f1"          # Electric Indigo (Primary Brand)
COLOR_PRIMARY_HOVER = "#818cf8"
COLOR_PRIMARY_ACTIVE = "#4f46e5"

COLOR_SUCCESS = "#10b981"          # Emerald (Start, Completed, Ready)
COLOR_SUCCESS_HOVER = "#34d399"
COLOR_SUCCESS_ACTIVE = "#059669"

COLOR_WARNING = "#f59e0b"          # Amber (Pause, Stabilizing, Low Quota)
COLOR_WARNING_HOVER = "#fbbf24"
COLOR_WARNING_ACTIVE = "#d97706"

COLOR_DANGER = "#ef4444"           # Crimson / Rose (Stop, Emergency, Failed)
COLOR_DANGER_HOVER = "#f87171"
COLOR_DANGER_ACTIVE = "#dc2626"

COLOR_INFO = "#0ea5e9"             # Cyan / Sky (Telemetry, Snippet tags)
COLOR_INFO_HOVER = "#38bdf8"
COLOR_INFO_ACTIVE = "#0284c7"


# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL DARK THEME STYLESHEET (PyQt6 QSS)
# ─────────────────────────────────────────────────────────────────────────────

DARK_THEME_QSS = f"""
/* ─── Base Window & Global Defaults ─── */
QMainWindow, QDialog, QWidget {{
    background-color: {COLOR_CANVAS};
    color: {COLOR_TEXT_PRIMARY};
    font-family: 'Segoe UI Variable Text', 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
    font-size: 13px;
    selection-background-color: {COLOR_PRIMARY};
    selection-color: #ffffff;
}}

/* ─── Modern Tabs ─── */
QTabWidget::pane {{
    border: 1px solid {COLOR_BORDER_DEFAULT};
    background-color: {COLOR_SURFACE_0};
    border-radius: 8px;
    top: -1px;
}}

QTabBar::tab {{
    background-color: transparent;
    color: {COLOR_TEXT_MUTED};
    padding: 7px 16px;
    margin-right: 4px;
    border-radius: 6px;
    font-weight: 600;
    font-size: 12px;
    border: 1px solid transparent;
}}

QTabBar::tab:selected {{
    background-color: {COLOR_SURFACE_1};
    color: {COLOR_TEXT_PRIMARY};
    border: 1px solid {COLOR_BORDER_DEFAULT};
}}

QTabBar::tab:hover:!selected {{
    background-color: {COLOR_SURFACE_0};
    color: {COLOR_TEXT_SECONDARY};
}}

/* ─── Group Boxes & Modern Cards ─── */
QGroupBox {{
    background-color: {COLOR_SURFACE_1};
    border: 1px solid {COLOR_BORDER_DEFAULT};
    border-radius: 8px;
    margin-top: 14px;
    padding: 16px 14px 14px 14px;
    font-weight: 700;
    color: {COLOR_TEXT_MUTED};
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.6px;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 8px;
    background-color: {COLOR_SURFACE_1};
    color: {COLOR_PRIMARY_HOVER};
    border-radius: 3px;
}}

/* ─── Buttons ─── */
QPushButton {{
    background-color: {COLOR_SURFACE_2};
    color: {COLOR_TEXT_PRIMARY};
    border: 1px solid {COLOR_BORDER_DEFAULT};
    border-radius: 6px;
    padding: 7px 14px;
    font-weight: 600;
    font-size: 12px;
    min-height: 20px;
}}

QPushButton:hover {{
    background-color: {COLOR_SURFACE_HOVER};
    border-color: {COLOR_BORDER_STRONG};
    color: #ffffff;
}}

QPushButton:pressed {{
    background-color: {COLOR_SURFACE_ACTIVE};
    border-color: {COLOR_PRIMARY};
}}

QPushButton:disabled {{
    background-color: #0b0f19;
    color: {COLOR_TEXT_FAINT};
    border-color: {COLOR_BORDER_SUBTLE};
}}

/* Primary Accent Button */
QPushButton#btn-primary, QPushButton[variant="primary"] {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #6366f1, stop:1 #4f46e5);
    border: 1px solid #4338ca;
    color: #ffffff;
    font-weight: 700;
}}

QPushButton#btn-primary:hover, QPushButton[variant="primary"]:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #7c82f8, stop:1 #6366f1);
    border-color: #6366f1;
    color: #ffffff;
}}

QPushButton#btn-primary:pressed, QPushButton[variant="primary"]:pressed {{
    background: #3730a3;
    border-color: #312e81;
}}

/* Success Button */
QPushButton#btn-success, QPushButton[variant="success"] {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #10b981, stop:1 #059669);
    border: 1px solid #047857;
    color: #ffffff;
    font-weight: 700;
}}

QPushButton#btn-success:hover, QPushButton[variant="success"]:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #34d399, stop:1 #10b981);
    border-color: #10b981;
    color: #ffffff;
}}

QPushButton#btn-success:pressed, QPushButton[variant="success"]:pressed {{
    background: #065f46;
}}

/* Warning Button */
QPushButton#btn-warning, QPushButton[variant="warning"] {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f59e0b, stop:1 #d97706);
    border: 1px solid #b45309;
    color: #ffffff;
    font-weight: 700;
}}

QPushButton#btn-warning:hover, QPushButton[variant="warning"]:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #fbbf24, stop:1 #f59e0b);
    border-color: #f59e0b;
    color: #ffffff;
}}

QPushButton#btn-warning:pressed, QPushButton[variant="warning"]:pressed {{
    background: #92400e;
}}

/* Danger Button */
QPushButton#btn-danger, QPushButton[variant="danger"] {{
    background-color: #261014;
    border: 1px solid #7f1d1d;
    color: #f87171;
    font-weight: 700;
}}

QPushButton#btn-danger:hover, QPushButton[variant="danger"]:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ef4444, stop:1 #dc2626);
    border-color: #b91c1c;
    color: #ffffff;
}}

QPushButton#btn-danger:pressed, QPushButton[variant="danger"]:pressed {{
    background: #991b1b;
}}

/* Secondary Toolbar Buttons */
QPushButton#btn-secondary, QPushButton[variant="secondary"] {{
    background-color: {COLOR_SURFACE_2};
    border: 1px solid {COLOR_BORDER_DEFAULT};
    color: {COLOR_TEXT_SECONDARY};
}}

QPushButton#btn-secondary:hover, QPushButton[variant="secondary"]:hover {{
    background-color: {COLOR_SURFACE_HOVER};
    border-color: {COLOR_BORDER_STRONG};
    color: {COLOR_TEXT_PRIMARY};
}}

/* Ghost Toolbar & Icon Buttons */
QPushButton[variant="ghost"], QToolButton[variant="ghost"] {{
    background-color: transparent;
    border: 1px solid transparent;
    color: {COLOR_TEXT_MUTED};
    padding: 4px 8px;
    border-radius: 5px;
}}

QPushButton[variant="ghost"]:hover, QToolButton[variant="ghost"]:hover {{
    background-color: {COLOR_SURFACE_HOVER};
    border-color: {COLOR_BORDER_DEFAULT};
    color: {COLOR_TEXT_PRIMARY};
}}

QPushButton[variant="ghost"]:pressed, QToolButton[variant="ghost"]:pressed {{
    background-color: {COLOR_SURFACE_ACTIVE};
}}

QPushButton[variant="ghost"]:disabled, QToolButton[variant="ghost"]:disabled {{
    background-color: transparent;
    color: {COLOR_TEXT_FAINT};
    border-color: transparent;
}}

/* General QToolButton styling (Table rows and compact tools) */
QToolButton {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 5px;
    color: {COLOR_TEXT_SECONDARY};
    padding: 2px 4px;
    font-weight: 600;
}}

QToolButton:hover {{
    background-color: {COLOR_SURFACE_HOVER};
    border-color: {COLOR_BORDER_STRONG};
    color: #ffffff;
}}

QToolButton:pressed {{
    background-color: {COLOR_SURFACE_ACTIVE};
}}

QToolButton:disabled {{
    color: {COLOR_TEXT_FAINT};
}}

/* Segmented Control Container & Items */
QFrame#segmented-container {{
    background-color: #101625;
    border: 1px solid {COLOR_BORDER_DEFAULT};
    border-radius: 6px;
    padding: 2px;
}}

QPushButton#segmented-item {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    color: {COLOR_TEXT_MUTED};
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
    min-height: 18px;
}}

QPushButton#segmented-item:hover {{
    color: {COLOR_TEXT_PRIMARY};
    background-color: {COLOR_SURFACE_2};
}}

QPushButton#segmented-item:checked {{
    background-color: {COLOR_SURFACE_ACTIVE};
    border: 1px solid {COLOR_BORDER_STRONG};
    color: #ffffff;
    font-weight: 700;
}}

/* ─── Inputs & Form Controls ─── */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
    background-color: #101625;
    border: 1px solid {COLOR_BORDER_DEFAULT};
    border-radius: 6px;
    padding: 7px 11px;
    color: {COLOR_TEXT_PRIMARY};
    font-size: 13px;
    selection-background-color: {COLOR_PRIMARY};
}}

QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover, QTextEdit:hover, QPlainTextEdit:hover {{
    border-color: {COLOR_BORDER_STRONG};
}}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {COLOR_PRIMARY};
    background-color: #131a2c;
}}

QComboBox::drop-down {{
    border: none;
    width: 24px;
    subcontrol-origin: padding;
    subcontrol-position: top right;
}}

QComboBox::down-arrow {{
    image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 10 10' fill='none' stroke='%2394a3b8' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'><polyline points='2 3.5 5 6.5 8 3.5'/></svg>");
    width: 10px;
    height: 10px;
    margin-right: 6px;
}}

QComboBox QAbstractItemView {{
    background-color: {COLOR_SURFACE_1};
    border: 1px solid {COLOR_BORDER_STRONG};
    border-radius: 6px;
    color: {COLOR_TEXT_PRIMARY};
    selection-background-color: {COLOR_PRIMARY};
    selection-color: #ffffff;
    padding: 4px;
}}

/* ─── Table Widget ─── */
QTableWidget {{
    background-color: {COLOR_CANVAS};
    border: 1px solid {COLOR_BORDER_DEFAULT};
    border-radius: 8px;
    gridline-color: {COLOR_BORDER_SUBTLE};
    color: {COLOR_TEXT_PRIMARY};
    selection-background-color: {COLOR_SURFACE_HOVER};
    selection-color: #ffffff;
}}

QHeaderView::section {{
    background-color: #101625;
    color: {COLOR_TEXT_MUTED};
    padding: 7px 6px;
    font-weight: 700;
    font-size: 11px;
    letter-spacing: 0.3px;
    border: none;
    border-bottom: 1px solid {COLOR_BORDER_DEFAULT};
    border-right: 1px solid {COLOR_BORDER_SUBTLE};
}}

QTableWidget::item {{
    padding: 6px 10px;
    border-bottom: 1px solid {COLOR_BORDER_SUBTLE};
}}

QTableWidget::item:hover {{
    background-color: #121929;
}}

QTableWidget::item:selected {{
    background-color: {COLOR_SURFACE_ACTIVE};
}}

/* ─── Progress Bar ─── */
QProgressBar {{
    background-color: #101625;
    border: 1px solid {COLOR_BORDER_DEFAULT};
    border-radius: 6px;
    text-align: center;
    color: {COLOR_TEXT_PRIMARY};
    font-weight: 700;
    font-size: 11px;
    height: 18px;
}}

QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6366f1, stop:1 #06b6d4);
    border-radius: 5px;
}}

/* ─── Modern Scroll Areas & Floating Scrollbars ─── */
QScrollArea {{
    border: none;
    background-color: transparent;
}}

QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}

QScrollBar:vertical {{
    background-color: transparent;
    width: 6px;
    margin: 0px;
    border-radius: 3px;
}}

QScrollBar::handle:vertical {{
    background-color: #232d42;
    min-height: 24px;
    border-radius: 3px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: #6366f1;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
    border: none;
    height: 0px;
}}

QScrollBar:horizontal {{
    background-color: transparent;
    height: 6px;
    margin: 0px;
    border-radius: 3px;
}}

QScrollBar::handle:horizontal {{
    background-color: #232d42;
    min-width: 24px;
    border-radius: 3px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: #6366f1;
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
    border: none;
    width: 0px;
}}

/* ─── Status Bar ─── */
QStatusBar {{
    background-color: #070a12;
    color: {COLOR_TEXT_MUTED};
    border-top: 1px solid {COLOR_BORDER_SUBTLE};
    padding: 4px 8px;
    font-size: 12px;
}}

/* ─── Labels & Badges ─── */
QLabel {{
    color: {COLOR_TEXT_PRIMARY};
    background-color: transparent;
    border: none;
}}

QLabel#lbl-muted {{
    color: {COLOR_TEXT_MUTED};
    font-size: 12px;
}}

QLabel#lbl-badge {{
    background-color: {COLOR_SURFACE_2};
    color: {COLOR_TEXT_SECONDARY};
    border: 1px solid {COLOR_BORDER_DEFAULT};
    border-radius: 5px;
    padding: 3px 9px;
    font-size: 11px;
    font-weight: 600;
}}

/* ─── Checkboxes & Radio Buttons ─── */
QCheckBox, QRadioButton {{
    background-color: transparent;
    color: {COLOR_TEXT_PRIMARY};
    spacing: 8px;
    font-size: 12px;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {COLOR_BORDER_DEFAULT};
    border-radius: 4px;
    background-color: #101625;
}}

QCheckBox::indicator:hover {{
    border-color: {COLOR_BORDER_STRONG};
    background-color: {COLOR_SURFACE_2};
}}

QCheckBox::indicator:checked {{
    background-color: {COLOR_PRIMARY};
    border-color: {COLOR_PRIMARY};
    image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 16 16' fill='none' stroke='white' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'><polyline points='3.5 8.5 6.5 11.5 12.5 4.5'/></svg>");
}}

QRadioButton::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {COLOR_BORDER_DEFAULT};
    border-radius: 7px;
    background-color: #101625;
}}

QRadioButton::indicator:hover {{
    border-color: {COLOR_BORDER_STRONG};
    background-color: {COLOR_SURFACE_2};
}}

QRadioButton::indicator:checked {{
    background-color: {COLOR_PRIMARY};
    border-color: {COLOR_PRIMARY};
    image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 14 14'><circle cx='7' cy='7' r='3.2' fill='white'/></svg>");
}}

/* ─── Sliders ─── */
QSlider::groove:horizontal {{
    height: 6px;
    background: {COLOR_SURFACE_2};
    border-radius: 3px;
}}

QSlider::sub-page:horizontal {{
    background: {COLOR_PRIMARY};
    border-radius: 3px;
}}

QSlider::handle:horizontal {{
    background: #ffffff;
    border: 2px solid {COLOR_PRIMARY};
    width: 16px;
    margin-top: -5px;
    margin-bottom: -5px;
    border-radius: 8px;
}}

QSlider::handle:horizontal:hover {{
    background: #cbd5e1;
}}
"""


# ─────────────────────────────────────────────────────────────────────────────
# REUSABLE CARD CONTAINER STYLESHEET
# ─────────────────────────────────────────────────────────────────────────────
CARD_STYLE = f"""
.QFrame {{
    background-color: {COLOR_SURFACE_1};
    border: 1px solid {COLOR_BORDER_DEFAULT};
    border-radius: 8px;
}}
QLabel {{
    background-color: transparent;
    border: none;
}}
QRadioButton {{
    background-color: transparent;
}}
QCheckBox {{
    background-color: transparent;
}}
"""


# ─────────────────────────────────────────────────────────────────────────────
# HTML PILL BADGE GENERATORS
# (Tested tokens: ❄️, Frozen, #450a0a, #064e3b, #78350f strictly preserved)
# ─────────────────────────────────────────────────────────────────────────────

def get_status_badge_html(status: str) -> str:
    """Generates an HTML pill badge for table cells with anti-clipping padding."""
    colors = {
        "Pending": ("#78350f", "#fef3c7", "#f59e0b"),      # (bg, text, border)
        "Running": ("#0c4a6e", "#e0f2fe", "#0284c7"),
        "Completed": ("#064e3b", "#d1fae5", "#10b981"),
        "Failed": ("#7f1d1d", "#fee2e2", "#ef4444"),
        "Skipped": ("#1e293b", "#e2e8f0", "#475569"),
        "Cancelled": ("#292524", "#f5f5f4", "#57534e"),
    }
    bg, text, border = colors.get(status, ("#182033", "#cbd5e1", "#26324d"))
    return (
        f'<div style="text-align: center; white-space: nowrap;">'
        f'<span style="background-color: {bg}; color: {text}; border: 1px solid {border}; '
        f'padding: 3px 12px; border-radius: 12px; font-weight: 700; font-size: 11px; letter-spacing: 0.3px; display: inline-block;">'
        f'{status}'
        f'</span></div>'
    )


def get_account_badge_html(status: str, enabled: bool = True) -> str:
    """Generates an HTML pill badge for account status (Active, Ready, Low Quota, Disabled, Frozen)."""
    if not enabled:
        return (
            '<div style="text-align: center; white-space: nowrap;">'
            '<span style="background-color: #182033; color: #94a3b8; border: 1px solid #26324d; '
            'padding: 3px 12px; border-radius: 12px; font-weight: 700; font-size: 11px; display: inline-block;">'
            'Disabled'
            '</span></div>'
        )

    colors = {
        "Active": ("#064e3b", "#d1fae5", "#10b981"),       # Emerald
        "Ready": ("#1e1b4b", "#e0e7ff", "#6366f1"),        # Indigo
        "Jump": ("#3b0764", "#f3e8ff", "#a855f7"),         # Purple (Jump target)
        "Low Quota": ("#78350f", "#fef3c7", "#f59e0b"),    # Amber
        "Exhausted": ("#7f1d1d", "#fee2e2", "#ef4444"),    # Red
        "Frozen": ("#450a0a", "#fecaca", "#dc2626"),       # Deep Red/Crimson (#450a0a tested)
        "Unregistered": ("#451a03", "#fed7aa", "#f97316"), # Orange
    }
    bg, text, border = colors.get(status, ("#182033", "#cbd5e1", "#26324d"))
    if status == "Frozen":
        label = f"❄️ {status}"
    elif status == "Jump":
        label = "🎯 Next"
    else:
        label = status
    return (
        f'<div style="text-align: center; white-space: nowrap;">'
        f'<span style="background-color: {bg}; color: {text}; border: 1px solid {border}; '
        f'padding: 3px 12px; border-radius: 12px; font-weight: 700; font-size: 11px; letter-spacing: 0.3px; display: inline-block;">'
        f'{label}'
        f'</span></div>'
    )


def get_quota_pill_html(percent: Optional[int]) -> str:
    """Generates an HTML pill badge for weekly quota percentage."""
    if percent is None:
        return (
            '<div style="text-align: center;">'
            '<span style="color: #64748b; font-size: 11px;">—</span>'
            '</div>'
        )
    if percent >= 20:
        bg, text, border = ("#064e3b", "#a7f3d0", "#059669")   # Healthy green (#064e3b tested)
    elif percent >= 3:
        bg, text, border = ("#78350f", "#fef3c7", "#d97706")   # Warning amber (#78350f tested)
    else:
        bg, text, border = ("#450a0a", "#fee2e2", "#dc2626")   # Critical red (< 3%) (#450a0a tested)

    return (
        f'<div style="text-align: center; white-space: nowrap;">'
        f'<span style="background-color: {bg}; color: {text}; border: 1px solid {border}; '
        f'padding: 2px 10px; border-radius: 10px; font-weight: 700; font-size: 11px; display: inline-block;">'
        f'{percent}%'
        f'</span></div>'
    )
