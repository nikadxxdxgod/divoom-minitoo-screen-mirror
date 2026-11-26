"""Settings dialog: page list on the left, pages on the right."""

from __future__ import annotations

import copy

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                             QFormLayout, QHBoxLayout, QLabel, QListWidget, QPushButton,
                             QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from ..bt_windows import open_bluetooth_settings
from ..i18n import LANGUAGES, _
from ..protocol import FORMATS
from ..settings import AppSettings
from .widgets import muted


def _page(title: str):
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(18, 14, 18, 14)
    h = QLabel(title)
    h.setProperty("heading", True)
    v.addWidget(h)
    form = QFormLayout()
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
    form.setHorizontalSpacing(14)
    form.setVerticalSpacing(10)
    v.addLayout(form)
    return w, v, form


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, debug_log: bool, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Settings"))
        self.resize(780, 520)
        self.result_settings = copy.deepcopy(settings)
        self.debug_log = debug_log
        s = self.result_settings

        self.pages = QListWidget()
        self.pages.setFixedWidth(180)
        self.stack = QStackedWidget()

        # --- General
        w, v, f = _page(_("General"))
        self.lang = QComboBox()
        for code, (native, english) in LANGUAGES.items():
            self.lang.addItem(native if native == english else f"{native} — {english}", code)
        i = self.lang.findData(s.language or "en")
        self.lang.setCurrentIndex(max(0, i))
        f.addRow(_("Language:"), self.lang)
        f.addRow("", muted(_("The new language is used after a restart.")))
        self.auto_connect = QCheckBox(_("Connect when the program starts"))
        self.auto_connect.setChecked(s.auto_connect)
        f.addRow("", self.auto_connect)
        self.auto_reconnect = QCheckBox(_("Reconnect automatically when the connection drops"))
        self.auto_reconnect.setChecked(s.auto_reconnect)
        f.addRow("", self.auto_reconnect)
        self.debug = QCheckBox(_("Detailed log (packets)"))
        self.debug.setChecked(debug_log)
        f.addRow("", self.debug)
        v.addStretch(1)
        self._add(_("General"), w)

        # --- Connection
        w, v, f = _page(_("Connection"))
        self.channel = QSpinBox()
        self.channel.setRange(1, 30)
        self.channel.setValue(s.channel)
        f.addRow(_("RFCOMM channel:"), self.channel)
        f.addRow("", muted(_("The MiniToo uses channel 1.")))
        bt = QPushButton(_("Open Windows Bluetooth settings"))
        bt.clicked.connect(open_bluetooth_settings)
        f.addRow("", bt)
        v.addWidget(muted(_("Pair the MiniToo once in Windows Settings (Bluetooth → Add device → "
                            "“Divoom MiniToo-Audio”). Close the Divoom app on your phone: the "
                            "speaker accepts only one connection at a time.")))
        v.addWidget(muted(_("A USB cable only powers the MiniToo and carries sound — it has no "
                            "data channel for the screen, so pictures always go over Bluetooth.")))
        v.addStretch(1)
        self._add(_("Connection"), w)

        # --- Transfer
        w, v, f = _page(_("Transfer"))
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.0, 60.0)
        self.interval.setDecimals(1)
        self.interval.setSuffix(" ms")
        self.interval.setValue(s.chunk_interval_ms)
        f.addRow(_("Pause between chunks:"), self.interval)
        self.auto_pacing = QCheckBox(_("Adjust the speed automatically"))
        self.auto_pacing.setChecked(s.auto_pacing)
        f.addRow("", self.auto_pacing)
        self.min_interval = QDoubleSpinBox()
        self.min_interval.setRange(0.0, 40.0)
        self.min_interval.setDecimals(1)
        self.min_interval.setSuffix(" ms")
        self.min_interval.setValue(s.min_interval_ms)
        f.addRow(_("Minimum pause:"), self.min_interval)
        self.req_timeout = QDoubleSpinBox()
        self.req_timeout.setRange(0.2, 15.0)
        self.req_timeout.setSuffix(" s")
        self.req_timeout.setValue(s.request_timeout)
        f.addRow(_("Wait for the device's reply:"), self.req_timeout)
        self.ack_timeout = QDoubleSpinBox()
        self.ack_timeout.setRange(0.2, 15.0)
        self.ack_timeout.setSuffix(" s")
        self.ack_timeout.setValue(s.ack_timeout)
        f.addRow(_("Wait for the confirmation:"), self.ack_timeout)
        v.addWidget(muted(_("Measured on a real MiniToo: every pause from 0 to 15 ms worked; "
                            "2–4 ms is the fastest. If pictures get damaged, use a longer pause "
                            "(e.g. 20 ms).")))
        v.addStretch(1)
        self._add(_("Transfer"), w)

        # --- Advanced
        w, v, f = _page(_("Advanced"))
        self.fmt = QComboBox()
        for key, fmt in FORMATS.items():
            self.fmt.addItem(_(fmt.label), key)
        self.fmt.setCurrentIndex(max(0, self.fmt.findData(s.fmt_key)))
        f.addRow(_("Picture format:"), self.fmt)
        self.max_kb = QSpinBox()
        self.max_kb.setRange(0, 60)
        self.max_kb.setSuffix(" KB")
        self.max_kb.setSpecialValueText(_("no limit"))
        self.max_kb.setValue(s.max_frame_kb)
        f.addRow(_("Maximum frame size:"), self.max_kb)
        self.hq = QCheckBox(_("High-quality scaling (LANCZOS)"))
        self.hq.setChecked(s.high_quality)
        f.addRow("", self.hq)
        self.keepalive = QSpinBox()
        self.keepalive.setRange(0, 120)
        self.keepalive.setSuffix(" s")
        self.keepalive.setSpecialValueText(_("off"))
        self.keepalive.setValue(int(s.keepalive_s))
        f.addRow(_("Re-send an unchanged screen every:"), self.keepalive)
        self.batch = QSpinBox()
        self.batch.setRange(1, 8)
        self.batch.setSpecialValueText(_("off"))
        self.batch.setValue(s.batch_frames)
        f.addRow(_("Frames per upload (experimental):"), self.batch)
        self.batch_interval = QSpinBox()
        self.batch_interval.setRange(50, 1000)
        self.batch_interval.setSingleStep(25)
        self.batch_interval.setSuffix(" ms")
        self.batch_interval.setValue(s.batch_interval_ms)
        f.addRow(_("Time between those frames:"), self.batch_interval)
        v.addWidget(muted(_("Safety: the program only ever sends two commands to the device — "
                            "picture upload (0x8B) and brightness (0x74). Everything else is "
                            "blocked in the code, and the firmware is never touched.")))
        v.addStretch(1)
        self._add(_("Advanced"), w)

        self.pages.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.pages.setCurrentRow(0)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("OK"))
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("primary", True)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Cancel"))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        body = QHBoxLayout()
        body.addWidget(self.pages)
        body.addWidget(self.stack, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(body, 1)
        lay.addWidget(buttons)

    def _add(self, title: str, page: QWidget) -> None:
        self.pages.addItem(title)
        self.stack.addWidget(page)

    def _accept(self) -> None:
        s = self.result_settings
        s.language = self.lang.currentData()
        s.auto_connect = self.auto_connect.isChecked()
        s.auto_reconnect = self.auto_reconnect.isChecked()
        self.debug_log = self.debug.isChecked()
        s.channel = self.channel.value()
        s.chunk_interval_ms = self.interval.value()
        s.auto_pacing = self.auto_pacing.isChecked()
        s.min_interval_ms = self.min_interval.value()
        s.request_timeout = self.req_timeout.value()
        s.ack_timeout = self.ack_timeout.value()
        s.fmt_key = self.fmt.currentData()
        s.max_frame_kb = self.max_kb.value()
        s.high_quality = self.hq.isChecked()
        s.keepalive_s = float(self.keepalive.value())
        s.batch_frames = self.batch.value()
        s.batch_interval_ms = self.batch_interval.value()
        self.accept()
