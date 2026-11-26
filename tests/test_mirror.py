"""Mirror engine: a fake "screen" + the virtual device."""

import os
import sys
import tempfile
import time
import unittest

from PIL import Image

from minitoo.capture import CaptureSource, Rect, WindowGone
from minitoo.device import MiniTooDevice, UploadSettings
from minitoo.imaging import ImageOptions, make_number_card
from minitoo.mirror import MirrorConfig, MirrorEngine, changed_pixels, send_animation, send_still
from minitoo.settings import AppSettings
from minitoo.simulator import FakeMiniToo, SimulatedTransport


class FakeGrabber:
    """A "screen" that the test changes itself."""

    def __init__(self) -> None:
        self.frame = Image.new("RGB", (640, 400), (30, 30, 30))
        self.calls = 0
        self.gone_after = 0

    def grab(self, src, with_cursor=True):
        self.calls += 1
        if self.gone_after and self.calls > self.gone_after:
            raise WindowGone("The window was closed.")
        return self.frame.copy(), (100.0, 100.0), Rect(0, 0, 640, 400)

    def refresh_monitors(self):
        pass

    def close(self):
        pass


def wait_until(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeMiniToo(bytes_per_sec=0, decode_ms=0)
        self.dev = MiniTooDevice(lambda: SimulatedTransport(self.fake),
                                 UploadSettings(chunk_interval_ms=0, request_timeout=0.5,
                                                ack_timeout=0.5))
        self.dev.connect()
        self.addCleanup(self.dev.disconnect)
        self.grabber = FakeGrabber()

    def engine(self, **kw):
        cfg = MirrorConfig(source=CaptureSource("monitor", 1), **kw)
        self.stats, self.previews, self.stopped = [], [], []
        eng = MirrorEngine(self.dev, cfg, on_preview=lambda img, ef, res: self.previews.append(img),
                           on_stats=self.stats.append, on_stopped=self.stopped.append,
                           grabber_factory=lambda: self.grabber)
        self.addCleanup(eng.stop)
        return eng

    def test_sends_and_skips_unchanged(self):
        eng = self.engine(target_fps=30, skip_unchanged=True)
        eng.start()
        self.assertTrue(wait_until(lambda: self.fake.uploads >= 1))
        time.sleep(0.4)
        self.assertEqual(self.fake.uploads, 1)          # screen unchanged -> nothing more is sent
        self.assertGreater(eng.stats.skipped, 0)
        self.grabber.frame = Image.new("RGB", (640, 400), (200, 40, 40))
        self.assertTrue(wait_until(lambda: self.fake.uploads >= 2))
        eng.stop()
        self.assertFalse(eng.running)
        self.assertEqual(self.stopped, [""])
        self.assertEqual(self.previews[-1].size, (160, 128))
        self.assertTrue(self.stats[-1].uploads >= 2)

    def test_continuous_without_skip_and_fps_limit(self):
        eng = self.engine(target_fps=5, skip_unchanged=False)
        eng.start()
        time.sleep(1.6)
        eng.stop()
        # 5 fps limit: ~8 in 1.6 s (not 30+)
        self.assertGreaterEqual(self.fake.uploads, 5)
        self.assertLessEqual(self.fake.uploads, 10)
        st = eng.stats
        self.assertGreater(st.fps, 2)
        self.assertGreater(st.kbps, 0)

    def test_keepalive(self):
        eng = self.engine(target_fps=30, skip_unchanged=True, keepalive_s=0.3)
        eng.start()
        time.sleep(1.1)
        eng.stop()
        self.assertGreaterEqual(self.fake.uploads, 3)

    def test_batch_animation(self):
        eng = self.engine(batch_frames=3, batch_interval_ms=50)
        eng.start()
        self.assertTrue(wait_until(lambda: self.fake.uploads >= 1))
        eng.stop()
        from minitoo.protocol import parse_payload
        p = parse_payload(self.fake.last_payload)
        self.assertEqual((p.frame_count, p.speed_ms), (3, 50))

    def test_live_config_update(self):
        eng = self.engine(target_fps=30, skip_unchanged=False)
        eng.start()
        self.assertTrue(wait_until(lambda: self.fake.uploads >= 1))
        cfg = MirrorConfig(source=CaptureSource("monitor", 1), fmt_key="zstd128", target_fps=30,
                           skip_unchanged=False)
        eng.update_config(cfg)
        self.assertTrue(wait_until(lambda: self.fake.last_payload[:1] == b"\x25"))
        eng.stop()
        self.assertEqual(self.fake.last_image.size, (128, 128))

    def test_window_gone_stops(self):
        self.grabber.gone_after = 2
        eng = self.engine(target_fps=30, skip_unchanged=False)
        eng.start()
        self.assertTrue(wait_until(lambda: not eng.running))
        self.assertTrue(self.stopped and "closed" in self.stopped[0])

    def test_waits_for_reconnect(self):
        self.fake.disconnect_after_uploads = 3
        self.dev.auto_reconnect = True
        eng = self.engine(target_fps=30, skip_unchanged=False)
        eng.start()
        self.assertTrue(wait_until(lambda: self.fake.uploads >= 6, timeout=8))
        eng.stop()
        self.assertGreaterEqual(self.dev.stats.reconnects, 1)

    def test_send_still_and_animation(self):
        ef, res = send_still(self.dev, Image.new("RGB", (800, 600), (0, 128, 255)), ImageOptions())
        self.assertTrue(res.ok)
        self.assertEqual(self.fake.last_image.size, (160, 128))
        frames = [make_number_card(300, 200, str(i)) for i in range(5)]
        ef, res = send_animation(self.dev, frames, ImageOptions(), 120)
        self.assertTrue(res.ok)
        self.assertEqual(ef.frame_count, 5)

    def test_changed_pixels(self):
        a = Image.new("RGB", (160, 128))
        b = a.copy()
        self.assertEqual(changed_pixels(a, b), 0)
        b.putpixel((5, 5), (255, 255, 255))
        self.assertEqual(changed_pixels(a, b), 1)
        b.putpixel((6, 5), (10, 10, 10))                # JPEG noise - not counted
        self.assertEqual(changed_pixels(a, b), 1)
        self.assertGreater(changed_pixels(a, None), 10 ** 6)


class SettingsTests(unittest.TestCase):
    def test_roundtrip_and_bad_values(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            s = AppSettings(address="AA:BB:CC:DD:EE:FF", target_fps=7.0, region=[1, 2, 300, 200])
            s.save(path)
            back = AppSettings.load(path)
            self.assertEqual(back.address, s.address)
            self.assertEqual(back.region, [1, 2, 300, 200])
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"target_fps": "abc", "channel": "3", "unknown": 1}')
            back = AppSettings.load(path)
            self.assertEqual(back.target_fps, 15.0)
            self.assertEqual(back.channel, 3)
            # old (v0) file -> measured values
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"chunk_interval_ms": 20, "jpeg_quality": 70, "connection": "simulator"}')
            back = AppSettings.load(path)
            self.assertEqual((back.chunk_interval_ms, back.jpeg_quality, back.connection),
                             (4.0, 95, "rfcomm"))
            self.assertTrue(back.chroma_444)
            # in a new (v2) file the user's choice stays
            s = AppSettings(chunk_interval_ms=12.0, jpeg_quality=60)
            s.save(path)
            back = AppSettings.load(path)
            self.assertEqual((back.chunk_interval_ms, back.jpeg_quality), (12.0, 60))
            with open(path, "w", encoding="utf-8") as f:
                f.write("not json")
            self.assertEqual(AppSettings.load(path).channel, 1)


