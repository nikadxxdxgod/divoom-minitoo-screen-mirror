"""Video files as a source (PyAV / FFmpeg).

Two ways to show a video on the MiniToo:

* **Play** - the video plays in real time on the PC and the newest frame is
  uploaded again and again, exactly like screen mirroring (~5-7 updates/s
  over Bluetooth; frames in between are skipped). Any length.
* **Upload clip as animation** - a short part of the video is converted into
  one multi-frame animation that the device plays by itself, smoothly and
  in a loop. Limited by the safe upload size (a few seconds).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from PIL import Image

from .capture import Rect

log = logging.getLogger("minitoo.video")

VIDEO_EXTENSIONS = ("*.mp4", "*.mkv", "*.mov", "*.avi", "*.webm", "*.m4v", "*.wmv", "*.flv",
                    "*.mpg", "*.mpeg", "*.ts", "*.gif")


class VideoError(Exception):
    pass


def available() -> bool:
    try:
        import av  # noqa: F401
        return True
    except ImportError:
        return False


@dataclass
class VideoInfo:
    path: str
    duration: float
    fps: float
    width: int
    height: int
    codec: str

    @property
    def frame_count(self) -> int:
        return int(self.duration * self.fps)


def _reduced_size(w: int, h: int, target: int = 320) -> Tuple[int, int]:
    """Decode-side downscale: keep about 2x the device resolution."""
    s = min(1.0, target / max(1, min(w, h)))
    return max(2, int(w * s) // 2 * 2), max(2, int(h * s) // 2 * 2)


class VideoDecoder:
    """Sequential decoder with seeking. Not thread-safe by itself - VideoPlayer
    serializes access."""

    def __init__(self, path: str) -> None:
        try:
            import av
        except ImportError as e:  # pragma: no cover
            raise VideoError("PyAV is not installed (pip install av)") from e
        try:
            self._container = av.open(path)
        except Exception as e:
            raise VideoError(str(e)) from e
        streams = self._container.streams.video
        if not streams:
            self._container.close()
            raise VideoError("no video stream")
        st = streams[0]
        st.thread_type = "AUTO"
        self._stream = st
        rate = st.average_rate or st.guessed_rate or 25
        fps = float(rate) if rate else 25.0
        if st.duration is not None and st.time_base is not None:
            duration = float(st.duration * st.time_base)
        elif self._container.duration:
            duration = self._container.duration / 1_000_000
        else:
            duration = 0.0
        w = st.codec_context.width or 0
        h = st.codec_context.height or 0
        self.info = VideoInfo(path, max(0.0, duration), fps if fps > 0 else 25.0, w, h,
                              st.codec_context.name or "?")
        self._out_size = _reduced_size(w, h) if w and h else None
        self._iter = None
        self._last: Optional[Image.Image] = None
        self._last_t = -1.0
        self._next_t = 0.0
        self._start = float(st.start_time * st.time_base) if st.start_time is not None else 0.0

    def close(self) -> None:
        try:
            self._container.close()
        except Exception:
            pass

    def _frames(self):
        if self._iter is None:
            self._iter = self._container.decode(self._stream)
        return self._iter

    def _seek(self, t: float) -> None:
        st = self._stream
        target = int((t + self._start) / float(st.time_base)) if st.time_base else 0
        try:
            self._container.seek(max(0, target), stream=st, backward=True, any_frame=False)
        except Exception as e:
            log.debug("seek failed: %s", e)
            self._container.seek(0)
        self._iter = None
        self._last = None
        self._last_t = -1.0

    def _to_image(self, frame) -> Image.Image:
        if self._out_size:
            w, h = self._out_size
            return frame.to_image(width=w, height=h)
        return frame.to_image()

    def frame_at(self, t: float) -> Optional[Image.Image]:
        """The frame shown at time t (seconds from the start)."""
        t = max(0.0, t)
        half = 0.5 / max(1.0, self.info.fps)
        if self._last is not None and self._last_t - half <= t < self._next_t:
            return self._last                       # still the same frame
        if self._last is None or t < self._last_t - half or t > self._last_t + 2.0:
            self._seek(t)
        prev = self._last
        prev_t = self._last_t
        try:
            for frame in self._frames():
                ft = (float(frame.time) - self._start) if frame.time is not None else prev_t + 1 / self.info.fps
                if ft + half < t:
                    prev, prev_t = frame, ft         # keep decoding (cheap: no conversion)
                    continue
                img = self._to_image(frame)
                self._last, self._last_t = img, ft
                self._next_t = ft + 1.0 / max(1.0, self.info.fps)
                return img
        except Exception as e:                       # corrupt data / end of stream
            log.debug("decode stopped: %s", e)
        # end of stream: show the last decoded frame
        if prev is not None and not isinstance(prev, Image.Image):
            img = self._to_image(prev)
            self._last, self._last_t, self._next_t = img, prev_t, float("inf")
            return img
        return self._last


class VideoPlayer:
    """Real-time playback clock (play / pause / seek / loop) + decoder, and
    optionally the sound (audio.AudioTrack) kept in sync with the picture."""

    def __init__(self, path: str, audio_device: Optional[int] = None) -> None:
        self._dec = VideoDecoder(path)
        self.info = self._dec.info
        self.loop = True
        self._lock = threading.Lock()
        self._playing = False
        self._base_pos = 0.0
        self._base_t = time.perf_counter()
        self._last_pos = 0.0
        self.ended = False
        self.audio = None
        self.set_audio_device(audio_device)

    @property
    def has_audio(self) -> bool:
        return self.audio is not None and self.audio.has_audio

    def set_audio_device(self, device: Optional[int]) -> None:
        """None = no sound, -1 = Windows default output, n = waveOut device n."""
        old, self.audio = self.audio, None
        if old is not None:
            old.stop()
        if device is None:
            return
        try:
            from .audio import AudioTrack
            self.audio = AudioTrack(self.info.path, device)
        except Exception as e:  # pragma: no cover
            log.warning("no sound: %s", e)
            return
        if self._playing:
            self.audio.play_from(self.position)

    def close(self) -> None:
        if self.audio is not None:
            self.audio.stop()
        with self._lock:
            self._dec.close()

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def position(self) -> float:
        with self._lock:
            return self._position_locked()

    def _position_locked(self) -> float:
        pos = self._base_pos
        if self._playing:
            pos += time.perf_counter() - self._base_t
        dur = self.info.duration
        if dur > 0 and pos >= dur:
            if self.loop:
                pos %= dur
            else:
                pos = dur
                self._playing = False
                self._base_pos = dur
                self.ended = True
        if self._playing and pos < self._last_pos - 0.5 and self.audio is not None:
            self.audio.play_from(pos)                   # looped: sound starts again too
        self._last_pos = pos
        return pos

    def play(self) -> None:
        with self._lock:
            if self.ended or (self.info.duration and self._base_pos >= self.info.duration):
                self._base_pos = 0.0
                self.ended = False
            self._base_t = time.perf_counter()
            self._last_pos = self._base_pos
            self._playing = True
            pos = self._base_pos
        if self.audio is not None:
            self.audio.play_from(pos)

    def pause(self) -> None:
        with self._lock:
            self._base_pos = self._position_locked()
            self._playing = False
        if self.audio is not None:
            self.audio.stop()

    def toggle(self) -> None:
        if self._playing:
            self.pause()
        else:
            self.play()

    def seek(self, t: float) -> None:
        with self._lock:
            dur = self.info.duration
            self._base_pos = min(max(0.0, t), dur) if dur else max(0.0, t)
            self._base_t = time.perf_counter()
            self._last_pos = self._base_pos
            self.ended = False
            playing, pos = self._playing, self._base_pos
        if self.audio is not None and playing:
            self.audio.play_from(pos)

    def _resync(self) -> None:
        """Keep the picture on the sound's clock (sound devices drift a little)."""
        a = self.audio
        if a is None or not self._playing or not a.active:
            return
        apos = a.position()
        if apos is None:
            return
        wall = self._base_pos + time.perf_counter() - self._base_t
        if abs(wall - apos) > 0.3 and (not self.info.duration or apos < self.info.duration):
            self._base_pos, self._base_t = apos, time.perf_counter()

    def current_frame(self) -> Optional[Image.Image]:
        with self._lock:
            self._resync()
            pos = self._position_locked()
            return self._dec.frame_at(pos)


