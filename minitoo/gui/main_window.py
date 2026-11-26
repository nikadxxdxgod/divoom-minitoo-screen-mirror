"""Main window: big preview in the middle, docks at the bottom."""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import List, Optional

from PIL import Image, ImageSequence
from PyQt6.QtCore import QByteArray, QProcess, Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QKeySequence
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDockWidget, QFileDialog,
                             QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar,
                             QPushButton, QSizePolicy, QSlider, QSpinBox, QStackedWidget,
                             QVBoxLayout, QWidget)

from .. import APP_NAME, REPO_URL, __version__
from ..bt_windows import (BtDevice, find_devices, list_bluetooth_com_ports, normalize_mac,
                          open_bluetooth_settings, usb_divoom_devices)
from ..capture import CaptureSource, Rect, WindowInfo, find_window, list_monitors, list_windows
from ..device import (STATE_LABELS, DeviceState, MiniTooDevice, UploadSettings,
                      make_transport_factory)
from ..i18n import N_, _, current_language
from ..imaging import ImageOptions, make_test_pattern, process_frame
from ..mirror import MirrorConfig, MirrorEngine, MirrorStats, send_animation, send_still
from ..protocol import FORMATS, MAX_SAFE_PAYLOAD
from ..settings import AppSettings, app_dir
from .widgets import (Invoker, LogView, PreviewWidget, QtLogHandler, StatTile, StatusDot,
                      dock_body, hrow, muted, run_in_background)

log = logging.getLogger("minitoo.gui")

# (label, (JPEG quality, 4:4:4)) - FPS measured on a real MiniToo (whole display)
PRESETS = [(N_("Custom"), None),
           (N_("Best quality (95, full colour) · ≈4–5 FPS"), (95, True)),
           (N_("Balanced (80) · ≈7 FPS"), (80, False)),
           (N_("Fastest (60) · more FPS, less detail"), (60, False))]
FIT_LABELS = [(N_("Fit — whole picture, with bars"), "fit"),
              (N_("Fill — crop the edges"), "fill"),
              (N_("Stretch — ignore proportions"), "stretch")]
ROT_LABELS = [("0°", 0), (N_("90° clockwise"), 90), ("180°", 180), ("270°", 270)]
CONN_LABELS = [("Bluetooth", "rfcomm"), (N_("COM port (Bluetooth serial)"), "serial"),
               (N_("Simulator — no device"), "simulator")]
METHOD_LABELS = [(N_("From screen — what is visible"), "screen"),
                 (N_("From window — even if covered"), "printwindow")]
RESTART_TITLE = N_("Restart required")
RESTART_TEXT = N_("The new language is used after a restart. Restart now?")
SOURCE_ICONS = {"monitor": "🖥", "window": "🗔", "region": "⬚", "image": "🖼", "video": "🎬"}
CLIP_BUDGET = 350_000


def _slider(lo: int, hi: int, value: int) -> QSlider:
    s = QSlider(Qt.Orientation.Horizontal)
    s.setRange(lo, hi)
    s.setValue(value)
    return s


def _select_data(combo: QComboBox, value) -> None:
    i = combo.findData(value)
    if i >= 0:
        combo.setCurrentIndex(i)