@unittest.skipUnless(sys.platform == "win32" and not os.environ.get("CI"),
                     "needs an interactive Windows desktop")
class CaptureSmokeTests(unittest.TestCase):
    def test_monitor_and_region(self):
        from minitoo.capture import ScreenGrabber, list_monitors, virtual_screen
        mons = list_monitors()
        self.assertTrue(mons)
        with ScreenGrabber() as g:
            img, cur, rect = g.grab(CaptureSource("monitor", 1))
            self.assertGreater(img.width, 100)
            self.assertEqual(rect, mons[0].rect)
            region = Rect(mons[0].rect.left + 10, mons[0].rect.top + 10, 320, 256)
            img, cur, rect = g.grab(CaptureSource("region", region=region))
            self.assertEqual(rect, region)
            self.assertEqual(img.size, (320, 256))
            vs = virtual_screen()
            outside = Rect(vs.right + 100, vs.bottom + 100, 50, 50)
            from minitoo.capture import SourceUnavailable
            with self.assertRaises(SourceUnavailable):
                g.grab(CaptureSource("region", region=outside))

    def test_closed_window(self):
        from minitoo.capture import ScreenGrabber
        with ScreenGrabber() as g:
            with self.assertRaises(WindowGone):
                g.grab(CaptureSource("window", hwnd=0x7FFFFFF0))


if __name__ == "__main__":
    unittest.main()
