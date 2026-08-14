"""Application theming helpers for Kaderblick branding."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QPainter, QPalette
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QWidget

from ..runtime_paths import asset_path


_PRIMARY_PURPLE = "#6d4cc2"
_PRIMARY_PURPLE_DARK = "#5a3ca8"
_PRIMARY_PURPLE_SOFT = "#ede8f9"
_SURFACE = "#FFFFFF"
_SURFACE_ALT = "#F7F5FC"
_APP_BG = "#F4F2F9"
_BORDER = "#D5CFF0"
_TEXT = "#18212B"
_MUTED = "#667582"
_SHADOW = "rgba(8, 32, 16, 0.08)"
_BRAND_FONT_FALLBACKS = ["Impact", "Arial Black", "Sans Serif"]
_UI_FONT_CANDIDATES = ["Roboto Flex", "Inter", "Montserrat", "Helvetica Neue", "Arial"]
_THEME_APPLIED_PROPERTY = "_kaderblickThemeApplied"
_BRAND_K_SIZE_DELTA = 1.7

_loaded_brand_family: str | None = None


_ICON_GAP = 8  # px between shield icon and text


class BrandWordmarkWidget(QWidget):
    def __init__(self, text: str = "KADERBLICK", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self._first = text[:1]
        self._rest = text[1:]
        self.setObjectName("brandWordmark")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFont(brand_wordmark_font())
        icon_path = asset_path("kaderblick_workflow_manager_with_title_justify_invert.svg")
        self._icon_renderer: QSvgRenderer | None = (
            QSvgRenderer(str(icon_path)) if icon_path.exists() else None
        )

    def _icon_width_for_height(self, h: int) -> int:
        if self._icon_renderer is None or not self._icon_renderer.isValid():
            return 0
        ds = self._icon_renderer.defaultSize()
        if ds.height() == 0:
            return h
        return round(h * ds.width() / ds.height())

    def sizeHint(self) -> QSize:
        first_font = QFont(self.font())
        first_font.setPointSizeF(first_font.pointSizeF() + _BRAND_K_SIZE_DELTA)
        first_metrics = QFontMetrics(first_font)
        rest_metrics = QFontMetrics(self.font())
        text_width = first_metrics.horizontalAdvance(self._first) + rest_metrics.horizontalAdvance(self._rest)
        height = max(first_metrics.height(), rest_metrics.height())
        icon_width = (self._icon_width_for_height(height) + _ICON_GAP) if self._icon_renderer is not None else 0
        return QSize(icon_width + text_width, height)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        first_font = QFont(self.font())
        first_font.setPointSizeF(first_font.pointSizeF() + _BRAND_K_SIZE_DELTA)
        first_metrics = QFontMetrics(first_font)
        rest_metrics = QFontMetrics(self.font())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        text_height = max(first_metrics.height(), rest_metrics.height())
        top = (self.height() - text_height) // 2

        x_off = 0
        if self._icon_renderer is not None and self._icon_renderer.isValid():
            icon_w = self._icon_width_for_height(text_height)
            self._icon_renderer.render(painter, QRectF(0, top, icon_w, text_height))
            x_off = icon_w + _ICON_GAP

        first_baseline_y = top + first_metrics.ascent()
        rest_baseline_y = top + rest_metrics.ascent()

        painter.setFont(first_font)
        painter.setPen(QColor("#018707"))
        painter.drawText(x_off, first_baseline_y, self._first)

        painter.setFont(self.font())
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(x_off + first_metrics.horizontalAdvance(self._first), rest_baseline_y, self._rest)
        painter.end()


def apply_application_theme(window: QWidget) -> None:
    app = cast(QApplication | None, QApplication.instance())
    if app is None:
        return

    _ensure_brand_font_loaded()
    if not bool(app.property(_THEME_APPLIED_PROPERTY)):
        app.setFont(_default_ui_font())
        app.setPalette(_build_palette(app.palette()))
        app.setStyleSheet(_build_stylesheet())
        app.setProperty(_THEME_APPLIED_PROPERTY, True)

    brand_wordmark = window.findChild(BrandWordmarkWidget, "brandWordmark")
    if brand_wordmark is not None:
        brand_wordmark.setFont(brand_wordmark_font())
        brand_wordmark.updateGeometry()
        brand_wordmark.update()


def brand_wordmark_font() -> QFont:
    family = _loaded_brand_family or next(iter(_BRAND_FONT_FALLBACKS), "Sans Serif")
    font = QFont(family)
    font.setPointSize(22)
    font.setWeight(QFont.Weight.Bold)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
    return font


def _default_ui_font() -> QFont:
    available = set(QFontDatabase.families())
    family = next((candidate for candidate in _UI_FONT_CANDIDATES if candidate in available), QApplication.font().family())
    font = QFont(family)
    font.setPointSize(10)
    return font


def _ensure_brand_font_loaded() -> None:
    global _loaded_brand_family
    if _loaded_brand_family is not None:
        return

    font_path = asset_path("ImpactLTStd.woff2")
    if font_path.exists():
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id >= 0:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                _loaded_brand_family = families[0]
                return

    for fallback in _BRAND_FONT_FALLBACKS:
        if fallback in set(QFontDatabase.families()):
            _loaded_brand_family = fallback
            return
    _loaded_brand_family = QApplication.font().family()


def _build_palette(seed: QPalette) -> QPalette:
    palette = QPalette(seed)
    text = QColor(_TEXT)
    surface = QColor(_SURFACE)
    surface_alt = QColor(_SURFACE_ALT)
    app_bg = QColor(_APP_BG)
    selection = QColor("#E2DCF6")
    muted = QColor(_MUTED)

    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
        palette.setColor(group, QPalette.ColorRole.WindowText, text)
        palette.setColor(group, QPalette.ColorRole.Text, text)
        palette.setColor(group, QPalette.ColorRole.ButtonText, text)
        palette.setColor(group, QPalette.ColorRole.Base, surface)
        palette.setColor(group, QPalette.ColorRole.AlternateBase, surface_alt)
        palette.setColor(group, QPalette.ColorRole.Window, app_bg)
        palette.setColor(group, QPalette.ColorRole.Highlight, selection)

        palette.setColor(group, QPalette.ColorRole.HighlightedText, text)
        palette.setColor(group, QPalette.ColorRole.PlaceholderText, muted)

    return palette


def _build_stylesheet() -> str:
    return f"""
    QWidget {{
        color: {_TEXT};
        background: {_APP_BG};
    }}

    QLabel {{
        background: transparent;
    }}

    QMainWindow, QDialog {{
        background: {_APP_BG};
    }}

    QMenuBar {{
        background: {_PRIMARY_PURPLE};
        color: white;
        border: none;
        padding: 4px 12px;
        font-weight: 600;
    }}

    QMenuBar::item {{
        background: transparent;
        padding: 8px 12px;
        margin: 2px 4px;
        border-radius: 14px;
    }}

    QMenuBar::item:selected {{
        background: rgba(255, 255, 255, 0.16);
    }}

    QMenu {{
        background: {_SURFACE};
        border: 1px solid {_BORDER};
        border-radius: 12px;
        padding: 6px;
    }}

    QMenu::item {{
        padding: 8px 12px;
        border-radius: 8px;
    }}

    QMenu::item:selected {{
        background: {_PRIMARY_PURPLE_SOFT};
        color: {_PRIMARY_PURPLE_DARK};
    }}

    QToolBar {{
        background: {_PRIMARY_PURPLE};
        border: none;
        spacing: 6px;
        padding: 10px 14px;
    }}

    QToolBar::separator {{
        background: rgba(255, 255, 255, 0.18);
        width: 1px;
        margin: 6px 10px;
    }}

    QToolBar QToolButton {{
        background: transparent;
        color: white;
        border: 1px solid transparent;
        border-radius: 16px;
        padding: 8px 12px;
        font-weight: 600;
    }}

    QToolBar QToolButton:hover {{
        background: rgba(255, 255, 255, 0.14);
    }}

    QToolBar QToolButton:pressed {{
        background: rgba(0, 0, 0, 0.12);
    }}

    QToolBar QToolButton#qt_toolbar_ext_button {{
        background: rgba(255, 255, 255, 0.18);
        border: 1px solid rgba(255, 255, 255, 0.28);
        min-width: 28px;
        padding: 8px;
    }}

    QToolBar QToolButton#qt_toolbar_ext_button:hover {{
        background: rgba(255, 255, 255, 0.28);
    }}

    QToolBar QCheckBox {{
        color: white;
        background: rgba(255, 255, 255, 0.12);
        border: 1px solid rgba(255, 255, 255, 0.2);
        border-radius: 16px;
        padding: 6px 12px;
        font-weight: 600;
        spacing: 8px;
        margin-left: 8px;
    }}

    QToolBar QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 9px;
        border: 1px solid rgba(255, 255, 255, 0.65);
        background: rgba(255, 255, 255, 0.12);
    }}

    QToolBar QCheckBox::indicator:checked {{
        background: {_PRIMARY_PURPLE_DARK};
        border-color: white;
    }}

    QToolBar QCheckBox#publishKaderblickToggle:checked {{
        background: {_PRIMARY_PURPLE_SOFT};
        color: {_PRIMARY_PURPLE_DARK};
        border: 2px solid white;
        padding: 5px 11px;
    }}

    QToolBar QCheckBox#publishKaderblickToggle::indicator:checked {{
        background: {_PRIMARY_PURPLE_DARK};
        border-color: {_PRIMARY_PURPLE_DARK};
    }}

    QToolBar QCheckBox#shutdownToggle:checked {{
        background: #c0392b;
        color: white;
        border: 2px solid white;
        padding: 5px 11px;
    }}

    QToolBar QCheckBox#shutdownToggle:checked:hover {{
        background: #a93226;
    }}

    QToolBar QCheckBox#shutdownToggle::indicator:checked {{
        background: white;
        border-color: white;
    }}

    QWidget#brandWordmark {{
        background: transparent;
    }}

    QStatusBar {{
        background: {_SURFACE};
        border-top: 1px solid {_BORDER};
    }}

    QStatusBar QLabel {{
        background: transparent;
    }}

    QProgressBar {{
        background: #EDE8F9;
        border: none;
        border-radius: 9px;
        min-height: 18px;
        text-align: center;
        color: {_TEXT};
        font-weight: 600;
    }}

    QProgressBar::chunk {{
        background: {_PRIMARY_PURPLE};
        border-radius: 9px;
    }}

    QTableWidget,
    QTextEdit,
    QPlainTextEdit,
    QListWidget,
    QTreeWidget,
    QFrame#cardSurface,
    QGroupBox,
    QLineEdit,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QDateEdit,
    QAbstractSpinBox,
    QScrollArea,
    QTabWidget::pane {{
        background: {_SURFACE};
        border: 1px solid {_BORDER};
        border-radius: 16px;
    }}

    QTableWidget {{
        alternate-background-color: {_SURFACE_ALT};
        gridline-color: #E3DDF5;
        selection-background-color: #E2DCF6;
        selection-color: {_TEXT};
        padding: 4px;
    }}

    QHeaderView::section {{
        background: #EEE8FA;
        color: {_TEXT};
        border: none;
        border-bottom: 1px solid {_BORDER};
        padding: 10px 12px;
        font-weight: 700;
    }}

    QTextEdit,
    QPlainTextEdit {{
        padding: 8px;
    }}

    QLineEdit,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QDateEdit,
    QAbstractSpinBox,
    QTextEdit,
    QPlainTextEdit {{
        padding: 8px 10px;
        selection-background-color: #E2DCF6;
        selection-color: {_TEXT};
        color: {_TEXT};
    }}

    /* Inline-Editoren in Views duerfen keine Formular-Paddings bekommen,
       sonst wird der Text in flachen Zeilen abgeschnitten. */
    QAbstractItemView QLineEdit,
    QTableView QLineEdit,
    QTreeView QLineEdit,
    QListView QLineEdit {{
        padding: 0 2px;
        margin: 0;
        border-radius: 0;
        min-height: 0px;
    }}

    QComboBox::drop-down,
    QDateEdit::drop-down {{
        border: none;
        width: 24px;
    }}

    QPushButton {{
        background: {_PRIMARY_PURPLE};
        color: white;
        border: none;
        border-radius: 12px;
        padding: 8px 14px;
        font-weight: 700;
    }}

    QPushButton:hover {{
        background: {_PRIMARY_PURPLE_DARK};
    }}

    QPushButton:disabled {{
        background: #C4B5E8;
        color: #F3F7F3;
    }}

    QPushButton[flat="true"] {{
        background: transparent;
        color: {_PRIMARY_PURPLE_DARK};
    }}

    QPushButton[flat="true"]:hover {{
        background: {_PRIMARY_PURPLE_SOFT};
    }}

    QGroupBox {{
        margin-top: 12px;
        padding-top: 10px;
        font-weight: 700;
    }}

    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 14px;
        padding: 0 6px;
        color: {_TEXT};
    }}

    QCheckBox {{
        spacing: 8px;
    }}

    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 9px;
        border: 1px solid #B5A4DF;
        background: white;
    }}

    QCheckBox::indicator:checked {{
        background: {_PRIMARY_PURPLE};
        border-color: {_PRIMARY_PURPLE};
    }}

    QSplitter::handle {{
        background: transparent;
    }}

    QScrollBar:vertical {{
        background: transparent;
        width: 12px;
        margin: 6px 2px 6px 2px;
    }}

    QScrollBar::handle:vertical {{
        background: #C4B5E8;
        min-height: 28px;
        border-radius: 6px;
    }}

    QScrollBar::handle:vertical:hover {{
        background: #A896D9;
    }}

    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical,
    QScrollBar::sub-page:vertical {{
        background: transparent;
        border: none;
        height: 0px;
    }}
    """
