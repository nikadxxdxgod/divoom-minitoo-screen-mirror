import io
import unittest

from PIL import Image

from minitoo import encoder as E
from minitoo import imaging as I
from minitoo.protocol import FORMATS, parse_payload


class ImagingTests(unittest.TestCase):
    def test_fit_modes_exact_size(self):
        src = Image.new("RGB", (1920, 1080), (200, 10, 10))
        for mode in I.FIT_MODES:
            out, tf = I.fit_image(src, (160, 128), mode)
            self.assertEqual(out.size, (160, 128), mode)
        out, tf = I.fit_image(src, (160, 128), "fit")
        self.assertEqual(out.getpixel((80, 2)), (0, 0, 0))        # bar at the top
        self.assertNotEqual(out.getpixel((80, 64)), (0, 0, 0))
        out, _ = I.fit_image(src, (160, 128), "fill")
        self.assertNotEqual(out.getpixel((80, 2)), (0, 0, 0))     # cropped - no bar

    def test_transform_maps_corners(self):
        src = Image.new("RGB", (1600, 1280))
        _, tf = I.fit_image(src, (160, 128), "stretch")
        self.assertEqual(tf.map(1600, 1280), (160, 128))
        _, tf = I.fit_image(Image.new("RGB", (2000, 1000)), (160, 128), "fit")
        x, y = tf.map(0, 0)
        self.assertAlmostEqual(x, 0)
        self.assertAlmostEqual(y, 24)

    def test_rotation_keeps_output_size(self):
        src = I.make_test_pattern(300, 200)
        for rot in I.ROTATIONS:
            opts = I.ImageOptions(rotation=rot, flip_h=rot == 90)
            self.assertEqual(I.process_frame(src, (160, 128), opts, (10, 10)).size, (160, 128))

    def test_rotation_direction(self):
        img = Image.new("RGB", (4, 2), (0, 0, 0))
        img.putpixel((0, 0), (255, 0, 0))               # red top-left
        r = I.apply_orientation(img, 90, False)         # clockwise
        self.assertEqual(r.size, (2, 4))
        self.assertEqual(r.getpixel((1, 0)), (255, 0, 0))  # -> top-right

    def test_mean_difference(self):
        a = Image.new("RGB", (16, 16), (10, 10, 10))
        self.assertEqual(I.mean_difference(a, a.copy()), 0)
        self.assertGreater(I.mean_difference(a, Image.new("RGB", (16, 16), (90, 10, 10))), 20)

    def test_cards(self):
        self.assertEqual(I.make_number_card(160, 128, "7", "x").size, (160, 128))
        self.assertEqual(I.make_test_pattern(128, 128).size, (128, 128))


class EncoderTests(unittest.TestCase):
    def test_jpeg_payload_is_baseline_160x128(self):
        enc = E.FrameEncoder("jpeg160", 70)
        ef = enc.encode([I.make_test_pattern(160, 128)])
        p = parse_payload(ef.payload)
        self.assertEqual((p.marker, p.rows, p.cols, p.frame_count, p.speed_ms), (0x23, 8, 10, 1, 0))
        jpg = Image.open(io.BytesIO(p.frames[0]))
        self.assertEqual(jpg.size, (160, 128))
        self.assertFalse(jpg.info.get("progressive") or jpg.info.get("progression"))
        self.assertEqual(ef.preview.size, (160, 128))
        self.assertLess(len(ef.payload), 20_000)

    def test_chroma_subsampling(self):
        from PIL import JpegImagePlugin
        img = I.make_test_pattern(160, 128)
        for chroma, expected in ((True, 0), (False, 2)):         # 0 = 4:4:4, 2 = 4:2:0
            ef = E.FrameEncoder("jpeg160", 95, chroma_444=chroma).encode([img])
            jpg = Image.open(io.BytesIO(parse_payload(ef.payload).frames[0]))
            self.assertEqual(JpegImagePlugin.get_sampling(jpg), expected)
        self.assertEqual(E.FrameEncoder("jpeg160", 100).jpeg_quality, 100)

    def test_quality_budget(self):
        enc = E.FrameEncoder("jpeg160", 95, max_frame_bytes=3000)
        noisy = Image.effect_noise((160, 128), 80).convert("RGB")
        ef = enc.encode([noisy])
        self.assertLess(ef.quality, 95)
        self.assertGreaterEqual(ef.quality, E.FrameEncoder.MIN_AUTO_QUALITY)

    def test_resizes_wrong_input(self):
        ef = E.FrameEncoder("jpeg160").encode([Image.new("L", (50, 50))])
        self.assertEqual(ef.preview.size, (160, 128))

    @unittest.skipIf(E.zstd_compress is None, "zstd not available")
    def test_zstd_payload_roundtrip_and_window(self):
        enc = E.FrameEncoder("zstd128")
        img = I.make_test_pattern(128, 128)
        ef = enc.encode([img])
        p = parse_payload(ef.payload)
        self.assertEqual((p.marker, p.rows, p.cols, p.speed_ms), (0x25, 8, 8, 1000))
        # [H][A]: window <= 128 KiB, otherwise the device shows a black screen
        self.assertLessEqual(E.zstd_window_size(p.frames[0]), 128 * 1024)
        back = E.decode_payload_image(ef.payload, FORMATS["zstd128"])
        self.assertEqual(back.tobytes(), img.tobytes())

    @unittest.skipIf(E.zstd_compress is None, "zstd not available")
    def test_zstd_window_large_input(self):
        enc = E.FrameEncoder("zstd128", zstd_level=19)
        frames = [Image.effect_noise((128, 128), 60 + i).convert("RGB") for i in range(6)]
        ef = enc.encode(frames, speed_ms=200)
        p = parse_payload(ef.payload)
        self.assertLessEqual(E.zstd_window_size(p.frames[0]), 128 * 1024)
        self.assertEqual(p.frame_count, 6)

    def test_animation_decode(self):
        frames = [I.make_number_card(160, 128, str(i)) for i in range(3)]
        ef = E.FrameEncoder("jpeg160", 80).encode(frames, speed_ms=120)
        p = parse_payload(ef.payload)
        self.assertEqual((p.frame_count, p.speed_ms), (3, 120))
        self.assertEqual(E.decode_payload_image(ef.payload, FORMATS["jpeg160"], 2).size, (160, 128))


if __name__ == "__main__":
    unittest.main()
