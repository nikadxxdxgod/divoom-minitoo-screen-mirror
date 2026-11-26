"""Screen region picker: drag a rectangle on a screenshot.

Works on a screenshot in physical pixels, so the result is right with
Windows display scaling (125 %, 150 % ...) and several monitors.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                             QWidget)

from ..capture import Rect, ScreenGrabber, list_monitors, virtual_screen
from ..i18n import _
from . import theme
from .widgets import pil_to_qimage


class _Canvas(QWidget):
    changed = pyqtSignal()

    def __init__(self, image, vs: Rect, parent=None) -> None:
        super().__init__(parent)
        self._img = pil_to_qimage(image)
        self._vs = vs
        self._ratio: Optional[float] = None
        self._sel: Optional[QRectF] = None      # in screenshot pixels
        self._anchor: Optional[QPointF] = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMinimumSize(600, 300)

    # coordinate conversion
    def _display_rect(self) -> QRectF:
        iw, ih = self._img.width(), self._img.height()
        s = min(self.width() / iw, self.height() / ih)
        w, h = iw * s, ih * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _to_img(self, p: QPointF) -> QPointF:
        d = self._display_rect()
        s = self._img.width() / d.width()
        x = min(max((p.x() - d.left()) * s, 0), self._img.width())
        y = min(max((p.y() - d.top()) * s, 0), self._img.height())
        return QPointF(x, y)

    def _phys_scale(self) -> float:
        return self._vs.width / self._img.width()

    def set_ratio(self, ratio: Optional[float]) -> None:
        self._ratio = ratio
        if self._sel is not None and ratio:
            r = self._sel
            h = r.width() / ratio
            self._sel = QRectF(r.left(), r.top(), r.width(), min(h, self._img.height() - r.top()))
        self.update()
        self.changed.emit()

    def set_selection_phys(self, rect: Optional[Rect]) -> None:
        if rect is None:
            self._sel = None
        else:
            k = 1 / self._phys_scale()
            self._sel = QRectF((rect.left - self._vs.left) * k, (rect.top - self._vs.top) * k,
                               rect.width * k, rect.height * k)
        self.update()
        self.changed.emit()

    def selection_phys(self) -> Optional[Rect]:
        r = self._sel
        if r is None or r.width() < 2 or r.height() < 2:
            return None
        k = self._phys_scale()
        out = Rect(round(self._vs.left + r.left() * k), round(self._vs.top + r.top() * k),
                   round(r.width() * k), round(r.height() * k))
        return out.intersect(self._vs)

    def select_monitor(self, rect: Rect) -> None:
        self.set_selection_phys(rect)

    # mouse
    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._anchor = self._to_img(ev.position())
            self._sel = QRectF(self._anchor, self._anchor)
            self.update()

    def mouseMoveEvent(self, ev) -> None:
        if self._anchor is None:
            return
        p = self._to_img(ev.position())
        a = self._anchor
        dx, dy = p.x() - a.x(), p.y() - a.y()
        if self._ratio:
            w = abs(dx)
            h = w / self._ratio
            if h > abs(dy) and abs(dy) > 0:
                h = abs(dy)
                w = h * self._ratio
            dx = w if dx >= 0 else -w
            dy = h if dy >= 0 else -h
        self._sel = QRectF(a, QPointF(a.x() + dx, a.y() + dy)).normalized()
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, ev) -> None:
        self._anchor = None
        self.changed.emit()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.BG))
        d = self._display_rect()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage(d, self._img)
        p.fillRect(d, QColor(0, 0, 0, 120))
        if self._sel is not None:
            s = d.width() / self._img.width()
            r = QRectF(d.left() + self._sel.left() * s, d.top() + self._sel.top() * s,
                       self._sel.width() * s, self._sel.height() * s)
            src = QRectF(self._sel)
            p.drawImage(r, self._img, src)
            p.setPen(QPen(QColor(theme.ACCENT), 2))
            p.drawRect(r)
        p.end()


class RegionDialog(QDialog):
    def __init__(self, current: Optional[Rect] = None, ratio: float = 160 / 128, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Select a screen region"))
        self.result_rect: Optional[Rect] = None
        vs = virtual_screen()
        with ScreenGrabber() as g:
            shot = g.grab_full(vs, max_side=2400)
        self._ratio_value = ratio

        self.canvas = _Canvas(shot, vs, self)
        self.info = QLabel()
        self.keep_ratio = QCheckBox(_("Keep the MiniToo screen proportions ({r}:1)", r=f"{ratio:.2f}"))
        self.keep_ratio.setChecked(True)
        self.keep_ratio.toggled.connect(lambda on: self.canvas.set_ratio(self._ratio_value if on else None))
        self.canvas.set_ratio(ratio)

        mon_row = QHBoxLayout()
        mon_row.addWidget(QLabel(_("Whole display:")))
        for m in list_monitors():
            b = QPushButton(_("Display {n}", n=m.index))
            b.clicked.connect(lambda _=False, r=m.rect: self._pick_monitor(r))
            mon_row.addWidget(b)
        mon_row.addStretch(1)

        ok = QPushButton(_("Select"))
        ok.setProperty("primary", True)
        ok.clicked.connect(self._accept)
        cancel = QPushButton(_("Cancel"))
        cancel.clicked.connect(self.reject)
        self.ok_btn = ok

        bottom = QHBoxLayout()
        bottom.addWidget(self.keep_ratio)
        bottom.addSpacing(16)
        bottom.addWidget(self.info, 1)
        bottom.addWidget(cancel)
        bottom.addWidget(ok)

        lay = QVBoxLayout(self)
        hint = QLabel(_("Drag with the mouse over the area that should appear on the MiniToo."))
        hint.setProperty("muted", True)
        lay.addWidget(hint)
        lay.addWidget(self.canvas, 1)
        lay.addLayout(mon_row)
        lay.addLayout(bottom)

        self.canvas.changed.connect(self._update_info)
        if current is not None:
            self.keep_ratio.setChecked(False)
            self.canvas.set_selection_phys(current)
        self._update_info()

        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None:
            self.resize(int(screen.width() * 0.85), int(screen.height() * 0.85))

    def _pick_monitor(self, rect: Rect) -> None:
        self.keep_ratio.setChecked(False)
        self.canvas.select_monitor(rect)

    def _update_info(self) -> None:
        r = self.canvas.selection_phys()
        self.ok_btn.setEnabled(r is not None and r.width >= 16 and r.height >= 16)
        self.info.setText(_("Selected: {r}", r=r) if r else _("Nothing selected"))

    def _accept(self) -> None:
        r = self.canvas.selection_phys()
        if r is not None and r.width >= 16 and r.height >= 16:
            self.result_rect = r
            self.accept()