class VideoGrabber:
    """Grabber interface for MirrorEngine (like capture.ScreenGrabber)."""

    def __init__(self, player: VideoPlayer) -> None:
        self.player = player

    def grab(self, src=None, with_cursor: bool = False):
        img = self.player.current_frame()
        if img is None:
            from .capture import SourceUnavailable
            from .i18n import _
            raise SourceUnavailable(_("The video has no frames to show."))
        return img, None, Rect(0, 0, img.width, img.height)

    def refresh_monitors(self) -> None:
        pass

    def close(self) -> None:
        pass


def extract_clip(path: str, start: float, seconds: float, fps: float,
                 progress: Optional[Callable[[int, int], None]] = None,
                 cancel: Optional[threading.Event] = None) -> List[Image.Image]:
    """Frames for an on-device animation: `seconds` of video from `start`,
    sampled at `fps` frames per second (max 255 frames)."""
    dec = VideoDecoder(path)
    try:
        dur = dec.info.duration or (start + seconds)
        start = max(0.0, min(start, max(0.0, dur - 0.05)))
        n = max(1, min(255, int(round(seconds * fps))))
        frames: List[Image.Image] = []
        for i in range(n):
            if cancel is not None and cancel.is_set():
                break
            t = start + i / fps
            if dur and t >= dur:
                break
            img = dec.frame_at(t)
            if img is None:
                break
            frames.append(img.copy())
            if progress is not None:
                progress(i + 1, n)
        return frames
    finally:
        dec.close()


def format_time(t: float) -> str:
    t = max(0, int(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
