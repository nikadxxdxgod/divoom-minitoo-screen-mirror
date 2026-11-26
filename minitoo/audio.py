"""Sound for video files: decoded with PyAV, played with the Windows waveOut API
(ctypes - no extra package). The output device can be chosen.

Note: if the sound goes to the MiniToo itself, the speaker may show its own
sound visualizer instead of the picture (described in [H]).
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from ctypes import wintypes
from typing import List, Optional, Tuple

log = logging.getLogger("minitoo.audio")

RATE = 48000
CHANNELS = 2
BYTES_PER_SEC = RATE * CHANNELS * 2
WAVE_MAPPER = -1                      # Windows default output
BUFFERS = 6
BUFFER_SEC = 0.08

if sys.platform == "win32":
    winmm = ctypes.WinDLL("winmm")

    class WAVEFORMATEX(ctypes.Structure):
        _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                    ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
                    ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
                    ("cbSize", wintypes.WORD)]

    class WAVEHDR(ctypes.Structure):
        pass

    WAVEHDR._fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                        ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t),
                        ("dwFlags", wintypes.DWORD), ("dwLoops", wintypes.DWORD),
                        ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]

    class WAVEOUTCAPSW(ctypes.Structure):
        _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                    ("szPname", wintypes.WCHAR * 32), ("dwFormats", wintypes.DWORD),
                    ("wChannels", wintypes.WORD), ("wReserved1", wintypes.WORD),
                    ("dwSupport", wintypes.DWORD)]

    class MMTIME(ctypes.Structure):
        _fields_ = [("wType", wintypes.UINT), ("u", wintypes.DWORD * 2)]

    HWAVEOUT = ctypes.c_void_p
    winmm.waveOutOpen.argtypes = [ctypes.POINTER(HWAVEOUT), wintypes.UINT, ctypes.POINTER(WAVEFORMATEX),
                                  ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
    for name in ("waveOutPrepareHeader", "waveOutUnprepareHeader", "waveOutWrite"):
        getattr(winmm, name).argtypes = [HWAVEOUT, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    for name in ("waveOutReset", "waveOutClose", "waveOutPause", "waveOutRestart"):
        getattr(winmm, name).argtypes = [HWAVEOUT]
    winmm.waveOutGetPosition.argtypes = [HWAVEOUT, ctypes.POINTER(MMTIME), wintypes.UINT]
    winmm.waveOutGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(WAVEOUTCAPSW), wintypes.UINT]

WHDR_DONE = 0x1
TIME_BYTES = 0x4


def output_devices() -> List[Tuple[int, str]]:
    """[(device id, name)] - names are cut to 31 characters by Windows."""
    if sys.platform != "win32":
        return []
    out = []
    for i in range(winmm.waveOutGetNumDevs()):
        caps = WAVEOUTCAPSW()
        if winmm.waveOutGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            out.append((i, caps.szPname))
    return out


class AudioTrack:
    """Plays the sound of a media file from a given position (one at a time)."""

    def __init__(self, path: str, device_id: int = WAVE_MAPPER) -> None:
        self.path = path
        self.device_id = device_id
        self.has_audio = self._probe()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._hwo = None
        self._start_pos = 0.0
        self._paused = False

    def _probe(self) -> bool:
        try:
            import av
            with av.open(self.path) as c:
                return bool(c.streams.audio)
        except Exception:
            return False

    # ---------------------------------------------------------------- control

    def play_from(self, t: float) -> None:
        if not self.has_audio or sys.platform != "win32":
            return
        self.stop()
        self._stop = threading.Event()
        self._start_pos = max(0.0, t)
        self._paused = False
        self._thread = threading.Thread(target=self._run, args=(self._start_pos, self._stop),
                                        name="minitoo-audio", daemon=True)
        self._thread.start()

    def pause(self) -> None:
        with self._lock:
            if self._hwo is not None and not self._paused:
                winmm.waveOutPause(self._hwo)
                self._paused = True

    def resume(self) -> None:
        with self._lock:
            if self._hwo is not None and self._paused:
                winmm.waveOutRestart(self._hwo)
                self._paused = False

    def stop(self) -> None:
        self._stop.set()
        th = self._thread
        if th is not None and th is not threading.current_thread():
            th.join(2.0)
        self._thread = None

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def position(self) -> Optional[float]:
        """Seconds of the file that have been heard, or None."""
        with self._lock:
            if self._hwo is None:
                return None
            mt = MMTIME()
            mt.wType = TIME_BYTES
            if winmm.waveOutGetPosition(self._hwo, ctypes.byref(mt), ctypes.sizeof(mt)) != 0:
                return None
            return self._start_pos + mt.u[0] / BYTES_PER_SEC

    # ---------------------------------------------------------------- worker

    def _pcm(self, start: float, stop: threading.Event):
        """Yields 16-bit stereo 48 kHz PCM bytes from `start`."""
        import av
        with av.open(self.path) as c:
            st = c.streams.audio[0]
            if start > 0 and st.time_base:
                c.seek(int(start / float(st.time_base)), stream=st, backward=True)
            res = av.AudioResampler(format="s16", layout="stereo", rate=RATE)
            for frame in c.decode(st):
                if stop.is_set():
                    return
                for rf in res.resample(frame):
                    data = bytes(rf.planes[0])[:rf.samples * 4]
                    t = float(rf.time) if rf.time is not None else start
                    if t + rf.samples / RATE <= start:
                        continue                         # before the seek target
                    if t < start:                         # trim the start of the frame
                        cut = int((start - t) * RATE) * 4
                        data = data[cut:]
                    yield data
            for rf in res.resample(None):
                yield bytes(rf.planes[0])[:rf.samples * 4]

    def _run(self, start: float, stop: threading.Event) -> None:
        fmt = WAVEFORMATEX(1, CHANNELS, RATE, BYTES_PER_SEC, CHANNELS * 2, 16, 0)
        hwo = HWAVEOUT()
        dev = self.device_id if self.device_id >= 0 else 0xFFFFFFFF
        err = winmm.waveOutOpen(ctypes.byref(hwo), dev, ctypes.byref(fmt), 0, 0, 0)
        if err != 0:
            log.warning("audio output could not be opened (MMSYSERR %d)", err)
            return
        size = int(BYTES_PER_SEC * BUFFER_SEC) // 4 * 4
        bufs = [ctypes.create_string_buffer(size) for _ in range(BUFFERS)]
        hdrs = [WAVEHDR() for _ in range(BUFFERS)]
        prepared = [False] * BUFFERS
        with self._lock:
            self._hwo = hwo
        pending = bytearray()
        source = self._pcm(start, stop)
        finished = False
        try:
            i = 0
            while not stop.is_set():
                h = hdrs[i]
                if prepared[i] and not (h.dwFlags & WHDR_DONE):
                    if finished and all(not p or (hd.dwFlags & WHDR_DONE) for p, hd in zip(prepared, hdrs)):
                        break
                    time.sleep(0.01)
                    continue
                if prepared[i]:
                    winmm.waveOutUnprepareHeader(hwo, ctypes.byref(h), ctypes.sizeof(h))
                    prepared[i] = False
                while len(pending) < size and not finished:
                    try:
                        pending += next(source)
                    except StopIteration:
                        finished = True
                if not pending:
                    if all(not p for p in prepared):
                        break
                    i = (i + 1) % BUFFERS
                    time.sleep(0.01)
                    continue
                chunk = bytes(pending[:size])
                del pending[:size]
                ctypes.memmove(bufs[i], chunk, len(chunk))
                h.lpData = ctypes.cast(bufs[i], ctypes.c_void_p)
                h.dwBufferLength = len(chunk)
                h.dwFlags = 0
                winmm.waveOutPrepareHeader(hwo, ctypes.byref(h), ctypes.sizeof(h))
                winmm.waveOutWrite(hwo, ctypes.byref(h), ctypes.sizeof(h))
                prepared[i] = True
                i = (i + 1) % BUFFERS
        except Exception as e:  # pragma: no cover
            log.warning("audio stopped: %s", e)
        finally:
            with self._lock:
                self._hwo = None
            winmm.waveOutReset(hwo)
            for k, h in enumerate(hdrs):
                if prepared[k]:
                    winmm.waveOutUnprepareHeader(hwo, ctypes.byref(h), ctypes.sizeof(h))
            winmm.waveOutClose(hwo)
