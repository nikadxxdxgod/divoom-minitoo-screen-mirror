"""Settings storage: %APPDATA%\\MiniTooMirror\\settings.json"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional

log = logging.getLogger("minitoo.settings")

SETTINGS_VERSION = 2


def app_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "MiniTooMirror")
    os.makedirs(path, exist_ok=True)
    return path


@dataclass
class AppSettings:
    version: int = SETTINGS_VERSION
    language: str = ""                  # "" = not chosen yet (first run)
    installer_language: str = ""        # last language seen from the installer

    # device
    connection: str = "rfcomm"          # rfcomm | serial | simulator
    address: str = ""
    device_name: str = ""
    channel: int = 1
    com_port: str = ""
    auto_connect: bool = False
    auto_reconnect: bool = True
    brightness: int = 80

    # transfer (values measured on a real MiniToo, 2026-11-26)
    chunk_interval_ms: float = 4.0
    auto_pacing: bool = True
    min_interval_ms: float = 2.0
    request_timeout: float = 1.5
    ack_timeout: float = 2.0

    # picture
    fmt_key: str = "jpeg160"
    jpeg_quality: int = 95
    still_quality: int = 95
    chroma_444: bool = True
    max_frame_kb: int = 0
    fit_mode: str = "fit"
    rotation: int = 0
    flip_h: bool = False
    contrast: float = 1.0
    saturation: float = 1.0
    sharpen: bool = False
    show_cursor: bool = True
    high_quality: bool = True

    # mirror
    source_kind: str = "monitor"        # monitor | window | region | image | video
    monitor: int = 1
    region: List[int] = field(default_factory=list)
    window_title: str = ""
    window_process: str = ""
    client_only: bool = True
    window_method: str = "screen"
    target_fps: float = 15.0
    skip_unchanged: bool = True
    keepalive_s: float = 0.0
    batch_frames: int = 1
    batch_interval_ms: int = 150

    # video
    video_loop: bool = True
    clip_fps: int = 12
    clip_seconds: float = 5.0

    # window
    geometry: str = ""
    window_state: str = ""
    last_image_dir: str = ""
    last_video_dir: str = ""

    @classmethod
    def path(cls) -> str:
        return os.path.join(app_dir(), "settings.json")

    @classmethod
    def exists(cls, path: Optional[str] = None) -> bool:
        return os.path.exists(path or cls.path())

    @classmethod
    def load(cls, path: Optional[str] = None) -> "AppSettings":
        path = path or cls.path()
        s = cls()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data: Dict[str, Any] = json.load(f)
        except FileNotFoundError:
            return s
        except (OSError, ValueError) as e:
            log.warning("Settings could not be read (%s) - using defaults", e)
            return s
        if not isinstance(data, dict):
            return s
        for fld in fields(cls):
            if fld.name in data:
                default = getattr(s, fld.name)
                value = data[fld.name]
                try:
                    if isinstance(default, bool):
                        value = bool(value)
                    elif isinstance(default, int):
                        value = int(value)
                    elif isinstance(default, float):
                        value = float(value)
                    elif isinstance(default, str):
                        value = str(value)
                    elif isinstance(default, list):
                        value = list(value)
                except (TypeError, ValueError):
                    continue
                setattr(s, fld.name, value)
        s._migrate(int(data.get("version", 0) or 0))
        return s

    def _migrate(self, file_version: int) -> None:
        """Values that changed after measuring on a real device are updated
        in settings files written by older versions."""
        if file_version < 2:
            self.chunk_interval_ms = 4.0
            self.min_interval_ms = 2.0
            self.request_timeout = 1.5
            self.ack_timeout = 2.0
            self.jpeg_quality = max(self.jpeg_quality, 95)
            self.still_quality = max(self.still_quality, 95)
            self.chroma_444 = True
            self.target_fps = max(self.target_fps, 15.0)
            if self.connection == "simulator":
                self.connection = "rfcomm"
            self.geometry = ""
        self.version = SETTINGS_VERSION

    def save(self, path: Optional[str] = None) -> None:
        path = path or self.path()
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except OSError as e:
            log.warning("Settings could not be saved: %s", e)