def _combo(items) -> QComboBox:
    c = QComboBox()
    for label, key in items:
        c.addItem(_(label), key)
    c.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(12)
    c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return c


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings, persist: bool = True, icon_path: str = "") -> None:
        super().__init__()
        self.s = settings
        self.persist = persist          # False = do not save settings (simulator / demo)
        self.icon_path = icon_path
        self.invoker = Invoker()
        self.device: Optional[MiniTooDevice] = None
        self.engine: Optional[MirrorEngine] = None
        self.video = None               # video.VideoPlayer
        self._devices: List[BtDevice] = []
        self._windows: List[WindowInfo] = []
        self._region: Optional[Rect] = Rect.from_list(settings.region)
        self._still_frames: List[Image.Image] = []
        self._still_speed = 100
        self._still_name = ""
        self._uploading = False
        self._scanning = False
        self._loading = True
        self._debug_log = False
        self._source_kind = settings.source_kind
        self._monitor_index = settings.monitor
        self._slider_dragging = False

        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1360, 860)
        self.preview = PreviewWidget()
        self.setCentralWidget(self.preview)
        self.setDockNestingEnabled(True)
        self._build_menu()
        self._build_docks()
        self._build_statusbar()
        self._attach_logging()
        self._load_settings()
        self._loading = False
        self._refresh_known_devices()
        self._refresh_sources()
        self._refresh_windows()
        self._refresh_com_ports()
        self._update_controls()
        self._usb_timer = QTimer(self)
        self._usb_timer.timeout.connect(self._check_usb)
        self._usb_timer.start(5000)
        QTimer.singleShot(400, self._check_usb)
        self._video_timer = QTimer(self)
        self._video_timer.timeout.connect(self._tick_video)
        self._video_timer.start(200)
        log.info("%s %s · %s", APP_NAME, __version__, AppSettings.path())
        if self.s.auto_connect and (self.s.address or self.s.connection == "simulator"):
            QTimer.singleShot(300, self._connect)

    # ================================================================ build

    def _build_menu(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu(_("&File"))
        a = m.addAction(_("Open image…"), self._open_image)
        a.setShortcut(QKeySequence("Ctrl+O"))
        a = m.addAction(_("Open video…"), self._open_video)
        a.setShortcut(QKeySequence("Ctrl+Shift+O"))
        m.addSeparator()
        a = m.addAction(_("Settings"), self._open_settings)
        a.setShortcut(QKeySequence("Ctrl+,"))
        m.addSeparator()
        m.addAction(_("Exit"), self.close)
        self.view_menu = mb.addMenu(_("&View"))
        t = mb.addMenu(_("&Tools"))
        t.addAction(_("Diagnostics…"), self._open_diagnostics)
        t.addAction(_("Windows Bluetooth settings…"), open_bluetooth_settings)
        t.addAction(_("Open log folder"), lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(app_dir())))
        h = mb.addMenu(_("&Help"))
        h.addAction(_("Documentation"), lambda: QDesktopServices.openUrl(QUrl(REPO_URL + "#readme")))
        h.addAction(_("Report a problem"), lambda: QDesktopServices.openUrl(QUrl(REPO_URL + "/issues")))
        h.addAction(_("Check for updates"), lambda: QDesktopServices.openUrl(QUrl(REPO_URL + "/releases")))
        h.addSeparator()
        h.addAction(_("About"), self._about)

    def _dock(self, name: str, title: str, widget: QWidget) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setObjectName(name)
        d.setWidget(widget)
        d.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                      | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                      | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        return d

    def _build_docks(self) -> None:
        self.dock_sources = self._dock("sources", _("Sources"), self._build_sources())
        self.dock_props = self._dock("source_settings", _("Source settings"), self._build_props())
        self.dock_picture = self._dock("picture", _("Picture"), self._build_picture())
        self.dock_device = self._dock("device", _("Device"), self._build_device())
        self.dock_stats = self._dock("stats", _("Statistics"), self._build_stats())
        self.dock_log = self._dock("log", _("Log"), self._build_log())
        self.dock_controls = self._dock("controls", _("Controls"), self._build_controls())
        bottom = Qt.DockWidgetArea.BottomDockWidgetArea
        right = Qt.DockWidgetArea.RightDockWidgetArea
        # right column (full height): device / statistics / log + controls
        self.setCorner(Qt.Corner.BottomRightCorner, right)
        self.setCorner(Qt.Corner.TopRightCorner, right)
        self.addDockWidget(right, self.dock_device)
        self.splitDockWidget(self.dock_device, self.dock_controls, Qt.Orientation.Vertical)
        self.tabifyDockWidget(self.dock_device, self.dock_stats)
        self.tabifyDockWidget(self.dock_stats, self.dock_log)
        self.dock_device.raise_()
        # bottom row: sources | source settings | picture
        self.addDockWidget(bottom, self.dock_sources)
        self.splitDockWidget(self.dock_sources, self.dock_props, Qt.Orientation.Horizontal)
        self.splitDockWidget(self.dock_props, self.dock_picture, Qt.Orientation.Horizontal)
        self.resizeDocks([self.dock_sources, self.dock_props, self.dock_picture], [200, 420, 420],
                         Qt.Orientation.Horizontal)
        self.resizeDocks([self.dock_sources], [300], Qt.Orientation.Vertical)
        self.resizeDocks([self.dock_device], [360], Qt.Orientation.Horizontal)
        self.resizeDocks([self.dock_device, self.dock_controls], [460, 260], Qt.Orientation.Vertical)
        self._default_state = self.saveState()
        for d in (self.dock_sources, self.dock_props, self.dock_picture, self.dock_device,
                  self.dock_stats, self.dock_log, self.dock_controls):
            self.view_menu.addAction(d.toggleViewAction())
        self.view_menu.addSeparator()
        self.view_menu.addAction(_("Reset layout"), lambda: self.restoreState(self._default_state))

    # --- Sources
    def _build_sources(self) -> QWidget:
        v = QVBoxLayout()
        self.source_list = QListWidget()
        self.source_list.currentItemChanged.connect(self._on_source_picked)
        v.addWidget(self.source_list)
        return dock_body(v, scroll=False)

    # --- Source settings
    def _build_props(self) -> QWidget:
        self.props = QStackedWidget()
        # monitor
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self.monitor_info = muted("")
        lay.addWidget(self.monitor_info)
        lay.addStretch(1)
        self.page_monitor = self.props.addWidget(w)
        # window
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(0, 0, 0, 0)
        self.window_combo = _combo([])
        self.window_combo.currentIndexChanged.connect(lambda *_a: self._on_option_changed())
        refresh = QPushButton("↻")
        refresh.setFixedWidth(34)
        refresh.setToolTip(_("Refresh the window list"))
        refresh.clicked.connect(self._refresh_windows)
        f.addRow(_("Window:"), hrow(self.window_combo, refresh))
        self.client_only_cb = QCheckBox(_("Content only (no frame or title bar)"))
        self.client_only_cb.toggled.connect(lambda *_a: self._on_option_changed())
        f.addRow("", self.client_only_cb)
        self.method_combo = _combo(METHOD_LABELS)
        self.method_combo.setToolTip(_("“From window” also captures covered windows, but some "
                                       "programs (games, video players) may appear black."))
        self.method_combo.currentIndexChanged.connect(lambda *_a: self._on_option_changed())
        f.addRow(_("Capture:"), self.method_combo)
        self.page_window = self.props.addWidget(w)
        # region
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self.region_btn = QPushButton(_("Select region…"))
        self.region_btn.clicked.connect(self._pick_region)
        self.region_label = muted("")
        lay.addWidget(self.region_btn)
        lay.addWidget(self.region_label)
        lay.addWidget(muted(_("Tip: a small region (e.g. one panel or a clock) looks much "
                              "sharper on the 160×128 screen than the whole display.")))
        lay.addStretch(1)
        self.page_region = self.props.addWidget(w)
        # image
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        open_btn = QPushButton(_("Open image…"))
        open_btn.clicked.connect(self._open_image)
        test_btn = QPushButton(_("Test pattern"))
        test_btn.setToolTip(_("Colour bars and coloured corners — shows whether the picture "
                              "fills the screen and is the right way round."))
        test_btn.clicked.connect(self._use_test_pattern)
        lay.addLayout(hrow(open_btn, test_btn))
        self.still_label = muted(_("No image selected"))
        lay.addWidget(self.still_label)
        self.still_progress = QProgressBar()
        self.still_progress.setRange(0, 100)
        lay.addWidget(self.still_progress)
        self.still_result = muted("")
        lay.addWidget(self.still_result)
        lay.addWidget(muted(_("PNG, JPEG, BMP, WebP and GIF (animated GIFs play on the device).")))
        lay.addStretch(1)
        self.page_image = self.props.addWidget(w)
        # video
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        open_v = QPushButton(_("Open video…"))
        open_v.clicked.connect(self._open_video)
        lay.addWidget(open_v)
        self.video_label = muted(_("No video selected"))
        lay.addWidget(self.video_label)
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedWidth(40)
        self.play_btn.setToolTip(_("Play / pause"))
        self.play_btn.clicked.connect(self._toggle_play)
        self.video_slider = _slider(0, 1000, 0)
        self.video_slider.sliderPressed.connect(lambda: setattr(self, "_slider_dragging", True))
        self.video_slider.sliderReleased.connect(self._slider_released)
        self.video_slider.sliderMoved.connect(self._slider_moved)
        self.video_time = QLabel("0:00 / 0:00")
        lay.addLayout(hrow(self.play_btn, self.video_slider, self.video_time))
        self.loop_cb = QCheckBox(_("Loop"))
        self.loop_cb.toggled.connect(self._on_loop)
        lay.addWidget(self.loop_cb)
        self.sound_combo = _combo([])
        self.sound_combo.setToolTip(_("If the sound plays on the MiniToo itself, the speaker may "
                                      "show its sound visualizer instead of the picture — choose "
                                      "the PC speakers to avoid that."))
        self.sound_combo.currentIndexChanged.connect(self._on_sound_changed)
        lay.addLayout(hrow(QLabel(_("Sound:")), self.sound_combo))
        g = QGroupBox(_("Upload a clip as an animation"))
        gf = QFormLayout(g)
        self.clip_seconds = QSpinBox()
        self.clip_seconds.setRange(1, 30)
        self.clip_seconds.setSuffix(" s")
        gf.addRow(_("Length:"), self.clip_seconds)
        self.clip_fps = QSpinBox()
        self.clip_fps.setRange(2, 25)
        self.clip_fps.setSuffix(" fps")
        gf.addRow(_("Frames per second:"), self.clip_fps)
        self.clip_btn = QPushButton(_("Upload clip from current position"))
        self.clip_btn.clicked.connect(self._upload_clip)
        gf.addRow(self.clip_btn)
        self.clip_progress = QProgressBar()
        self.clip_progress.setRange(0, 100)
        gf.addRow(self.clip_progress)
        self.clip_result = muted(_("The device plays the clip by itself, smoothly and in a loop. "
                                   "Longer clips get lower quality (upload limit ~350 KB)."))
        gf.addRow(self.clip_result)
        lay.addWidget(g)
        lay.addStretch(1)
        self.page_video = self.props.addWidget(w)

        v = QVBoxLayout()
        v.addWidget(self.props)
        return dock_body(v)

    # --- Picture
    def _build_picture(self) -> QWidget:
        f = QFormLayout()
        f.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.preset_combo = _combo(PRESETS)
        self.preset_combo.setToolTip(_("FPS measured on a real MiniToo with the whole display "
                                       "mirrored; it depends on the picture."))
        self.preset_combo.activated.connect(self._apply_preset)
        f.addRow(_("Mode:"), self.preset_combo)
        self.quality_slider = _slider(20, 100, 95)
        self.quality_label = QLabel()
        self.quality_label.setMinimumWidth(28)
        self.quality_slider.valueChanged.connect(self._on_quality_changed)
        f.addRow(_("Quality:"), hrow(self.quality_slider, self.quality_label))
        self.fps_slider = _slider(1, 30, 15)
        self.fps_label = QLabel()
        self.fps_label.setMinimumWidth(28)
        self.fps_slider.setToolTip(_("Upper limit. The real rate depends on Bluetooth speed and "
                                     "frame size (see Statistics)."))
        self.fps_slider.valueChanged.connect(self._on_fps_changed)
        f.addRow(_("Max FPS:"), hrow(self.fps_slider, self.fps_label))
        self.chroma_cb = QCheckBox(_("Full colour"))
        self.chroma_cb.setToolTip(_("JPEG 4:4:4 — sharper coloured text. Turning it off makes "
                                    "frames ~20–30 % smaller."))
        self.chroma_cb.toggled.connect(lambda *_a: (self._sync_preset(), self._on_option_changed()))
        f.addRow("", self.chroma_cb)
        self.fit_combo = _combo(FIT_LABELS)
        self.fit_combo.currentIndexChanged.connect(lambda *_a: self._on_option_changed())
        f.addRow(_("Fit:"), self.fit_combo)
        self.rot_combo = _combo(ROT_LABELS)
        self.rot_combo.currentIndexChanged.connect(lambda *_a: self._on_option_changed())
        f.addRow(_("Rotation:"), self.rot_combo)
        self.flip_cb = QCheckBox(_("Mirror horizontally"))
        self.flip_cb.toggled.connect(lambda *_a: self._on_option_changed())
        f.addRow("", self.flip_cb)
        self.contrast_slider = _slider(50, 200, 100)
        self.contrast_slider.valueChanged.connect(lambda *_a: self._on_option_changed())
        f.addRow(_("Contrast:"), self.contrast_slider)
        self.sat_slider = _slider(0, 200, 100)
        self.sat_slider.valueChanged.connect(lambda *_a: self._on_option_changed())
        f.addRow(_("Saturation:"), self.sat_slider)
        self.sharpen_cb = QCheckBox(_("Sharpen (small text)"))
        self.sharpen_cb.toggled.connect(lambda *_a: self._on_option_changed())
        f.addRow("", self.sharpen_cb)
        self.cursor_cb = QCheckBox(_("Show mouse pointer"))
        self.cursor_cb.toggled.connect(lambda *_a: self._on_option_changed())
        f.addRow("", self.cursor_cb)
        self.skip_cb = QCheckBox(_("Don't resend an unchanged screen"))
        self.skip_cb.toggled.connect(lambda *_a: self._on_option_changed())
        f.addRow("", self.skip_cb)
        return dock_body(f)

    # --- Device
    def _build_device(self) -> QWidget:
        v = QVBoxLayout()
        v.setSpacing(7)
        self.conn_combo = _combo(CONN_LABELS)
        self.conn_combo.currentIndexChanged.connect(self._on_conn_type)
        v.addLayout(hrow(QLabel(_("Connection:")), self.conn_combo))
        self.bt_box = QWidget()
        bl = QVBoxLayout(self.bt_box)
        bl.setContentsMargins(0, 0, 0, 0)
        self.dev_combo = _combo([])
        self.dev_combo.currentIndexChanged.connect(self._on_device_picked)
        self.scan_btn = QPushButton(_("Search"))
        self.scan_btn.setToolTip(_("Search for nearby Bluetooth devices (~7 s)"))
        self.scan_btn.clicked.connect(self._scan)
        bl.addLayout(hrow(self.dev_combo, self.scan_btn))
        self.mac_edit = QLineEdit()
        self.mac_edit.setPlaceholderText(_("Address, e.g. AA:BB:CC:DD:EE:FF"))
        bl.addWidget(self.mac_edit)
        v.addWidget(self.bt_box)
        self.com_box = QWidget()
        cl = QHBoxLayout(self.com_box)
        cl.setContentsMargins(0, 0, 0, 0)
        self.com_combo = QComboBox()
        self.com_combo.setEditable(True)
        self.com_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        com_refresh = QPushButton("↻")
        com_refresh.setFixedWidth(34)
        com_refresh.clicked.connect(self._refresh_com_ports)
        cl.addWidget(self.com_combo, 1)
        cl.addWidget(com_refresh)
        v.addWidget(self.com_box)
        self.connect_btn = QPushButton(_("Connect"))
        self.connect_btn.setProperty("primary", True)
        self.connect_btn.clicked.connect(self._connect)
        self.disconnect_btn = QPushButton(_("Disconnect"))
        self.disconnect_btn.clicked.connect(self._disconnect)
        v.addLayout(hrow(self.connect_btn, self.disconnect_btn))
        self.bright_slider = _slider(0, 100, 80)
        self.bright_slider.sliderReleased.connect(self._send_brightness)
        self.bright_label = QLabel("80%")
        self.bright_label.setMinimumWidth(36)
        self.bright_slider.valueChanged.connect(lambda val: self.bright_label.setText(f"{val}%"))
        v.addLayout(hrow(QLabel(_("Brightness:")), self.bright_slider, self.bright_label))
        self.usb_label = muted("")
        self.usb_label.setVisible(False)
        v.addWidget(self.usb_label)
        v.addStretch(1)
        return dock_body(v)

    # --- Statistics
    def _build_stats(self) -> QWidget:
        g = QGridLayout()
        g.setSpacing(6)
        self.t_fps = StatTile(_("Updates / s"))
        self.t_kbps = StatTile(_("KB / s"))
        self.t_lat = StatTile(_("Latency, ms"), tooltip=_("From capture to the device's "
                                                         "confirmation."))
        self.t_size = StatTile(_("Frame size"))
        self.t_quality = StatTile(_("JPEG quality"))
        self.t_interval = StatTile(_("Pause, ms"), tooltip=_("Pause between 256-byte chunks"))
        self.t_sent = StatTile(_("Sent"))
        self.t_skip = StatTile(_("Skipped / errors"))
        for i, t in enumerate([self.t_fps, self.t_kbps, self.t_lat, self.t_size, self.t_quality,
                               self.t_interval, self.t_sent, self.t_skip]):
            g.addWidget(t, i // 2, i % 2)
        self.mirror_status = muted("")
        v = QVBoxLayout()
        v.addLayout(g)
        v.addWidget(self.mirror_status)
        v.addStretch(1)
        return dock_body(v)

    # --- Log
    def _build_log(self) -> QWidget:
        v = QVBoxLayout()
        self.log_view = LogView()
        v.addWidget(self.log_view, 1)
        clear = QPushButton(_("Clear"))
        clear.clicked.connect(self.log_view.clear)
        v.addLayout(hrow(None, clear))
        return dock_body(v, scroll=False)

    # --- Controls
    def _build_controls(self) -> QWidget:
        v = QVBoxLayout()
        v.setSpacing(6)
        self.start_btn = QPushButton()
        self.start_btn.setProperty("big", True)
        self.start_btn.clicked.connect(self._toggle_start)
        self.send_btn = QPushButton(_("Send to device"))
        self.send_btn.setProperty("big", True)
        self.send_btn.clicked.connect(self._send_still)
        self.ctl_connect = QPushButton()
        self.ctl_connect.setProperty("big", True)
        self.ctl_connect.clicked.connect(self._toggle_connect)
        settings_btn = QPushButton(_("Settings"))
        settings_btn.setProperty("big", True)
        settings_btn.clicked.connect(self._open_settings)
        exit_btn = QPushButton(_("Exit"))
        exit_btn.setProperty("big", True)
        exit_btn.clicked.connect(self.close)
        for b in (self.start_btn, self.send_btn, self.ctl_connect, settings_btn, exit_btn):
            v.addWidget(b)
        v.addStretch(1)
        return dock_body(v)

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.sb_usb = QLabel("")
        self.sb_fps = QLabel("")
        self.sb_kbps = QLabel("")
        self.sb_lat = QLabel("")
        self.sb_conn = StatusDot()
        for wdg in (self.sb_usb, self.sb_fps, self.sb_kbps, self.sb_lat, self.sb_conn):
            sb.addPermanentWidget(wdg)
        self.sb_conn.set_state("off", _(STATE_LABELS[DeviceState.DISCONNECTED]))
        self._set_stat_labels(None)

    def _attach_logging(self) -> None:
        self._log_handler = QtLogHandler(self.invoker, self.log_view)
        root = logging.getLogger("minitoo")
        root.addHandler(self._log_handler)
        root.setLevel(logging.INFO)

    def _set_debug(self, on: bool) -> None:
        self._debug_log = on
        logging.getLogger("minitoo").setLevel(logging.DEBUG if on else logging.INFO)

    # ============================================================ settings

    def _load_settings(self) -> None:
        s = self.s
        _select_data(self.conn_combo, s.connection)
        self.mac_edit.setText(s.address)
        self.com_combo.setEditText(s.com_port)
        self.bright_slider.setValue(s.brightness)
        self.quality_slider.setValue(s.jpeg_quality)
        self.chroma_cb.setChecked(s.chroma_444)
        _select_data(self.fit_combo, s.fit_mode)
        _select_data(self.rot_combo, s.rotation)
        self.flip_cb.setChecked(s.flip_h)
        self.contrast_slider.setValue(round(s.contrast * 100))
        self.sat_slider.setValue(round(s.saturation * 100))
        self.sharpen_cb.setChecked(s.sharpen)
        self.cursor_cb.setChecked(s.show_cursor)
        self.client_only_cb.setChecked(s.client_only)
        _select_data(self.method_combo, s.window_method)
        self.fps_slider.setValue(max(1, min(30, round(s.target_fps))))
        self.skip_cb.setChecked(s.skip_unchanged)
        self.loop_cb.setChecked(s.video_loop)
        self._fill_sound_devices()
        self.clip_fps.setValue(s.clip_fps)
        self.clip_seconds.setValue(int(s.clip_seconds))
        self._on_fps_changed(self.fps_slider.value())
        self._on_quality_changed(self.quality_slider.value())
        self._update_region_label()
        self._on_conn_type()
        fmt = FORMATS.get(s.fmt_key, FORMATS["jpeg160"])
        self.preview.set_format_size(fmt.width, fmt.height)
        try:
            if s.geometry:
                self.restoreGeometry(QByteArray.fromHex(s.geometry.encode()))
            if s.window_state:
                self.restoreState(QByteArray.fromHex(s.window_state.encode()))
        except Exception:
            pass

    def _store_settings(self) -> None:
        s = self.s
        s.language = current_language()
        s.connection = self.conn_combo.currentData()
        s.address = normalize_mac(self.mac_edit.text()) or self.mac_edit.text().strip()
        s.com_port = self._com_port_text()
        s.brightness = self.bright_slider.value()
        s.jpeg_quality = self.quality_slider.value()
        s.still_quality = self.quality_slider.value()
        s.chroma_444 = self.chroma_cb.isChecked()
        opts = self._image_options()
        s.fit_mode, s.rotation, s.flip_h = opts.fit_mode, opts.rotation, opts.flip_h
        s.contrast, s.saturation, s.sharpen = opts.contrast, opts.saturation, opts.sharpen
        s.show_cursor = opts.show_cursor
        src = self._capture_source()
        s.source_kind = self._source_kind
        s.monitor = self._monitor_index
        s.region = self._region.to_list() if self._region else []
        if src.kind == "window" and src.window_title:
            s.window_title, s.window_process = src.window_title, src.window_process
        s.client_only, s.window_method = src.client_only, src.method
        s.target_fps = float(self.fps_slider.value())
        s.skip_unchanged = self.skip_cb.isChecked()
        s.video_loop = self.loop_cb.isChecked()
        s.clip_fps = self.clip_fps.value()
        s.clip_seconds = float(self.clip_seconds.value())
        s.geometry = bytes(self.saveGeometry().toHex()).decode()
        s.window_state = bytes(self.saveState().toHex()).decode()

    def _save_settings(self) -> None:
        if self.persist:
            self.s.save()

    def closeEvent(self, ev) -> None:
        if self.engine is not None:
            self.engine.stop(wait=True)
        if self.device is not None:
            self.device.disconnect()
        if self.video is not None:
            self.video.close()
        self._store_settings()
        self._save_settings()
        logging.getLogger("minitoo").removeHandler(self._log_handler)
        super().closeEvent(ev)

    def _open_settings(self) -> None:
        from .settings_dialog import SettingsDialog

        self._store_settings()
        dlg = SettingsDialog(self.s, self._debug_log, self)
        if not dlg.exec():
            return
        old_lang = self.s.language
        new = dlg.result_settings
        for name in ("language", "auto_connect", "auto_reconnect", "channel", "chunk_interval_ms",
                     "auto_pacing", "min_interval_ms", "request_timeout", "ack_timeout", "fmt_key",
                     "max_frame_kb", "high_quality", "keepalive_s", "batch_frames",
                     "batch_interval_ms"):
            setattr(self.s, name, getattr(new, name))
        self._set_debug(dlg.debug_log)
        if self.device is not None:
            self.device.settings = self._upload_settings()
            self.device.auto_reconnect = self.s.auto_reconnect
            self.device.reset_pacing()
        self._on_option_changed()
        self._save_settings()
        if self.s.language != old_lang:
            from ..i18n import load_table
            table = load_table(self.s.language)     # ask in the *new* language
            title = table.get(RESTART_TITLE, RESTART_TITLE)
            text = table.get(RESTART_TEXT, RESTART_TEXT)
            if QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes:
                self._restart()

    def _restart(self) -> None:
        args = [a for a in sys.argv[1:] if not a.startswith("--screenshot")]
        if getattr(sys, "frozen", False):
            QProcess.startDetached(sys.executable, args)
        else:
            QProcess.startDetached(sys.executable, [os.path.abspath(sys.argv[0])] + args)
        self.close()
        QApplication.quit()

    # ============================================================ device

    def _on_conn_type(self, *_a) -> None:
        kind = self.conn_combo.currentData()
        self.bt_box.setVisible(kind == "rfcomm")
        self.com_box.setVisible(kind == "serial")

    def _com_port_text(self) -> str:
        data = self.com_combo.currentData()
        text = self.com_combo.currentText().strip()
        if data and self.com_combo.itemText(self.com_combo.currentIndex()) == text:
            return data
        return text.split(" ")[0] if text else ""

    def _refresh_com_ports(self) -> None:
        current = self._com_port_text() or self.s.com_port
        self.com_combo.clear()
        try:
            ports = list_bluetooth_com_ports()
        except Exception as e:
            log.warning("COM ports: %s", e)
            ports = []
        for p in ports:
            mac = f" · {p.mac}" if p.mac else ""
            self.com_combo.addItem(f"{p.device} — {p.description}{mac}", p.device)
        if current:
            i = self.com_combo.findData(current)
            if i >= 0:
                self.com_combo.setCurrentIndex(i)
            else:
                self.com_combo.setEditText(current)

    def _fill_devices(self, devices: List[BtDevice]) -> None:
        self._devices = devices
        current = normalize_mac(self.mac_edit.text())
        self.dev_combo.blockSignals(True)
        self.dev_combo.clear()
        self.dev_combo.addItem(_("— choose a device —"), "")
        for d in devices:
            self.dev_combo.addItem(d.label(), d.address)
        if current:
            i = self.dev_combo.findData(current)
            if i >= 0:
                self.dev_combo.setCurrentIndex(i)
        self.dev_combo.blockSignals(False)

    def _refresh_known_devices(self) -> None:
        def done(devs):
            self._fill_devices(devs)
            if not self.mac_edit.text().strip():
                best = next((d for d in devs if d.is_divoom), None)
                if best:
                    self.mac_edit.setText(best.address)
                    _select_data(self.dev_combo, best.address)
        run_in_background(self.invoker, lambda: find_devices(inquiry=False), done,
                          lambda e: log.warning("Bluetooth devices: %s", e))

    def _scan(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText(_("Searching…"))
        log.info(_("Searching for Bluetooth devices (~7 s). The MiniToo must be on and "
                   "disconnected from the phone."))

        def finish():
            self._scanning = False
            self.scan_btn.setEnabled(True)
            self.scan_btn.setText(_("Search"))

        def done(devs: List[BtDevice]):
            finish()
            self._fill_devices(devs)
            divoom = [d for d in devs if d.is_divoom]
            log.info(_("Found {n} devices.", n=len(devs)))
            if divoom and not normalize_mac(self.mac_edit.text()):
                self.mac_edit.setText(divoom[0].address)
                _select_data(self.dev_combo, divoom[0].address)
            for d in divoom:
                if not d.paired:
                    log.warning(_("“{name}” is not paired with Windows — pair it first "
                                  "(Tools → Windows Bluetooth settings).", name=d.name))

        def fail(e):
            finish()
            log.error("%s: %s", _("Search failed"), e)

        run_in_background(self.invoker, lambda: find_devices(inquiry=True, timeout_multiplier=5),
                          done, fail, name="bt-scan")

    def _on_device_picked(self, *_a) -> None:
        addr = self.dev_combo.currentData()
        if addr:
            self.mac_edit.setText(addr)

    def _upload_settings(self) -> UploadSettings:
        s = self.s
        return UploadSettings(chunk_interval_ms=s.chunk_interval_ms, auto_pacing=s.auto_pacing,
                              min_interval_ms=s.min_interval_ms,
                              request_timeout=s.request_timeout, ack_timeout=s.ack_timeout)

    def _toggle_connect(self) -> None:
        if self.device is not None and self.device.state != DeviceState.DISCONNECTED:
            self._disconnect()
        else:
            self._connect()

    def _connect(self) -> None:
        kind = self.conn_combo.currentData()
        if kind == "rfcomm":
            mac = normalize_mac(self.mac_edit.text())
            if not mac:
                QMessageBox.warning(self, APP_NAME, _("Choose the device or type its address."))
                return
            self.mac_edit.setText(mac)
            factory = make_transport_factory("rfcomm", address=mac, channel=self.s.channel)
            known = next((d for d in self._devices if d.address == mac), None)
            if known is not None and not known.paired:
                log.warning(_("“{name}” is not paired with Windows — pair it first "
                              "(Tools → Windows Bluetooth settings).", name=known.name))
        elif kind == "serial":
            port = self._com_port_text()
            if not port:
                QMessageBox.warning(self, APP_NAME, _("Choose a COM port."))
                return
            factory = make_transport_factory("serial", port=port)
        else:
            factory = make_transport_factory("simulator")
        if self.device is not None:
            self.device.disconnect()
        self.device = MiniTooDevice(factory, self._upload_settings(),
                                    auto_reconnect=self.s.auto_reconnect,
                                    on_state=lambda st, msg: self.invoker(
                                        lambda: self._on_device_state(st, msg)))
        self._store_settings()
        self._save_settings()
        dev = self.device
        run_in_background(self.invoker, dev.connect, lambda _r: self._after_connect(dev),
                          lambda e: self._connect_failed(dev, e), name="bt-connect")
        self._update_controls()

    def _after_connect(self, dev: MiniTooDevice) -> None:
        if dev is self.device:
            self.statusBar().showMessage(_("Connected") + f": {dev.description}", 8000)

    def _connect_failed(self, dev: MiniTooDevice, e: BaseException) -> None:
        if dev is not self.device:
            return
        log.error("%s: %s", _("Connection failed"), e)
        self.statusBar().showMessage(_("Connection failed"), 8000)
        self._update_controls()

    def _disconnect(self) -> None:
        if self.engine is not None:
            self.engine.stop(wait=False)
        dev = self.device
        if dev is not None:
            run_in_background(self.invoker, dev.disconnect, name="bt-disconnect")

    def _on_device_state(self, state: DeviceState, message: str) -> None:
        kind = {DeviceState.CONNECTED: "ok", DeviceState.CONNECTING: "busy",
                DeviceState.RECONNECTING: "busy"}.get(state, "off")
        if state == DeviceState.DISCONNECTED and message:
            kind = "error"
        self.sb_conn.set_state(kind, _(STATE_LABELS[state]))
        self.sb_conn.setToolTip(message)
        self._update_controls()

    def _send_brightness(self) -> None:
        dev = self.device
        if dev is None or not dev.is_connected:
            return
        level = self.bright_slider.value()
        run_in_background(self.invoker, lambda: dev.set_brightness(level), None,
                          lambda e: log.error("%s", e))

    def _check_usb(self) -> None:
        def done(names):
            was = self.usb_label.isVisible()
            self.usb_label.setVisible(bool(names))
            self.sb_usb.setText("🔌 USB" if names else "")
            if names:
                text = _("Connected by USB cable: power and sound only. Pictures go over "
                         "Bluetooth — the MiniToo has no screen data channel on USB.")
                self.usb_label.setText("🔌 " + text)
                self.sb_usb.setToolTip(text)
                if not was:
                    log.info(_("USB cable detected ({names}). If the PC's sound plays on the "
                               "MiniToo, it may show its sound visualizer instead of the picture.",
                               names=", ".join(names)))

        run_in_background(self.invoker, usb_divoom_devices, done, lambda e: None, name="usb-check")

    # ============================================================ sources

    def _refresh_sources(self) -> None:
        self.source_list.blockSignals(True)
        self.source_list.clear()
        try:
            monitors = list_monitors()
        except Exception as e:
            log.warning("displays: %s", e)
            monitors = []
        self._monitors = monitors
        for m in monitors:
            self._add_source(f"{SOURCE_ICONS['monitor']}  {_('Display {n}', n=m.index)}",
                             f"monitor:{m.index}")
            self.source_list.item(self.source_list.count() - 1).setToolTip(m.label())
        self._add_source(f"{SOURCE_ICONS['window']}  {_('Window')}", "window:0")
        self._add_source(f"{SOURCE_ICONS['region']}  {_('Screen region')}", "region:0")
        self._add_source(f"{SOURCE_ICONS['image']}  {_('Image / GIF')}", "image:0")
        self._add_source(f"{SOURCE_ICONS['video']}  {_('Video file')}", "video:0")
        self.source_list.setMinimumWidth(self.source_list.sizeHintForColumn(0) + 34)
        target = f"{self._source_kind}:{self._monitor_index if self._source_kind == 'monitor' else 0}"
        for i in range(self.source_list.count()):
            if self.source_list.item(i).data(Qt.ItemDataRole.UserRole) == target:
                self.source_list.setCurrentRow(i)
                break
        else:
            self.source_list.setCurrentRow(0)
        self.source_list.blockSignals(False)
        self._on_source_picked(self.source_list.currentItem())

    def _add_source(self, text: str, data) -> None:
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, data)
        self.source_list.addItem(item)

    def _on_source_picked(self, item, _prev=None) -> None:
        if item is None:
            return
        kind, idx_s = str(item.data(Qt.ItemDataRole.UserRole)).split(":")
        idx = int(idx_s)
        if self.engine is not None and self.engine.running and kind != self._source_kind:
            if kind in ("image",) or self._source_kind == "video" or kind == "video":
                self.engine.stop(wait=False)
        self._source_kind = kind
        if kind == "monitor":
            self._monitor_index = idx
            m = next((m for m in self._monitors if m.index == idx), None)
            self.monitor_info.setText(_("Mirrors the whole display ({w}×{h}).",
                                        w=m.rect.width, h=m.rect.height) if m else "")
            self.monitor_info.setText(self.monitor_info.text() + "\n\n" + _(
                "Tip: a small region (e.g. one panel or a clock) looks much sharper on the "
                "160×128 screen than the whole display."))
            self.props.setCurrentIndex(self.page_monitor)
        elif kind == "window":
            self.props.setCurrentIndex(self.page_window)
        elif kind == "region":
            self.props.setCurrentIndex(self.page_region)
        elif kind == "image":
            self.props.setCurrentIndex(self.page_image)
            self._still_preview()
        elif kind == "video":
            self.props.setCurrentIndex(self.page_video)
            self._video_preview()
        self.dock_props.setWindowTitle(_("Source settings") + " · " + item.text().split("  ", 1)[-1])
        self._update_controls()
        self._on_option_changed()

    def _refresh_windows(self) -> None:
        prev = self._capture_source()
        try:
            wins = list_windows()
        except Exception as e:
            log.warning("windows: %s", e)
            wins = []
        self._windows = wins
        self.window_combo.blockSignals(True)
        self.window_combo.clear()
        for w in wins:
            self.window_combo.addItem(w.label(), w.hwnd)
        target = None
        if prev.hwnd:
            target = next((i for i, w in enumerate(wins) if w.hwnd == prev.hwnd), None)
        if target is None and self.s.window_title:
            found = find_window(self.s.window_title, self.s.window_process)
            if found is not None:
                target = next((i for i, w in enumerate(wins) if w.hwnd == found.hwnd), None)
        if target is not None:
            self.window_combo.setCurrentIndex(target)
        self.window_combo.blockSignals(False)

    def _pick_region(self) -> None:
        fmt = FORMATS.get(self.s.fmt_key, FORMATS["jpeg160"])
        ratio = fmt.width / fmt.height
        if (self.rot_combo.currentData() or 0) in (90, 270):
            ratio = 1 / ratio
        visible = self.isVisible()
        self.hide()
        QTimer.singleShot(250, lambda: self._show_region_dialog(ratio, visible))

    def _show_region_dialog(self, ratio: float, restore: bool) -> None:
        from .region_dialog import RegionDialog

        try:
            dlg = RegionDialog(self._region, ratio, None)
        except Exception as e:
            if restore:
                self.show()
            log.error("%s", e)
            return
        if restore:
            self.show()
        if dlg.exec() and dlg.result_rect is not None:
            self._region = dlg.result_rect
            self._update_region_label()
            self._on_option_changed()

    def _update_region_label(self) -> None:
        self.region_label.setText(_("Selected: {r}", r=self._region) if self._region
                                  else _("Nothing selected"))

    # ============================================================ options

    def _image_options(self) -> ImageOptions:
        return ImageOptions(fit_mode=self.fit_combo.currentData() or "fit",
                            rotation=self.rot_combo.currentData() or 0,
                            flip_h=self.flip_cb.isChecked(),
                            contrast=self.contrast_slider.value() / 100,
                            saturation=self.sat_slider.value() / 100,
                            sharpen=self.sharpen_cb.isChecked(),
                            show_cursor=self.cursor_cb.isChecked(),
                            high_quality=self.s.high_quality)

    def _capture_source(self) -> CaptureSource:
        kind = self._source_kind if self._source_kind in ("monitor", "window", "region") else "monitor"
        win = None
        idx = self.window_combo.currentIndex()
        if 0 <= idx < len(self._windows):
            win = self._windows[idx]
        return CaptureSource(kind=kind, monitor=self._monitor_index or 1, region=self._region,
                             hwnd=win.hwnd if win else 0, window_title=win.title if win else "",
                             window_process=win.process if win else "",
                             client_only=self.client_only_cb.isChecked(),
                             method=self.method_combo.currentData() or "screen")

    def _mirror_config(self) -> MirrorConfig:
        s = self.s
        return MirrorConfig(source=self._capture_source(), image=self._image_options(),
                            fmt_key=s.fmt_key, jpeg_quality=self.quality_slider.value(),
                            chroma_444=self.chroma_cb.isChecked(), max_frame_kb=s.max_frame_kb,
                            target_fps=float(self.fps_slider.value()),
                            skip_unchanged=self.skip_cb.isChecked(), keepalive_s=s.keepalive_s,
                            batch_frames=s.batch_frames, batch_interval_ms=s.batch_interval_ms)

    def _on_option_changed(self, *_a) -> None:
        if self._loading:
            return
        if self.engine is not None and self.engine.running:
            self.engine.update_config(self._mirror_config())
            return
        fmt = FORMATS.get(self.s.fmt_key, FORMATS["jpeg160"])
        self.preview.set_format_size(fmt.width, fmt.height)
        if self._source_kind == "image":
            self._still_preview()
        elif self._source_kind == "video":
            self._video_preview()

    def _apply_preset(self, *_a) -> None:
        data = self.preset_combo.currentData()
        if not data:
            return
        quality, chroma = data
        self._loading = True
        self.quality_slider.setValue(quality)
        self.chroma_cb.setChecked(chroma)
        self._loading = False
        self._on_quality_changed(quality)

    def _sync_preset(self) -> None:
        cur = (self.quality_slider.value(), self.chroma_cb.isChecked())
        idx = next((i for i, (_l, d) in enumerate(PRESETS) if d == cur), 0)
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(idx)
        self.preset_combo.blockSignals(False)

    def _on_fps_changed(self, v: int) -> None:
        self.fps_label.setText(str(v))
        self._on_option_changed()

    def _on_quality_changed(self, v: int) -> None:
        self.quality_label.setText(str(v))
        if hasattr(self, "chroma_cb"):
            self._sync_preset()
        self._on_option_changed()

    # ============================================================ controls

    @property
    def _mirroring(self) -> bool:
        return self.engine is not None and self.engine.running

    def _update_controls(self) -> None:
        dev = self.device
        state = dev.state if dev is not None else DeviceState.DISCONNECTED
        connected = state == DeviceState.CONNECTED
        mirroring = self._mirroring
        kind = self._source_kind
        self.connect_btn.setEnabled(state == DeviceState.DISCONNECTED)
        self.disconnect_btn.setEnabled(state != DeviceState.DISCONNECTED)
        self.conn_combo.setEnabled(state == DeviceState.DISCONNECTED)
        self.ctl_connect.setText(_("Connect") if state == DeviceState.DISCONNECTED else _("Disconnect"))
        if mirroring:
            self.start_btn.setText(_("Stop video") if kind == "video" else _("Stop mirroring"))
            self.start_btn.setProperty("active", True)
            self.start_btn.setProperty("primary", False)
            self.start_btn.setEnabled(True)
        else:
            self.start_btn.setText(_("Play on device") if kind == "video" else _("Start mirroring"))
            self.start_btn.setProperty("active", False)
            self.start_btn.setProperty("primary", True)
            ok_source = kind in ("monitor", "window", "region") or (kind == "video" and self.video)
            self.start_btn.setEnabled(connected and not self._uploading and bool(ok_source))
        self.start_btn.style().unpolish(self.start_btn)
        self.start_btn.style().polish(self.start_btn)
        self.send_btn.setEnabled(connected and not mirroring and not self._uploading
                                 and kind == "image" and bool(self._still_frames))
        self.clip_btn.setEnabled(connected and not mirroring and not self._uploading
                                 and self.video is not None)
        self.preview.set_live(mirroring)

    def _toggle_start(self) -> None:
        if self._mirroring:
            self._stop_mirror()
        else:
            self._start_mirror()

    def _start_mirror(self) -> None:
        dev = self.device
        if dev is None or not dev.is_connected:
            return
        cfg = self._mirror_config()
        grabber_factory = None
        kind = self._source_kind
        if kind == "window" and not cfg.source.hwnd:
            QMessageBox.information(self, APP_NAME, _("Choose a window from the list."))
            return
        if kind == "region" and not self._region:
            self._pick_region()
            return
        if kind == "video":
            if self.video is None:
                self._open_video()
                return
            from ..video import VideoGrabber
            player = self.video
            grabber_factory = lambda: VideoGrabber(player)  # noqa: E731
            player.play()
        self._store_settings()
        self._save_settings()
        self.engine = MirrorEngine(
            dev, cfg,
            on_preview=lambda img, ef, res: self.invoker(lambda: self._on_preview(img, ef, res)),
            on_stats=lambda st: self.invoker(lambda: self._on_stats(st)),
            on_stopped=lambda reason: self.invoker(lambda: self._on_mirror_stopped(reason)),
            grabber_factory=grabber_factory)
        self.engine.start()
        log.info("%s: %s", _("Started"), self.source_list.currentItem().text().split("  ", 1)[-1])
        self._update_controls()

    def _stop_mirror(self) -> None:
        if self.engine is not None:
            self.engine.stop(wait=False)
        if self.video is not None and self._source_kind == "video":
            self.video.pause()
        self.start_btn.setEnabled(False)

    def _on_mirror_stopped(self, reason: str) -> None:
        if reason:
            log.warning(reason)
            self.mirror_status.setText(reason)
        else:
            log.info(_("Stopped"))
        if self.video is not None:
            self.video.pause()
        self._update_controls()

    def _on_preview(self, img, ef, res) -> None:
        kb = len(ef.payload) / 1024
        q = f"q{ef.quality}" if ef.fmt.encoding == "jpeg" else "zstd"
        frames = f" · {ef.frame_count} " + _("frames") if ef.frame_count > 1 else ""
        self.preview.set_image(img, f"{ef.fmt.width}×{ef.fmt.height} · {q} · {kb:.1f} KB{frames}")

    def _set_stat_labels(self, st: Optional[MirrorStats]) -> None:
        if st is None or not st.uploads:
            self.sb_fps.setText(_("FPS: {v}", v="—"))
            self.sb_kbps.setText("— KB/s")
            self.sb_lat.setText(_("Latency: {v}", v="—"))
            return
        self.sb_fps.setText(_("FPS: {v}", v=f"{st.fps:.1f}"))
        self.sb_kbps.setText(f"{st.kbps:.1f} KB/s")
        self.sb_lat.setText(_("Latency: {v}", v=f"{st.latency_ms:.0f} ms"))

    def _on_stats(self, st: MirrorStats) -> None:
        self.t_fps.set(f"{st.fps:.1f}" + (f" ({st.shown_fps:.1f})" if st.shown_fps > st.fps + 0.05 else ""))
        self.t_kbps.set(f"{st.kbps:.1f}")
        self.t_lat.set(f"{st.latency_ms:.0f}" if st.latency_ms else "—")
        self.t_size.set(f"{st.frame_bytes / 1024:.1f} KB" if st.frame_bytes else "—")
        self.t_quality.set(str(st.quality) if st.quality else "—")
        self.t_interval.set(f"{st.interval_ms:.1f}" if st.uploads else "—")
        self.t_sent.set(str(st.uploads))
        self.t_skip.set(f"{st.skipped} / {st.errors}")
        self.mirror_status.setText(st.status)
        self.statusBar().showMessage(st.status)
        self._set_stat_labels(st)
        if not st.running:
            self._update_controls()

    # ============================================================ image

    def _open_image(self) -> None:
        start = self.s.last_image_dir or os.path.expanduser("~")
        path, _f = QFileDialog.getOpenFileName(
            self, _("Open image…"), start,
            _("Images") + " (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff *.ico);;"
            + _("All files") + " (*)")
        if not path:
            return
        self.s.last_image_dir = os.path.dirname(path)
        try:
            img = Image.open(path)
            frames, durations = [], []
            for fr in ImageSequence.Iterator(img):
                frames.append(fr.convert("RGB"))
                durations.append(int(fr.info.get("duration", 100) or 100))
                if len(frames) >= 120:
                    break
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, _("The image could not be opened:") + f"\n{e}")
            return
        self._still_frames = frames
        self._still_speed = max(20, min(2000, round(sum(durations) / len(durations))))
        self._still_name = os.path.basename(path)
        w, h = frames[0].size
        anim = (" · " + _("animation: {n} frames", n=len(frames))) if len(frames) > 1 else ""
        self.still_label.setText(f"{self._still_name} — {w}×{h}{anim}")
        self._select_source("image")
        self._still_preview()
        self._update_controls()

    def _use_test_pattern(self) -> None:
        fmt = FORMATS.get(self.s.fmt_key, FORMATS["jpeg160"])
        self._still_frames = [make_test_pattern(fmt.width, fmt.height, "MiniToo")]
        self._still_name = _("Test pattern")
        self.still_label.setText(f"{self._still_name} — {fmt.width}×{fmt.height}")
        self._still_preview()
        self._update_controls()

    def _select_source(self, kind: str) -> None:
        for i in range(self.source_list.count()):
            if str(self.source_list.item(i).data(Qt.ItemDataRole.UserRole)).startswith(kind + ":"):
                self.source_list.setCurrentRow(i)
                return

    def _still_preview(self) -> None:
        if not self._still_frames or self._mirroring:
            return
        from ..encoder import FrameEncoder

        try:
            enc = FrameEncoder(self.s.fmt_key, self.quality_slider.value(),
                               chroma_444=self.chroma_cb.isChecked())
            fmt = enc.fmt
            img = process_frame(self._still_frames[0], (fmt.width, fmt.height), self._image_options())
            ef = enc.encode([img])
            self.preview.set_image(ef.preview, _("Preview (not sent yet)") +
                                   f" · {len(ef.payload) / 1024:.1f} KB")
        except Exception as e:
            log.warning("preview: %s", e)

    def _send_still(self) -> None:
        dev = self.device
        if dev is None or not dev.is_connected or not self._still_frames or self._uploading:
            return
        self._uploading = True
        self._update_controls()
        self.still_progress.setValue(0)
        frames = list(self._still_frames)
        opts = self._image_options()
        fmt_key = self.s.fmt_key
        quality = self.quality_slider.value()
        chroma = self.chroma_cb.isChecked()
        speed = self._still_speed

        def progress(done, total):
            self.invoker(lambda: self.still_progress.setValue(round(done * 100 / total)))

        def work():
            if len(frames) > 1:
                return send_animation(dev, frames, opts, speed, fmt_key, min(quality, 85),
                                      chroma_444=chroma, progress=progress)
            return send_still(dev, frames[0], opts, fmt_key, quality, progress, chroma)

        def done(result):
            ef, res = result
            self._uploading = False
            self.still_progress.setValue(100 if res.ok else 0)
            self._on_preview(ef.preview, ef, res)
            self.still_result.setText(res.summary())
            (log.info if res.ok else log.warning)("%s: %s", self._still_name, res.summary())
            self._update_controls()

        def fail(e):
            self._uploading = False
            self.still_progress.setValue(0)
            self.still_result.setText(str(e))
            log.error("%s", e)
            self._update_controls()

        run_in_background(self.invoker, work, done, fail, name="send-still")

    # ============================================================ video

    def _open_video(self) -> None:
        from ..video import VIDEO_EXTENSIONS, VideoError, VideoPlayer, available, format_time

        if not available():
            QMessageBox.warning(self, APP_NAME, _("Video support is not installed (PyAV)."))
            return
        start = self.s.last_video_dir or os.path.expanduser("~")
        path, _f = QFileDialog.getOpenFileName(
            self, _("Open video…"), start,
            _("Videos") + f" ({' '.join(VIDEO_EXTENSIONS)});;" + _("All files") + " (*)")
        if not path:
            return
        self.s.last_video_dir = os.path.dirname(path)
        if self._mirroring and self._source_kind == "video":
            self.engine.stop(wait=True)
        try:
            player = VideoPlayer(path, self._sound_device())
        except VideoError as e:
            QMessageBox.warning(self, APP_NAME, _("The video could not be opened:") + f"\n{e}")
            return
        if self.video is not None:
            self.video.close()
        self.video = player
        player.loop = self.loop_cb.isChecked()
        info = player.info
        self.video_slider.setRange(0, max(1, int(info.duration * 1000)))
        self.video_label.setText(f"{os.path.basename(path)} — {info.width}×{info.height} · "
                                 f"{info.fps:.0f} fps · {format_time(info.duration)}"
                                 + ("" if player.audio is None or player.has_audio
                                    else " · " + _("no sound in this file")))
        self._select_source("video")
        self._video_preview()
        self._update_controls()

    def _video_preview(self) -> None:
        if self.video is None or self._mirroring:
            return
        try:
            frame = self.video.current_frame()
            if frame is None:
                return
            fmt = FORMATS.get(self.s.fmt_key, FORMATS["jpeg160"])
            img = process_frame(frame, (fmt.width, fmt.height), self._image_options())
            self.preview.set_image(img, _("Preview (not sent yet)"))
        except Exception as e:
            log.warning("video preview: %s", e)

    def _toggle_play(self) -> None:
        if self.video is None:
            self._open_video()
            return
        if self._source_kind == "video" and not self._mirroring and self.device is not None \
                and self.device.is_connected and not self.video.playing:
            self._start_mirror()
            return
        self.video.toggle()
        self._tick_video()

    def _fill_sound_devices(self) -> None:
        from ..audio import output_devices

        want = self.s.video_sound
        self.sound_combo.blockSignals(True)
        self.sound_combo.clear()
        self.sound_combo.addItem(_("Off"), "off")
        self.sound_combo.addItem(_("Windows default output"), "default")
        try:
            for dev_id, name in output_devices():
                self.sound_combo.addItem(name, f"dev:{dev_id}:{name}")
        except Exception as e:
            log.warning("sound devices: %s", e)
        idx = 1
        for i in range(self.sound_combo.count()):
            data = self.sound_combo.itemData(i)
            if data == want or (want.startswith("dev:") and data.split(":", 2)[-1] == want.split(":", 2)[-1]):
                idx = i
                break
        self.sound_combo.setCurrentIndex(idx)
        self.sound_combo.blockSignals(False)

    def _sound_device(self) -> Optional[int]:
        data = self.sound_combo.currentData() or "default"
        if data == "off":
            return None
        if data.startswith("dev:"):
            return int(data.split(":", 2)[1])
        return -1

    def _on_sound_changed(self, *_a) -> None:
        self.s.video_sound = self.sound_combo.currentData() or "default"
        if self.video is not None:
            self.video.set_audio_device(self._sound_device())

    def _on_loop(self, on: bool) -> None:
        if self.video is not None:
            self.video.loop = on

    def _slider_moved(self, value: int) -> None:
        if self.video is not None:
            self.video.seek(value / 1000)
            self._video_preview()

    def _slider_released(self) -> None:
        self._slider_dragging = False
        if self.video is not None:
            self.video.seek(self.video_slider.value() / 1000)
            self._video_preview()

    def _tick_video(self) -> None:
        v = self.video
        if v is None:
            return
        from ..video import format_time

        pos = v.position
        if not self._slider_dragging:
            self.video_slider.blockSignals(True)
            self.video_slider.setValue(int(pos * 1000))
            self.video_slider.blockSignals(False)
        self.video_time.setText(f"{format_time(pos)} / {format_time(v.info.duration)}")
        self.play_btn.setText("⏸" if v.playing else "▶")
        if v.playing and not self._mirroring and self._source_kind == "video":
            self._video_preview()

    def _upload_clip(self) -> None:
        dev = self.device
        if dev is None or not dev.is_connected or self.video is None or self._uploading:
            return
        from ..video import extract_clip

        path = self.video.info.path
        start = self.video.position
        seconds = float(self.clip_seconds.value())
        fps = float(self.clip_fps.value())
        if seconds * fps > 255:
            seconds = 255 / fps
        opts = self._image_options()
        quality = min(90, self.quality_slider.value())
        chroma = self.chroma_cb.isChecked()
        self._uploading = True
        self._update_controls()
        self.clip_progress.setValue(0)

        def p_extract(done, total):
            self.invoker(lambda: self.clip_progress.setValue(round(done * 50 / total)))

        def p_upload(done, total):
            self.invoker(lambda: self.clip_progress.setValue(50 + round(done * 50 / total)))

        def work():
            frames = extract_clip(path, start, seconds, fps, p_extract)
            if not frames:
                raise RuntimeError(_("No frames could be read from the video."))
            return send_animation(dev, frames, opts, round(1000 / fps), self.s.fmt_key, quality,
                                  max_bytes=min(CLIP_BUDGET, MAX_SAFE_PAYLOAD - 10_000),
                                  chroma_444=chroma, progress=p_upload)

        def done(result):
            ef, res = result
            self._uploading = False
            self.clip_progress.setValue(100 if res.ok else 0)
            self._on_preview(ef.preview, ef, res)
            text = _("{n} frames, quality {q}", n=ef.frame_count, q=ef.quality) + " · " + res.summary()
            self.clip_result.setText(text)
            (log.info if res.ok else log.warning)("%s: %s", _("Clip"), text)
            self._update_controls()

        def fail(e):
            self._uploading = False
            self.clip_progress.setValue(0)
            self.clip_result.setText(str(e))
            log.error("%s", e)
            self._update_controls()

        run_in_background(self.invoker, work, done, fail, name="upload-clip")

    # ============================================================ dialogs

    def _open_diagnostics(self) -> None:
        from .diagnostics_dialog import DiagnosticsDialog

        DiagnosticsDialog(self, lambda: self.device, self.invoker,
                          lambda: self._mirroring or self._uploading).exec()

    def _about(self) -> None:
        QMessageBox.about(
            self, _("About"),
            f"<h3>{APP_NAME} {__version__}</h3>"
            f"<p>{_('Screen mirroring for the Divoom MiniToo')}</p>"
            f"<p>{_('Unofficial project — not affiliated with or endorsed by Divoom.')}</p>"
            f"<p>MIT License · <a href='{REPO_URL}'>{REPO_URL.replace('https://', '')}</a></p>")
