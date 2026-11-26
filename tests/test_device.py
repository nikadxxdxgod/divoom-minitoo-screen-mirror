"""MiniTooDevice against the virtual device (simulator.py)."""

import threading
import time
import unittest

from minitoo.device import DeviceState, MiniTooDevice, NotConnected, UploadError, UploadSettings
from minitoo.encoder import FrameEncoder
from minitoo.imaging import make_number_card, make_test_pattern
from minitoo.protocol import MAX_SAFE_PAYLOAD
from minitoo.simulator import FakeMiniToo, SimulatedTransport
from minitoo.transport import TransportError


def fast_settings(**kw):
    base = dict(chunk_interval_ms=0, request_timeout=0.5, ack_timeout=0.5, short_wait=0.05)
    base.update(kw)
    return UploadSettings(**base)


def payload(n="1"):
    return FrameEncoder("jpeg160", 80).encode([make_number_card(160, 128, n)])


class DeviceTests(unittest.TestCase):
    def make(self, fake=None, **kw):
        fake = fake or FakeMiniToo(bytes_per_sec=0, decode_ms=0)
        dev = MiniTooDevice(lambda: SimulatedTransport(fake), fast_settings(**kw.pop("s", {})), **kw)
        self.addCleanup(dev.disconnect)
        return dev, fake

    def test_upload_roundtrip(self):
        dev, fake = self.make()
        dev.connect()
        self.assertTrue(dev.is_connected)
        ef = FrameEncoder("jpeg160", 85).encode([make_test_pattern(160, 128)])
        res = dev.upload(ef.payload, ef.fmt.start_extra)
        self.assertTrue(res.ok and res.acked and res.go_ahead, res.summary())
        self.assertEqual(fake.last_payload, ef.payload)
        self.assertEqual(fake.last_image.size, (160, 128))
        self.assertEqual(fake.last_error, "")
        self.assertEqual(fake.log, [])
        self.assertTrue(dev.ack_supported)

    def test_resend_requests(self):
        fake = FakeMiniToo(bytes_per_sec=0, decode_ms=0, drop_once={0, 4, 9})
        dev, _ = self.make(fake)
        dev.connect()
        ef = payload()
        self.assertGreater(ef.chunks, 9)
        res = dev.upload(ef.payload, ef.fmt.start_extra)
        self.assertTrue(res.ok and res.acked)
        self.assertEqual(res.resends, 3)
        self.assertEqual(fake.last_payload, ef.payload)

    def test_no_go_ahead_and_no_ack_device(self):
        fake = FakeMiniToo(bytes_per_sec=0, decode_ms=0, send_go_ahead=False, send_ack=False)
        dev, _ = self.make(fake)
        dev.connect()
        for i in range(4):
            res = dev.upload(payload(str(i)).payload, b"\x00")
            self.assertTrue(res.ok, res.summary())
            self.assertFalse(res.acked)
        self.assertIs(dev.ack_supported, False)
        self.assertIs(dev.go_ahead_supported, False)
        self.assertEqual(fake.uploads, 4)
        t0 = time.perf_counter()
        dev.upload(payload("x").payload, b"\x00")
        self.assertLess(time.perf_counter() - t0, 0.4)    # the wait got shorter

    def test_auto_pacing(self):
        dev, _ = self.make(s=dict(chunk_interval_ms=10, min_interval_ms=6, auto_pacing=True))
        dev.connect()
        for i in range(8):
            dev.upload(payload(str(i)).payload, b"\x00")
        self.assertLess(dev.current_interval_ms, 10)
        self.assertGreaterEqual(dev.current_interval_ms, 6)

    def test_pacing_backs_off_on_resend(self):
        fake = FakeMiniToo(bytes_per_sec=0, decode_ms=0)
        dev, _ = self.make(fake, s=dict(chunk_interval_ms=8))
        dev.connect()
        dev.upload(payload().payload, b"\x00")
        fake.drop_once = {2}
        before = dev.current_interval_ms
        res = dev.upload(payload("2").payload, b"\x00")
        self.assertEqual(res.resends, 1)
        self.assertGreater(dev.current_interval_ms, before)

    def test_start_retry_when_device_silent(self):
        fake = FakeMiniToo(bytes_per_sec=0, decode_ms=0)
        dev, _ = self.make(fake)
        dev.connect()
        self.assertTrue(dev.upload(payload("1").payload, b"\x00").ok)   # go-ahead is known now
        fake.ignore_starts = 1                       # ignores the first start
        res = dev.upload(payload("2").payload, b"\x00")
        self.assertTrue(res.ok and res.acked, res.summary())
        self.assertEqual(fake.uploads, 2)
        fake.ignore_starts = 2                       # ignores both -> no data is sent
        tx_before = dev.stats.tx_bytes
        res = dev.upload(payload("3").payload, b"\x00")
        self.assertFalse(res.ok)
        self.assertIn("start", res.error)
        self.assertLess(dev.stats.tx_bytes - tx_before, 64)       # only two start packets
        self.assertEqual(fake.uploads, 2)

    def test_payload_limit(self):
        dev, _ = self.make()
        dev.connect()
        with self.assertRaises(UploadError):
            dev.upload(b"\x00" * (MAX_SAFE_PAYLOAD + 1))

    def test_not_connected(self):
        dev, _ = self.make()
        with self.assertRaises(NotConnected):
            dev.upload(b"\x23" * 10)

    def test_brightness(self):
        dev, fake = self.make()
        dev.connect()
        dev.set_brightness(42)
        time.sleep(0.05)
        self.assertEqual(fake.brightness, 42)

    def test_connect_failure(self):
        fake = FakeMiniToo(bytes_per_sec=0, fail_connects=1)
        dev, _ = self.make(fake)
        with self.assertRaises(TransportError):
            dev.connect()
        self.assertEqual(dev.state, DeviceState.DISCONNECTED)
        dev.connect()
        self.assertTrue(dev.is_connected)

    def test_auto_reconnect(self):
        fake = FakeMiniToo(bytes_per_sec=0, decode_ms=0, disconnect_after_uploads=2)
        states = []
        dev, _ = self.make(fake, auto_reconnect=True,
                           on_state=lambda st, msg: states.append(st))
        dev.connect()
        dev.upload(payload("1").payload, b"\x00")
        dev.upload(payload("2").payload, b"\x00")
        # the link "drops" after the 2nd upload -> reconnect (~1 s)
        deadline = time.time() + 5
        while DeviceState.RECONNECTING not in states and time.time() < deadline:
            time.sleep(0.02)
        self.assertIn(DeviceState.RECONNECTING, states)
        self.assertTrue(dev.wait_connected(5))
        res = dev.upload(payload("3").payload, b"\x00")
        self.assertTrue(res.ok, res.summary())
        self.assertGreaterEqual(fake.connects, 2)
        self.assertEqual(dev.stats.reconnects, 1)

    def test_disconnect_stops_reconnect(self):
        fake = FakeMiniToo(bytes_per_sec=0, decode_ms=0, disconnect_after_uploads=1)
        dev, _ = self.make(fake, auto_reconnect=True)
        dev.connect()
        dev.upload(payload().payload, b"\x00")
        time.sleep(0.4)
        dev.disconnect()
        self.assertEqual(dev.state, DeviceState.DISCONNECTED)
        time.sleep(1.5)
        self.assertEqual(dev.state, DeviceState.DISCONNECTED)

    def test_concurrent_uploads_serialized(self):
        dev, fake = self.make()
        dev.connect()
        results = []

        def worker(n):
            results.append(dev.upload(payload(str(n)).payload, b"\x00"))

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        self.assertEqual(len(results), 4)
        self.assertTrue(all(r.ok and r.acked for r in results))
        self.assertEqual(fake.uploads, 4)
        self.assertEqual(fake.log, [])


if __name__ == "__main__":
    unittest.main()
