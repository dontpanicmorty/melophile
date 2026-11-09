"""Draws Melophile's icon (src/melophile/assets/icon.png, 1024 px): a plum-to-coral rounded square with a
white pair of eighth notes. Drawn with shapes only, so it looks the same on every machine.

    uv run python scripts/make_icon.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QTransform  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "src" / "melophile" / "assets" / "icon.png"
S = 1024


def main() -> int:
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    img = QImage(S, S, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # macOS icon grid: the shape fills 824 of 1024 px, centered
    m = 100
    body = QRectF(m, m, S - 2 * m, S - 2 * m)
    g = QLinearGradient(body.topLeft(), body.bottomRight())
    g.setColorAt(0.0, QColor("#5b2a86"))
    g.setColorAt(0.55, QColor("#8b3f8f"))
    g.setColorAt(1.0, QColor("#f2795b"))
    shape = QPainterPath()
    shape.addRoundedRect(body, 185, 185)
    p.fillPath(shape, g)

    # a soft highlight across the top
    hi = QLinearGradient(QPointF(0, m), QPointF(0, S / 2))
    hi.setColorAt(0, QColor(255, 255, 255, 46))
    hi.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillPath(shape, hi)

    # two note heads joined by a beam
    white = QColor("#ffffff")
    notes = QPainterPath()
    notes.setFillRule(Qt.FillRule.WindingFill)  # overlapping parts stay filled
    tilt = QTransform().rotate(-22)
    for cx, cy in ((385, 690), (665, 630)):
        head = QPainterPath()
        head.setFillRule(Qt.FillRule.WindingFill)
        head.addEllipse(QPointF(0, 0), 92, 66)
        notes.addPath(tilt.map(head).translated(cx, cy))
    stem_w = 34
    notes.addRect(QRectF(385 + 92 - stem_w * 0.9, 330, stem_w, 690 - 330 - 10))
    notes.addRect(QRectF(665 + 92 - stem_w * 0.9, 270, stem_w, 630 - 270 - 10))
    beam = QPainterPath()
    beam.setFillRule(Qt.FillRule.WindingFill)
    beam.moveTo(385 + 92 - stem_w * 0.9, 330)
    beam.lineTo(665 + 92 + stem_w * 0.1, 270)
    beam.lineTo(665 + 92 + stem_w * 0.1, 270 + 82)
    beam.lineTo(385 + 92 - stem_w * 0.9, 330 + 82)
    beam.closeSubpath()
    notes.addPath(beam)
    p.fillPath(notes.simplified(), white)
    p.end()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(OUT))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
