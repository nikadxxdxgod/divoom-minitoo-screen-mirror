import random
import unittest

from minitoo import protocol as P


def gist_build_packet(command: int, data: bytes = b"") -> bytes:
    """[G]'s build_packet verbatim - our code must match it."""
    total = 1 + 2 + 1 + len(data) + 2 + 1
    length_field = total - 4
    packet = bytearray(total)
    packet[0] = 0x01
    packet[1] = length_field & 0xFF
    packet[2] = (length_field >> 8) & 0xFF
    packet[3] = command & 0xFF
    for i, b in enumerate(data):
        packet[4 + i] = b
    checksum = 0
    for i in range(1, total - 2):
        checksum = (checksum + (packet[i] & 0xFF)) & 0xFFFF
    packet[-3] = checksum & 0xFF
    packet[-2] = (checksum >> 8) & 0xFF
    packet[-1] = 0x02
    return bytes(packet)


def gist_build_chunks(payload: bytes):
    chunks = []
    total_len = len(payload)
    n = (total_len + 255) // 256
    for i in range(n):
        inner = bytes([0x01]) + total_len.to_bytes(4, "little") + i.to_bytes(2, "little") \
            + payload[i * 256:(i + 1) * 256]
        chunks.append(gist_build_packet(0x8B, inner))
    return chunks


# real device replies from the public sources ([B], [A]) - also seen on our device
DEVICE_SEND_ALL = bytes.fromhex("01 07 00 04 8b 55 00 01 ec 00 02")
DEVICE_UPLOAD_DONE = bytes.fromhex("01 09 00 04 bd 55 13 01 05 00 38 01 02")


class FramingTests(unittest.TestCase):
    def test_matches_reference_builder(self):
        rnd = random.Random(1)
        for n in (0, 1, 5, 256, 263, 1000):
            body = bytes(rnd.randrange(256) for _ in range(n))
            self.assertEqual(P.build_frame(0x8B, body), gist_build_packet(0x8B, body))

    def test_brightness_frame(self):
        self.assertEqual(P.build_brightness_frame(50), gist_build_packet(0x74, bytes([50])))
        self.assertEqual(P.build_brightness_frame(150)[4], 100)
        self.assertEqual(P.build_brightness_frame(-3)[4], 0)

    def test_whitelist_blocks_everything_else(self):
        for cmd in range(256):
            if cmd in P.ALLOWED_COMMANDS:
                P.build_frame(cmd, b"\x00")
            else:
                with self.assertRaises(P.UnsafeCommandError):
                    P.build_frame(cmd, b"\x00")
        for cmd in P.KNOWN_DANGEROUS_COMMANDS:
            self.assertNotIn(cmd, P.ALLOWED_COMMANDS)

    def test_start_frame(self):
        f = P.build_start_frame(5200, b"\x00")
        self.assertEqual(f, gist_build_packet(0x8B, b"\x00" + (5200).to_bytes(4, "little") + b"\x00"))
        with self.assertRaises(P.ProtocolError):
            P.build_start_frame(0)

    def test_chunks_match_reference(self):
        rnd = random.Random(2)
        for size in (1, 255, 256, 257, 5000, 12345):
            payload = bytes(rnd.randrange(256) for _ in range(size))
            ours = P.build_chunk_frames(payload)
            self.assertEqual(ours, gist_build_chunks(payload))
            self.assertEqual(len(ours), P.chunk_count(size))
            # every chunk except the last carries exactly 256 bytes
            for fr in ours[:-1]:
                self.assertEqual(len(fr) - 7 - 7, 256)


class ParserTests(unittest.TestCase):
    def test_real_device_replies(self):
        p = P.FrameParser()
        frames = p.feed(DEVICE_SEND_ALL + DEVICE_UPLOAD_DONE)
        self.assertEqual(len(frames), 2)
        self.assertEqual(P.classify_reply(frames[0], 10), (P.Reply.SEND_ALL, -1))
        self.assertEqual(P.classify_reply(frames[1], 10), (P.Reply.UPLOAD_DONE, -1))

    def test_byte_by_byte_and_garbage(self):
        p = P.FrameParser()
        stream = b"\xff\x00\x13" + DEVICE_SEND_ALL + b"\x02\x02" + DEVICE_UPLOAD_DONE
        out = []
        for b in stream:
            out += p.feed(bytes([b]))
        self.assertEqual([f.raw for f in out], [DEVICE_SEND_ALL, DEVICE_UPLOAD_DONE])
        self.assertGreater(p.garbage_bytes, 0)

    def test_bad_checksum_resync(self):
        bad = bytearray(DEVICE_SEND_ALL)
        bad[-3] ^= 0xFF
        p = P.FrameParser()
        frames = p.feed(bytes(bad) + DEVICE_UPLOAD_DONE)
        self.assertEqual([f.raw for f in frames], [DEVICE_UPLOAD_DONE])
        self.assertGreaterEqual(p.bad_frames, 1)

    def test_resend_index_widths(self):
        le32 = P.encode_device_frame(0x04, bytes([0x8B, 0x55, 0x01]) + (7).to_bytes(4, "little"))
        le16 = P.encode_device_frame(0x04, bytes([0x8B, 0x55, 0x01]) + (7).to_bytes(2, "little")
                                     + b"\xAA\xBB")
        for raw in (le32, le16):
            f = P.FrameParser().feed(raw)[0]
            self.assertEqual(P.classify_reply(f, 20), (P.Reply.RESEND, 7))

    def test_other_replies(self):
        f = P.FrameParser().feed(P.encode_device_frame(0x04, bytes([0x74, 0x55])))[0]
        self.assertEqual(P.classify_reply(f)[0], P.Reply.OTHER)
        f = P.FrameParser().feed(P.encode_device_frame(0x46, b"\x01\x02"))[0]
        self.assertFalse(f.is_response)
        self.assertEqual(P.classify_reply(f)[0], P.Reply.OTHER)


class PayloadTests(unittest.TestCase):
    def test_jpeg_header_matches_gist(self):
        jpeg = b"\xff\xd8" + b"x" * 100 + b"\xff\xd9"
        payload = P.build_jpeg_payload([jpeg], 0)
        self.assertEqual(payload[:6], bytes([0x23, 0x01, 0x00, 0x00, 0x08, 0x0A]))
        self.assertEqual(payload[6], 0x01)
        self.assertEqual(int.from_bytes(payload[7:11], "big"), len(jpeg))
        parsed = P.parse_payload(payload)
        self.assertEqual(parsed.frames, [jpeg])

    def test_zstd_header(self):
        blob = b"\x28\xb5\x2f\xfd" + b"z" * 50
        payload = P.build_zstd_payload(blob, 1, 1000)
        self.assertEqual(payload[:6], bytes([0x25, 0x01, 0x03, 0xE8, 0x08, 0x08]))
        self.assertEqual(int.from_bytes(payload[6:10], "big"), len(blob))
        self.assertEqual(P.parse_payload(payload).frames, [blob])

    def test_multi_frame(self):
        frames = [bytes([i]) * (10 + i) for i in range(5)]
        payload = P.build_jpeg_payload(frames, 150)
        p = P.parse_payload(payload)
        self.assertEqual((p.frame_count, p.speed_ms), (5, 150))
        self.assertEqual(p.frames, frames)
        with self.assertRaises(P.ProtocolError):
            P.build_jpeg_payload([], 0)


if __name__ == "__main__":
    unittest.main()
