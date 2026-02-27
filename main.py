#!/usr/bin/env python3
"""MJPEG Converter – Haupteinstiegspunkt.

Startet die PySide6-GUI für die MJPEG-Konvertierung mit:
  - Profil-System (KI Auswertung / YouTube / Benutzerdefiniert)
  - NVIDIA NVENC Hardware-Encoding (falls verfügbar)
  - Halbzeiten zusammenführen mit Titelkarten
  - YouTube-Upload mit Playlist-Unterstützung

Aufruf:
    python main.py
"""

import sys

from PySide6.QtWidgets import QApplication

from src.app import ConverterApp


def main():
    app = QApplication(sys.argv)
    window = ConverterApp()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
