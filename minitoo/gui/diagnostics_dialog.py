"""Diagnostics: system check and device tests."""

from __future__ import annotations

import platform
import statistics
import sys
import time
from typing import Callable, Optional

from PyQt6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog, QHBoxLayout,
                             QPlainTextEdit, QPushButton, QVBoxLayout)

from .. import APP_NAME, __version__
from ..device import MiniTooDevice
from ..i18n import _
from ..imaging import CARD_COLORS, ImageOptions, make_number_card, make_test_pattern
from .widgets import Invoker, muted, run_in_background


class DiagnosticsDialog(QDialog):
    def __init__(self, parent, device_getter: Callable[[], Optional[MiniTooDevice]],
                 invoker: Invoker, is_busy: Callable[[], bool]) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Diagnostics"))
        self.resize(860, 620)
        self._device = device_getter
        self._invoker = invoker
        self._is_busy = is_busy
        self._busy = False

        self.text = QPlainTextEdit()
        self.text.setObjectName("log")
        self.text.setReadOnly(True)
        self.inquiry_cb = QCheckBox(_("Also search for nearby Bluetooth devices (~7 s)"))
        self.inquiry_cb.setChecked(True)

        self.sys_btn = QPushButton(_("Check system"))
        self.sys_btn.setProperty("primary", True)
        self.sys_btn.clicked.connect(self._run_system)
        self.img_btn = QPushButton(_("Test image"))
        self.img_btn.clicked.connect(self._run_test_image)
        self.rep_btn = QPushButton(_("Speed test (10 frames)"))
        self.rep_btn.clicked.connect(self._run_repeat)
        copy_btn = QPushButton(_("Copy"))
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self.text.toPlainText()))
        save_btn = QPushButton(_("Save…"))
        save_btn.clicked.connect(self._save)
        close_btn = QPushButton(_("Close"))
        close_btn.clicked.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(muted(_("“Check system” sends nothing to the device. The test buttons only "
                              "send pictures (command 0x8B); the device must be connected and "
                              "mirroring stopped.")))
        lay.addWidget(self.inquiry_cb)
        lay.addWidget(self.text, 1)
        row = QHBoxLayout()
        for b in (self.sys_btn, self.img_btn, self.rep_btn):
            row.addWidget(b)
        row.addStretch(1)
        for b in (copy_btn, save_btn, close_btn):
            row.addWidget(b)
        lay.addLayout(row)
        self._update_buttons()

    def _line(self, text: str = "") -> None:
        self._invoker(lambda: self.text.appendPlainText(text))

    def _update_buttons(self) -> None:
        dev = self._device()
        ready = dev is not None and dev.is_connected and not self._is_busy()
        self.sys_btn.setEnabled(not self._busy)
        self.img_btn.setEnabled(ready and not self._busy)
        self.rep_btn.setEnabled(ready and not self._busy)

    def _start(self, fn) -> None:
        if self._busy:
            return
        self._busy = True
        self._update_buttons()

        def done(_r=None):
            self._busy = False
            self._update_buttons()

        def fail(e):
            self.text.appendPlainText(f"✗ {e}")
            done()

        run_in_background(self._invoker, fn, done, fail, name="diagnostics")

    def _save(self) -> None:
        path, _f = QFileDialog.getSaveFileName(self, _("Save report"), "minitoo_diagnostics.txt",
                                               "Text (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.text.toPlainText())

    # ------------------------------------------------------------------ system

    def _run_system(self) -> None:
        inquiry = self.inquiry_cb.isChecked()
        self.text.clear()
        self._start(lambda: self._system(inquiry))

    def _system(self, inquiry: bool) -> None:
        L = self._line
        L(f"=== {APP_NAME} {__version__} — {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
        L(f"Windows: {platform.platform()}  ·  Python {sys.version.split()[0]} ({platform.architecture()[0]})")
        try:
            import PIL
            from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
            L(f"PyQt6 {PYQT_VERSION_STR} / Qt {QT_VERSION_STR}  ·  Pillow {PIL.__version__}")
        except Exception:
            pass
        from ..encoder import ZSTD_BACKEND
        from ..video import available as video_available
        L(f"zstd: {ZSTD_BACKEND or '-'}  ·  video (PyAV): {'OK' if video_available() else '-'}")
        L()

        from ..bt_windows import (bluetooth_socket_supported, find_devices,
                                  list_bluetooth_com_ports, list_radios, usb_divoom_devices)
        L("— Bluetooth —")
        try:
            radios = list_radios()
            for r in radios:
                L(f"✓ {_('Adapter')}: {r.name or '-'}  [{r.address}]")
            if not radios:
                L("✗ " + _("No Bluetooth adapter found, or it is turned off."))
        except Exception as e:
            L(f"✗ {e}")
        L(("✓ " if bluetooth_socket_supported() else "✗ ") + "Python Bluetooth socket (RFCOMM)")
        try:
            devs = find_devices(inquiry=inquiry, timeout_multiplier=5)
            L(_("Devices: {n}", n=len(devs)))
            for d in devs:
                mark = "★" if d.is_divoom else " "
                L(f"  {mark} {d.label()}  class 0x{d.device_class:06X}")
            if not any(d.is_divoom for d in devs):
                L("  ! " + _("No Divoom device found — turn the MiniToo on, disconnect it from "
                             "the phone and search again."))
            elif not any(d.is_divoom and d.paired for d in devs):
                L("  ! " + _("The MiniToo is not paired — Windows Settings → Bluetooth → Add device."))
        except Exception as e:
            L(f"✗ {e}")
        try:
            for p in list_bluetooth_com_ports():
                L(f"  COM: {p.device} — {p.description}  {p.mac or ''}")
            usb = usb_divoom_devices()
            if usb:
                L("  USB: " + ", ".join(usb) + " — " + _("power and sound only"))
        except Exception as e:
            L(f"  {e}")
        L()

        L("— " + _("Screen capture") + " —")
        try:
            from ..capture import CaptureSource, ScreenGrabber, list_monitors
            from ..imaging import process_frame
            for m in list_monitors():
                L(f"  {m.label()}  {m.rect}")
            with ScreenGrabber() as g:
                times = []
                for _i in range(5):
                    t0 = time.perf_counter()
                    raw, cur, _r = g.grab(CaptureSource("monitor", 1))
                    process_frame(raw, (160, 128), ImageOptions(), cur)
                    times.append((time.perf_counter() - t0) * 1000)
            L(f"✓ {_('Display {n}', n=1)}: {statistics.median(times):.0f} ms")
        except Exception as e:
            L(f"✗ {e}")
        L()

        L("— " + _("Connection") + " —")
        dev = self._device()
        if dev is None:
            L("  " + _("Not connected"))
        else:
            st = dev.stats
            L(f"  {dev.description} · {dev.state.value}")
            L(f"  go-ahead: {dev.go_ahead_supported} · ack: {dev.ack_supported} · "
              f"interval {dev.current_interval_ms:.1f} ms")
            L(f"  tx {st.tx_bytes} B · rx {st.rx_bytes} B ({st.rx_frames}) · ok {st.uploads_ok} · "
              f"failed {st.uploads_failed} · resends {st.resends} · reconnects {st.reconnects}")
            for h in st.other_frames[-5:]:
                L(f"    {h}")
            if dev.last_error:
                L(f"  {dev.last_error}")
        L("=== " + _("Done") + " ===")

    # ------------------------------------------------------------------ device tests

    def _run_test_image(self) -> None:
        dev = self._device()
        if dev is not None:
            self._start(lambda: self._test_image(dev))

    def _test_image(self, dev: MiniTooDevice) -> None:
        from ..mirror import send_still
        L = self._line
        L()
        L(f"— {_('Test image')} ({time.strftime('%H:%M:%S')}) —")
        ef, res = send_still(dev, make_test_pattern(160, 128, "MiniToo"), ImageOptions())
        L(f"  {res.summary()}")
        L("  " + _("Expected: colour bars at the bottom; red top-left, green top-right, "
                   "blue bottom-left and yellow bottom-right corner."))

    def _run_repeat(self) -> None:
        dev = self._device()
        if dev is not None:
            self._start(lambda: self._repeat(dev))

    def _repeat(self, dev: MiniTooDevice, count: int = 10) -> None:
        from ..encoder import FrameEncoder
        L = self._line
        L()
        L(f"— {_('Speed test (10 frames)')} ({time.strftime('%H:%M:%S')}) —")
        enc = FrameEncoder("jpeg160", 90)
        totals, acked = [], 0
        t_start = time.perf_counter()
        for i in range(1, count + 1):
            img = make_number_card(160, 128, str(i), time.strftime("%H:%M:%S"),
                                   CARD_COLORS[i % len(CARD_COLORS)])
            ef = enc.encode([img])
            res = dev.upload(ef.payload, ef.fmt.start_extra)
            totals.append(res.total_ms)
            acked += res.acked
            L(f"  {i:2d}: {res.summary()}")
            if not res.ok and not dev.is_connected:
                break
        span = time.perf_counter() - t_start
        if totals:
            L("  " + _("Average {avg} ms per frame · {fps} frames/s · confirmed {ok}/{n}",
                       avg=round(statistics.mean(totals)), fps=f"{len(totals) / span:.2f}",
                       ok=acked, n=len(totals)))
