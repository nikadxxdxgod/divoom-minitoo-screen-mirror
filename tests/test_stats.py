"""System stats (sensors, parsing, the dashboard) and the display helpers."""

import ctypes
import sys
import time
import unittest

from minitoo import displays, stats
from minitoo.capture import CaptureSource, SourceUnavailable
from minitoo.device import MiniTooDevice, UploadSettings
from minitoo.mirror import MirrorConfig, MirrorEngine
from minitoo.simulator import FakeMiniToo, SimulatedTransport


def wait_until(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class FakeSampler:
    def __init__(self):
        self.n = 0
        self.closed = False

    def sample(self):
        self.n += 1
        return stats.Reading(t=time.time(), cpu=10.0 * (self.n % 10), cpu_temp=55.0, gpu=12.0,
                             ram_used=4 * 1024 ** 3, ram_total=16 * 1024 ** 3,
                             net_down=1.5 * 1024 ** 2, net_up=40 * 1024, sources=("fake",))

    def close(self):
        self.closed = True


class ParsingTests(unittest.TestCase):
    def test_gpu_engines_like_task_manager(self):
        items = [("pid_10_luid_0x0_0xC9F1_phys_0_eng_0_engtype_3D", 20.0),
                 ("pid_22_luid_0x0_0xC9F1_phys_0_eng_0_engtype_3D", 15.0),
                 ("pid_10_luid_0x0_0xC9F1_phys_0_eng_1_engtype_VideoDecode", 30.0)]
        self.assertAlmostEqual(stats.gpu_load_from_engines(items), 35.0)
        self.assertEqual(stats.gpu_load_from_engines([]), 0.0)
        self.assertEqual(stats.gpu_load_from_engines([("pid_1_x", 80.0), ("pid_2_x", 70.0)]), 100.0)

    def test_thermal_zone_kelvin(self):
        self.assertAlmostEqual(stats.thermal_zone_celsius([("\\_TZ.THM0", 354.0)]), 80.85)
        self.assertIsNone(stats.thermal_zone_celsius([("\\_TZ.X", 0.0)]))
        self.assertAlmostEqual(stats.thermal_zone_celsius([("a", 320.0), ("b", 330.0)]), 56.85)

    def test_network_skips_tunnels(self):
        items = [("Intel[R] Dual Band Wireless-AC 8265", 1000.0), ("isatap.{1234}", 500.0),
                 ("Teredo Tunneling Pseudo-Interface", 7.0)]
        self.assertEqual(stats.net_total(items), 1000.0)

    def test_nvidia_smi(self):
        self.assertEqual(stats.parse_nvidia_smi("45, 12\n"), (45.0, 12.0))
        self.assertIsNone(stats.parse_nvidia_smi("[N/A], [N/A]"))
        self.assertIsNone(stats.parse_nvidia_smi(""))

    def test_lhm_with_sensor_ids(self):
        data = {"Text": "Sensor", "Children": [{"Text": "PC", "Children": [
            {"Text": "AMD Ryzen 7 5800X", "ImageURL": "images_icon/cpu.png", "Children": [
                {"Text": "Temperatures", "Children": [
                    {"Text": "Core (Tctl/Tdie)", "Value": "61,5 °C", "SensorId": "/amdcpu/0/temperature/2"},
                    {"Text": "CCD1 (Tdie)", "Value": "58.0 °C", "SensorId": "/amdcpu/0/temperature/3"}]}]},
            {"Text": "NVIDIA GeForce RTX 3060", "ImageURL": "images_icon/nvidia.png", "Children": [
                {"Text": "Temperatures", "Children": [
                    {"Text": "GPU Core", "Value": "47.0 °C", "SensorId": "/gpu-nvidia/0/temperature/0"},
                    {"Text": "GPU Hot Spot", "Value": "57.0 °C", "SensorId": "/gpu-nvidia/0/temperature/2"}]},
                {"Text": "Load", "Children": [
                    {"Text": "GPU Core", "Value": "23.0 %", "SensorId": "/gpu-nvidia/0/load/0"}]}]}]}]}
        self.assertEqual(stats.parse_lhm(data), {"cpu_temp": 61.5, "gpu_temp": 47.0, "gpu": 23.0})

    def test_lhm_without_sensor_ids(self):
        data = {"Text": "Sensor", "Children": [
            {"Text": "Intel Core i5-7300U", "ImageURL": "images_icon/cpu.png", "Children": [
                {"Text": "Temperatures", "ImageURL": "images_icon/temperature.png", "Children": [
                    {"Text": "CPU Core #1", "Value": "70.0 °C", "Children": []},
                    {"Text": "CPU Package", "Value": "73.0 °C", "Children": []}]}]}]}
        self.assertEqual(stats.parse_lhm(data), {"cpu_temp": 73.0})
        self.assertEqual(stats.parse_lhm({}), {})
        self.assertEqual(stats.parse_lhm([]), {})

    def test_rates(self):
        self.assertEqual(stats.format_rate(None), ("—", ""))
        self.assertEqual(stats.format_rate(500), ("500", "B/s"))
        self.assertEqual(stats.format_rate(1.5 * 1024 ** 2), ("1.5", "MB/s"))
        self.assertEqual(stats.format_rate(35 * 1024), ("35", "KB/s"))


class DashboardTests(unittest.TestCase):
    def test_render_full_and_empty(self):
        img = stats.render_dashboard(FakeSampler().sample(), {"cpu": [10, 50, 90], "down": [1, 2e6]})
        self.assertEqual(img.size, (160, 128))
        self.assertEqual(img.mode, "RGB")
        empty = stats.render_dashboard(None)           # no sensors at all: dashes, no crash
        self.assertEqual(empty.size, (160, 128))
        self.assertNotEqual(img.tobytes(), empty.tobytes())

    def test_monitor_and_grabber(self):
        fake = FakeSampler()
        mon = stats.StatsMonitor(interval=0.05, sampler_factory=lambda: fake)
        grabber = stats.StatsGrabber(mon)
        with self.assertRaises(SourceUnavailable):
            grabber.grab()
        mon.start()
        self.addCleanup(mon.stop, True)
        self.assertTrue(wait_until(lambda: mon.image() is not None and len(mon.history()["cpu"]) >= 3))
        img, cursor, rect = grabber.grab()
        self.assertEqual(img.size, (160, 128))
        self.assertIsNone(cursor)
        self.assertEqual(mon.latest().sources, ("fake",))
        mon.stop(wait=True)
        self.assertFalse(mon.running)
        self.assertTrue(fake.closed)

    def test_mirrors_to_the_simulator(self):
        device = FakeMiniToo(bytes_per_sec=0, decode_ms=0)
        dev = MiniTooDevice(lambda: SimulatedTransport(device),
                            UploadSettings(chunk_interval_ms=0, request_timeout=0.5, ack_timeout=0.5))
        dev.connect()
        self.addCleanup(dev.disconnect)
        mon = stats.StatsMonitor(interval=0.1, sampler_factory=FakeSampler)
        mon.start()
        self.addCleanup(mon.stop, True)
        eng = MirrorEngine(dev, MirrorConfig(source=CaptureSource("monitor", 1), target_fps=10),
                           grabber_factory=lambda: stats.StatsGrabber(mon))
        self.addCleanup(eng.stop)
        eng.start()
        self.assertTrue(wait_until(lambda: device.uploads >= 2))

    @unittest.skipUnless(sys.platform == "win32", "Windows sensors")
    def test_real_sensors_do_not_crash(self):
        s = stats.SystemSampler(lhm_url="", use_nvidia=False)
        self.addCleanup(s.close)
        time.sleep(0.3)
        r = s.sample()
        self.assertIn("PDH", r.sources)
        self.assertIsNotNone(r.ram)
        self.assertTrue(0 <= r.ram <= 100)
        if r.cpu is not None:
            self.assertTrue(0 <= r.cpu <= 100)


class DisplayTests(unittest.TestCase):
    def test_struct_sizes(self):
        self.assertEqual(ctypes.sizeof(displays.DEVMODEW), 220)
        self.assertEqual(ctypes.sizeof(displays.DISPLAY_DEVICEW), 840)

    def test_virtual_names(self):
        for name in ("Virtual Display Driver", "IddSampleDriver Device", "Parsec Virtual Display Adapter",
                     "USB Mobile Monitor Virtual Display", "spacedesk Graphics Adapter"):
            self.assertTrue(displays.looks_virtual(name), name)
        for name in ("Intel(R) HD Graphics 520", "NVIDIA GeForce RTX 3060", "Generic PnP Monitor",
                     "Wide viewing angle & High density FlexView Display 1920x1080", "Hidden Monitor", ""):
            self.assertFalse(displays.looks_virtual(name), name)

    def test_minitoo_modes(self):
        self.assertTrue(displays.is_minitoo_shape(800, 640))
        self.assertTrue(displays.is_minitoo_shape(1280, 1024))
        self.assertFalse(displays.is_minitoo_shape(1920, 1080))
        self.assertFalse(displays.is_minitoo_shape(0, 0))
        modes = [(1920, 1080, 60), (1280, 1024, 60), (640, 512, 60), (800, 640, 60)]
        self.assertEqual(displays.best_minitoo_mode(modes), (800, 640))
        self.assertEqual(displays.best_minitoo_mode(modes[:2]), (1280, 1024))
        self.assertEqual(displays.best_minitoo_mode([(1500, 1200, 60)]), (1500, 1200))
        self.assertIsNone(displays.best_minitoo_mode([(1920, 1080, 60)]))

    @unittest.skipUnless(sys.platform == "win32", "Windows displays")
    def test_lists_real_displays(self):
        devs = displays.display_devices()
        attached = [d for d in devs.values() if d.attached]
        self.assertTrue(attached)
        mode = displays.current_mode(attached[0].device)
        self.assertIsNotNone(mode)
        self.assertIn(mode[:2], [m[:2] for m in displays.list_modes(attached[0].device)])


if __name__ == "__main__":
    unittest.main()
