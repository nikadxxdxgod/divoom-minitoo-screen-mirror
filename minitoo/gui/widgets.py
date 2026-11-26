"""Small widgets and helpers."""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from PIL import Image
from PyQt6.QtCore import QObject, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QScrollArea,
                             QSizePolicy, QVBoxLayout, QWidget)

from ..i18n import _
from . import theme


class Invoker(QObject):
    """Call a function on the GUI thread from any thread."""

    _call = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self._call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def _run(self, fn) -> None:
        fn()

    def __call__(self, fn: Callable[[], Any]) -> None:
        self._call.emit(fn)


def run_in_background(invoker: Invoker, fn: Callable[[], Any],
                      on_done: Optional[Callable[[Any], None]] = None,
                      on_error: Optional[Callable[[BaseException], None]] = None,
                      name: str = "worker") -> threading.Thread:
    def worker():
        try:
            result = fn()
        except BaseException as e:  # noqa: BLE001
            if on_error is not None:
                invoker(lambda e=e: on_error(e))
            else:
                logging.getLogger("minitoo.gui").exception("background task")
            return
        if on_done is not None:
            invoker(lambda: on_done(result))

    t = threading.Thread(target=worker, name=name, daemon=True)
    t.start()
    return t


def pil_to_qimage(img: Image.Image) -> QImage:
    img = img.convert("RGB")
    w, h = img.size
    data = img.tobytes("raw", "RGB")
    return QImage(data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class StatusDot(QLabel):
    """Coloured dot + text for the status bar."""
    COLORS = {"off": theme.MUTED, "busy": theme.WARNING, "ok": theme.SUCCESS, "error": theme.DANGER}

    def set_state(self, kind: str, text: str) -> None:
        color = self.COLORS.get(kind, theme.MUTED)
        self.setText(f'<span style="color:{color}">●</span>&nbsp;{text}')


class StatTile(QFrame):
    def __init__(self, title: str, value: str = "—", tooltip: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("tile")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(0)
        self.value = QLabel(value)
        self.value.setObjectName("tileValue")
        self.title = QLabel(title)
        self.title.setObjectName("tileTitle")
        lay.addWidget(self.value)
        lay.addWidget(self.title)
        if tooltip:
            self.setToolTip(tooltip)

    def set(self, value: str) -> None:
        self.value.setText(value)


class PreviewWidget(QWidget):
    """A copy of the device screen: exactly the pixels that were sent,
    scaled by an integer factor (no blur), on a dark canvas."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._img: Optional[Any] = None
        self._size = (160, 128)
        self._caption = ""
        self._live = False
        self.setMinimumSize(360, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(800, 520)

    def set_format_size(self, w: int, h: int) -> None:
        self._size = (w, h)
        self.update()

    def set_live(self, live: bool) -> None:
        self._live = live
        self.update()

    def set_image(self, img: Optional[Image.Image], caption: str = "") -> None:
        self._img = pil_to_qimage(img) if img is not None else None
        if img is not None:
            self._size = img.size
        self._caption = caption
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.CANVAS))
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self._size
        avail_w, avail_h = self.width() - 80, self.height() - 90
        scale = max(1, min(avail_w // w, avail_h // h)) if avail_w > 0 and avail_h > 0 else 1
        sw, sh = w * scale, h * scale
        x = (self.width() - sw) // 2
        y = max(30, (self.height() - 24 - sh) // 2)
        # device body
        body = QRectF(x - 22, y - 22, sw + 44, sh + 44)
        path = QPainterPath()
        path.addRoundedRect(body, 22, 22)
        p.fillPath(path, QColor("#2a2c33"))
        p.setPen(QPen(QColor("#3d414c"), 1.2))
        p.drawPath(path)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        screen = QRectF(x, y, sw, sh)
        if self._img is not None:
            p.drawImage(screen, self._img)
        else:
            p.fillRect(screen, QColor("#050506"))
            p.setPen(QColor(theme.MUTED))
            p.drawText(screen, Qt.AlignmentFlag.AlignCenter, f"{w}×{h}")
        p.setPen(QPen(QColor("#000000"), 1))
        p.drawRect(screen)
        # caption
        p.setPen(QColor(theme.MUTED))
        caption = self._caption or _("Nothing sent yet")
        p.drawText(QRectF(0, body.bottom() + 8, self.width(), 22), Qt.AlignmentFlag.AlignCenter,
                   f"{caption}   ·   ×{scale}")
        # LIVE badge
        if self._live:
            f = QFont(p.font())
            f.setBold(True)
            p.setFont(f)
            text = _("LIVE")
            tw = p.fontMetrics().horizontalAdvance(text) + 26
            badge = QRectF(14, 12, tw, 24)
            bp = QPainterPath()
            bp.addRoundedRect(badge, 5, 5)
            p.fillPath(bp, QColor(theme.DANGER))
            p.setPen(QColor("white"))
            p.drawEllipse(QRectF(22, 20, 8, 8))
            p.drawText(badge.adjusted(16, 0, 0, 0), Qt.AlignmentFlag.AlignCenter, text)
        p.end()


class LogView(QPlainTextEdit):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("log")
        self.setReadOnly(True)
        self.setMaximumBlockCount(3000)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

    def add(self, text: str, level: int = logging.INFO) -> None:
        color = {logging.WARNING: theme.WARNING, logging.ERROR: theme.DANGER,
                 logging.CRITICAL: theme.DANGER, logging.DEBUG: "#6f7787"}.get(level)
        esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.appendHtml(f'<span style="color:{color}">{esc}</span>' if color else esc)
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())


class QtLogHandler(logging.Handler):
    """logging -> log view (on the GUI thread)."""

    def __init__(self, invoker: Invoker, view: LogView) -> None:
        super().__init__()
        self.invoker = invoker
        self.view = view
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:  # pragma: no cover
            return
        level = record.levelno
        self.invoker(lambda: self.view.add(msg, level))


def hrow(*widgets, stretch_last: bool = False, spacing: int = 6) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    if stretch_last:
        lay.addStretch(1)
    return lay


def muted(text: str, wrap: bool = True) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("muted", True)
    lbl.setWordWrap(wrap)
    return lbl


def dock_body(inner_layout, scroll: bool = True) -> QWidget:
    """Content widget for a dock: padded, optionally scrollable."""
    body = QWidget()
    body.setObjectName("dockBody")
    inner_layout.setContentsMargins(10, 10, 10, 10)
    body.setLayout(inner_layout)
    if not scroll:
        return body
    area = QScrollArea()
    area.setWidget(body)
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setFrameShape(QFrame.Shape.NoFrame)
    # never narrower than the content (otherwise it is clipped on the right)
    area.setMinimumWidth(body.minimumSizeHint().width() + 14)
    holder = QWidget()
    holder.setObjectName("dockBody")
    lay = QVBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(area)
    return holder
