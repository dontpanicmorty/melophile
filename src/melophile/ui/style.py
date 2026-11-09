"""Melophile's look: the system's light or dark appearance, with one plum accent and soft cards."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#8b3f8f"
ACCENT_DARK = "#c77dcb"
DANGER = "#c2410c"


def is_dark(app: QApplication) -> bool:
    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


def apply(app: QApplication) -> None:
    dark = is_dark(app)
    accent = ACCENT_DARK if dark else ACCENT
    pal = app.palette()
    pal.setColor(QPalette.ColorRole.Highlight, QColor(accent))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#1a1020" if dark else "#ffffff"))
    pal.setColor(QPalette.ColorRole.Link, QColor(accent))
    app.setPalette(pal)
    card = "rgba(255,255,255,0.05)" if dark else "rgba(0,0,0,0.035)"
    line = "rgba(255,255,255,0.10)" if dark else "rgba(0,0,0,0.10)"
    side = "rgba(255,255,255,0.03)" if dark else "rgba(0,0,0,0.04)"
    app.setStyleSheet(f"""
        QWidget#sidebar {{ background: {side}; border-right: 1px solid {line}; }}
        QListWidget#nav {{ border: none; background: transparent; font-size: 14px; outline: 0; }}
        QListWidget#nav::item {{ padding: 8px 12px; margin: 1px 8px; border-radius: 7px; }}
        QListWidget#nav::item:selected {{ background: {accent}; color: palette(highlighted-text); }}
        QLabel#brand {{ font-size: 20px; font-weight: 700; padding: 14px 18px 2px; }}
        QLabel#tagline {{ color: palette(placeholder-text); padding: 0 18px 12px; }}
        QLabel#h1 {{ font-size: 24px; font-weight: 700; }}
        QLabel#h2 {{ font-size: 15px; font-weight: 600; }}
        QLabel#muted {{ color: palette(placeholder-text); }}
        QLabel#metric {{ font-size: 26px; font-weight: 700; }}
        QFrame#card {{ background: {card}; border: 1px solid {line}; border-radius: 10px; }}
        QPushButton#primary {{ background: {accent}; color: palette(highlighted-text); border: none;
                              border-radius: 6px; padding: 6px 16px; font-weight: 600; }}
        QPushButton#primary:disabled {{ background: {line}; color: palette(placeholder-text); }}
        QPushButton#danger {{ color: {DANGER}; }}
        QTableWidget {{ border: 1px solid {line}; border-radius: 6px; }}
    """)
    f = app.font()
    if f.pointSize() < 13:
        f.setPointSize(13)
    app.setFont(f)


def heading(text: str, kind: str = "h1"):
    from PySide6.QtWidgets import QLabel

    label = QLabel(text)
    label.setObjectName(kind)
    label.setWordWrap(kind == "muted")
    return label


def mono() -> QFont:
    f = QFont("Menlo")
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f
