"""Video source: decoding, seeking, clips and mirroring a video to the simulator."""

import os
import tempfile
import time
import unittest

from minitoo import video
from minitoo.imaging import make_number_card


def make_sample(path, seconds=2.0, fps=10, size=(320, 180)):
    import av
    c = av.open(path, "w")
    s = c.add_stream("mpeg4", rate=fps)
    s.width, s.height, s.pix_fmt = size[0], size[1], "yuv420p"
    for i in range(int(seconds * fps)):
        img = make_number_card(size[0], size[1], str(i))
        for p in s.encode(av.VideoFrame.from_image(img)):
            c.mux(p)
    for p in s.encode():
        c.mux(p)
    c.close()


@unittest.skipUnless(video.available(), "PyAV not installed")
class VideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "sample.mp4")
        make_sample(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_info_and_seek(self):
        p = video.VideoPlayer(self.path)
        self.addCleanup(p.close)
        self.assertAlmostEqual(p.info.duration, 2.0, delta=0.15)
        self.assertEqual((p.info.width, p.info.height), (320, 180))
        first = p.current_frame()
        self.assertIsNotNone(first)
        p.seek(1.5)
        later = p.current_frame()
        self.assertNotEqual(first.tobytes(), later.tobytes())
        p.seek(0.0)
        again = p.current_frame()
        self.assertEqual(first.tobytes(), again.tobytes())

    def test_play_pause_end(self):
        p = video.VideoPlayer(self.path)
        self.addCleanup(p.close)
        p.loop = False
        p.seek(1.8)
        p.play()
        time.sleep(0.4)
        self.assertGreaterEqual(p.position, 1.99)
        self.assertFalse(p.playing)
        self.assertTrue(p.ended)
        p.play()                              # restarts from the beginning
        self.assertLess(p.position, 0.5)
        p.pause()
        pos = p.position
        time.sleep(0.2)
        self.assertAlmostEqual(p.position, pos, delta=0.01)

    def test_loop(self):
        p = video.VideoPlayer(self.path)
        self.addCleanup(p.close)
        p.loop = True
        p.seek(1.9)
        p.play()
        time.sleep(0.3)
        self.assertLess(p.position, 0.5)
        self.assertTrue(p.playing)

    def test_extract_clip(self):
        frames = video.extract_clip(self.path, 0.5, 1.0, 5)
        self.assertEqual(len(frames), 5)
        self.assertEqual(len({f.tobytes() for f in frames}), 5)   # all different
        self.assertEqual(len(video.extract_clip(self.path, 1.5, 5.0, 10)), 5)   # stops at the end

    def test_mirror_video_to_simulator(self):
        from minitoo.device import MiniTooDevice, UploadSettings
        from minitoo.mirror import MirrorConfig, MirrorEngine
        from minitoo.simulator import FakeMiniToo, SimulatedTransport

        fake = FakeMiniToo(bytes_per_sec=0, decode_ms=0)
        dev = MiniTooDevice(lambda: SimulatedTransport(fake),
                            UploadSettings(chunk_interval_ms=0, ack_timeout=0.5))
        dev.connect()
        self.addCleanup(dev.disconnect)
        p = video.VideoPlayer(self.path)
        self.addCleanup(p.close)
        p.play()
        eng = MirrorEngine(dev, MirrorConfig(target_fps=20),
                           grabber_factory=lambda: video.VideoGrabber(p))
        eng.start()
        time.sleep(1.0)
        eng.stop()
        self.assertGreaterEqual(fake.uploads, 3)
        self.assertEqual(fake.last_image.size, (160, 128))

    def test_bad_file(self):
        bad = os.path.join(self.tmp.name, "bad.mp4")
        with open(bad, "wb") as f:
            f.write(b"not a video")
        with self.assertRaises(video.VideoError):
            video.VideoPlayer(bad)

    def test_sound_track(self):
        import array
        import math

        import av
        path = os.path.join(self.tmp.name, "with_sound.mp4")
        c = av.open(path, "w")
        vs = c.add_stream("mpeg4", rate=10)
        vs.width, vs.height, vs.pix_fmt = 160, 128, "yuv420p"
        a = c.add_stream("aac", rate=48000)
        a.layout = "stereo"
        for i in range(10):
            for p in vs.encode(av.VideoFrame.from_image(make_number_card(160, 128, str(i)))):
                c.mux(p)
        for k in range(40):
            fr = av.AudioFrame(format="s16", layout="stereo", samples=1024)
            fr.planes[0].update(array.array("h", [0 * int(math.sin(j / 9)) for j in range(2048)]).tobytes())
            fr.sample_rate = 48000
            for p in a.encode(fr):
                c.mux(p)
        for st in (vs, a):
            for p in st.encode():
                c.mux(p)
        c.close()
        silent = video.VideoPlayer(self.path, -1)
        self.addCleanup(silent.close)
        self.assertFalse(silent.has_audio)
        p = video.VideoPlayer(path, -1)          # may have no audio device on CI - must not fail
        self.addCleanup(p.close)
        self.assertTrue(p.has_audio)
        p.play()
        time.sleep(0.3)
        import threading
        ts = [threading.Thread(target=lambda: [p.audio.play_from(0.2) for _ in range(4)])
              for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertLessEqual(p.audio.playing_count, 1)      # never two sounds at once
        for i in range(10):
            p.seek(i * 0.05, restart_audio=False)
        p.seek(0.5)
        self.assertLessEqual(p.audio.playing_count, 1)
        p.pause()
        time.sleep(0.1)
        self.assertEqual(p.audio.playing_count, 0)
        p.set_audio_device(None)
        self.assertFalse(p.has_audio)

    def test_format_time(self):
        self.assertEqual(video.format_time(75), "1:15")
        self.assertEqual(video.format_time(3725), "1:02:05")


if __name__ == "__main__":
    unittest.main()
