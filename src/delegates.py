"""Tabellen-Delegate für grafischen Fortschritt in der Status-Spalte."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QStyledItemDelegate, QApplication, QStyle,
)


class ProgressDelegate(QStyledItemDelegate):
    """Zeichnet einen Fortschrittsbalken als Hintergrund in die Status-Zelle,
    wenn ein Job den Status 'Läuft' hat."""

    _BAR_COLOR = QColor(60, 140, 220, 90)       # halbtransparentes Blau
    _BAR_DONE_COLOR = QColor(60, 180, 75, 90)   # halbtransparentes Grün

    def paint(self, painter, option, index):
        self.initStyleOption(option, index)
        text = index.data(Qt.DisplayRole) or ""
        pct = index.data(Qt.UserRole)

        if pct is not None and isinstance(pct, int) and pct > 0:
            # Standardhintergrund (Auswahl, Alternating Rows etc.)
            style = (option.widget.style() if option.widget
                     else QApplication.style())
            style.drawPrimitive(
                QStyle.PE_PanelItemViewItem, option, painter, option.widget)

            # Fortschrittsbalken zeichnen
            rect = option.rect
            fill_width = int(rect.width() * min(pct, 100) / 100)
            bar_rect = rect.adjusted(0, 1, 0, -1)
            bar_rect.setWidth(fill_width)

            color = self._BAR_DONE_COLOR if pct >= 100 else self._BAR_COLOR
            painter.fillRect(bar_rect, color)

            # Text darüber zeichnen
            painter.save()
            if "Fertig" in text or "Übersprungen" in text:
                brush = index.data(Qt.ForegroundRole)
                painter.setPen(brush.color() if brush else Qt.black)
            elif "Fehler" in text:
                painter.setPen(Qt.red)
            else:
                painter.setPen(Qt.blue)
            painter.drawText(
                rect.adjusted(4, 0, -4, 0),
                Qt.AlignVCenter | Qt.AlignLeft,
                text,
            )
            painter.restore()
        else:
            # Kein Fortschritt → normales Rendering
            super().paint(painter, option, index)
