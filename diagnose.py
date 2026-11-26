"""MiniToo Mirror — Bluetooth diagnostic (console).

Console text is English because the default Windows console font has no
Georgian glyphs. The same checks exist in Georgian inside the app
(Tools -> Diagnostics).

Run:  diagnose.bat            (interactive, asks before every step that talks to the device)
      python diagnose.py --mac AA:BB:CC:DD:EE:FF --yes

Safety: the only commands ever sent are 0x8B (image upload) and, if you ask
for it, 0x74 (brightness). Nothing touches the firmware.
Everything is written to diagnostic_report.txt next to this script.
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import platform
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(getattr(sys, "frozen", False) and sys.executable or __file__))
REPORT = os.path.join(HERE, "diagnostic_report.txt")

WINSOCK_EN = {
    10013: "access denied by Windows",
    10048: "address in use - another program is already connected to the device",
    10049: "invalid Bluetooth address",
    10050: "Bluetooth radio is off",
    10051: "device unreachable - pair it in Windows first; make sure it is on and NOT connected to the phone",
    10053: "connection aborted by this PC",
    10054: "connection reset by the device",
    10060: "timed out - device off, out of range or busy with another host",
    10061: "connection refused - wrong RFCOMM channel or device busy",
    10064: "host is down",
    10065: "no route to host - pair the device in Windows first",
}


class Report:
    def __init__(self) -> None:
        self.buf = io.StringIO()

    def __call__(self, text: str = "") -> None:
        print(text, flush=True)
        self.buf.write(text + "\n")

    def raw(self, text: str) -> None:            # file only (may contain Georgian)
        self.buf.write(text + "\n")

    def save(self) -> None:
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write(self.buf.getvalue())


R = Report()


def ask(question: str, default: bool, auto: bool) -> bool:
    if auto:
        R(f"{question} -> {'yes' if default else 'no'} (--yes)")
        return default
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        ans = input(question + suffix).strip().lower()
    except EOFError:
        ans = ""
    result = default if not ans else ans.startswith("y")
    R.raw(f"{question} -> {'yes' if result else 'no'}")
    return result


def observe(question: str, auto: bool) -> None:
    if auto:
        return
    try:
        ans = input(question + " (type an answer, Enter to skip): ").strip()
    except EOFError:
        ans = ""
    R.raw(f"USER: {question} -> {ans or '(skipped)'}")


def explain(e: BaseException) -> str:
    cause = e.__cause__ if isinstance(e.__cause__, OSError) else e
    code = getattr(cause, "winerror", None) or getattr(cause, "errno", None)
    hint = WINSOCK_EN.get(code or 0, "")
    return f"{type(cause).__name__} [{code}] {hint}".strip() if code else f"{type(e).__name__}"


def section(title: str) -> None:
    R()
    R(f"=== {title} ===")


def main() -> int:
    ap = argparse.ArgumentParser(description="MiniToo Bluetooth diagnostic")
    ap.add_argument("--mac", help="device address, e.g. AA:BB:CC:DD:EE:FF")
    ap.add_argument("--channel", type=int, default=1, help="RFCOMM channel (default 1)")
    ap.add_argument("--com", help="use this COM port instead of the RFCOMM socket")
    ap.add_argument("--yes", action="store_true", help="non-interactive: connect + test image + 10 frames")
    ap.add_argument("--no-scan", action="store_true", help="skip the ~7 s inquiry scan")
    ap.add_argument("--sweep", action="store_true", help="also run the chunk-interval sweep")
    ap.add_argument("--simulator", action="store_true", help="virtual device (tests the script itself)")
    args = ap.parse_args()
    auto = args.yes

    sys.path.insert(0, HERE)
    from minitoo import __version__
    from minitoo.capture import ensure_dpi_awareness
    ensure_dpi_awareness()

    # all library log lines (packet dumps, Georgian text) go to the report file only
    class FileOnly(logging.Handler):
        def emit(self, record):
            R.raw(f"    log {record.levelname}: {record.getMessage()}")

    lg = logging.getLogger("minitoo")
    lg.addHandler(FileOnly())
    lg.setLevel(logging.DEBUG)

    R(f"MiniToo Mirror {__version__} - Bluetooth diagnostic - {time.strftime('%Y-%m-%d %H:%M:%S')}")
    R(f"Report file: {REPORT}")

    section("1. Environment")
    R(f"Windows : {platform.platform()} ({platform.version()})")
    R(f"Python  : {sys.version.split()[0]} {platform.architecture()[0]}")
    try:
        import PIL
        R(f"Pillow  : {PIL.__version__}")
    except ImportError:
        R("Pillow  : MISSING (run install.bat)")
        return 1
    from minitoo.encoder import ZSTD_BACKEND
    R(f"zstd    : {ZSTD_BACKEND or 'missing (lossless 128x128 mode unavailable)'}")

    section("2. Bluetooth adapter")
    from minitoo.bt_windows import (bluetooth_socket_supported, find_devices,
                                    list_bluetooth_com_ports, list_radios, normalize_mac)
    try:
        radios = list_radios()
    except OSError as e:
        radios = []
        R(f"ERROR reading adapters: {e}")
    for r in radios:
        R(f"OK  {r.name or '(no name)'} [{r.address}] manufacturer #{r.manufacturer} lmp {r.lmp_subversion}")
    if not radios:
        R("FAIL no Bluetooth adapter found or it is turned off.")
    R(("OK  " if bluetooth_socket_supported() else "FAIL ") + "Python RFCOMM socket support")

    section("3. Devices")
    inquiry = not args.no_scan and ask("Scan for nearby Bluetooth devices (~7 s)?", True, auto)
    try:
        devs = find_devices(inquiry=inquiry, timeout_multiplier=5)
    except OSError as e:
        devs = []
        R(f"ERROR: {e}")
    for d in devs:
        R(f"{'*' if d.is_divoom else ' '} {d.name or '(no name)':32s} [{d.address}] paired={d.paired} "
          f"connected={d.connected} remembered={d.remembered} class=0x{d.device_class:06X}")
    if not devs:
        R("  (none)")

    section("4. Bluetooth COM ports")
    ports = list_bluetooth_com_ports()
    for p in ports:
        R(f"  {p.device}: {p.description}  mac={p.mac}  hwid={p.hwid}")
    if not ports:
        R("  (none) - normal if the device is not paired or offers no outgoing serial port")

    # target
    mac = normalize_mac(args.mac or "")
    if args.simulator:
        mac = "00:00:00:00:00:00"
    if not mac and not args.com:
        cand = next((d for d in devs if d.is_divoom), None)
        if cand:
            mac = cand.address
            R()
            R(f"Target: {cand.name} [{mac}] (paired={cand.paired})")
            if not cand.paired:
                R("NOTE: the device is NOT paired. Pair it first: Windows Settings > Bluetooth > "
                  "Add device > 'Divoom MiniToo-Audio'. A connection attempt will most likely fail.")
    if not mac and not args.com:
        R()
        R("No MiniToo found. Turn it on, disconnect it from the phone (close the Divoom app or turn "
          "off phone Bluetooth), then run again or pass --mac.")
        R.save()
        return 2

    from minitoo.device import MiniTooDevice, UploadSettings
    from minitoo.transport import RfcommTransport, SerialTransport, TransportError

    if args.simulator:
        from minitoo.simulator import FakeMiniToo, SimulatedTransport
        fake = FakeMiniToo()
        factory = lambda: SimulatedTransport(fake)  # noqa: E731
        target = "SIMULATOR (virtual device)"
    elif args.com:
        factory = lambda: SerialTransport(args.com)  # noqa: E731
        target = args.com
    else:
        factory = lambda: RfcommTransport(mac, args.channel, connect_timeout=20)  # noqa: E731
        target = f"{mac} channel {args.channel}"

    section("5. Connection")
    if not ask(f"Try to connect to {target}? (opens a connection, sends nothing)", True, auto):
        R.save()
        return 0
    dev = MiniTooDevice(factory, UploadSettings(), auto_reconnect=False)
    t0 = time.perf_counter()
    try:
        dev.connect()
    except (TransportError, OSError) as e:
        R(f"FAIL after {time.perf_counter() - t0:.1f} s: {explain(e)}")
        R.raw(f"    detail: {e}")
        R("Hints: pair in Windows first; close the Divoom phone app / turn off phone Bluetooth; "
          "power-cycle the speaker; try --channel 2..5 only if channel 1 is refused.")
        R.save()
        return 3
    R(f"OK  connected in {(time.perf_counter() - t0) * 1000:.0f} ms")
    time.sleep(2.0)
    st = dev.stats
    R(f"Unsolicited data in 2 s: {st.rx_bytes} bytes, {st.rx_frames} frames")

    try:
        from minitoo.encoder import FrameEncoder
        from minitoo.imaging import CARD_COLORS, make_number_card, make_test_pattern

        section("6. Test image (sends command 0x8B only)")
        if ask("Send one test image (color bars + corner markers)?", True, auto):
            enc = FrameEncoder("jpeg160", 85)
            ef = enc.encode([make_test_pattern(160, 128, "MiniToo")])
            res = dev.upload(ef.payload, ef.fmt.start_extra)
            R(f"payload {len(ef.payload)} B, {ef.chunks} chunks")
            R(f"go-ahead reply: {res.go_ahead}   final ACK: {res.acked}   resends: {res.resends}")
            R(f"timing: wait {res.start_wait_ms:.0f} ms + stream {res.stream_ms:.0f} ms + "
              f"ack {res.ack_wait_ms:.0f} ms = {res.total_ms:.0f} ms ({res.kbps:.1f} KB/s)")
            if res.error:
                R(f"error: {res.error}")
            R("Expected on the speaker: color bars at the bottom, RED top-left, GREEN top-right, "
              "BLUE bottom-left, YELLOW bottom-right corner.")
            observe("Did the image appear? Correct colors/orientation? Is it cut off?", auto)

        section("7. Repeated updates (10 frames)")
        if ask("Send numbers 1..10 back to back?", True, auto):
            enc = FrameEncoder("jpeg160", 70)
            totals, acks = [], 0
            t_all = time.perf_counter()
            for i in range(1, 11):
                ef = enc.encode([make_number_card(160, 128, str(i), "",
                                                  CARD_COLORS[i % len(CARD_COLORS)])])
                res = dev.upload(ef.payload, ef.fmt.start_extra)
                totals.append(res.total_ms)
                acks += res.acked
                R(f"  {i:2d}: {len(ef.payload):5d} B  {res.total_ms:6.0f} ms  ack={res.acked} "
                  f"resends={res.resends} interval={res.interval_ms:.1f} ms {res.error}")
                if not dev.is_connected:
                    R("  connection lost")
                    break
            span = time.perf_counter() - t_all
            R(f"avg {statistics.mean(totals):.0f} ms/frame, min {min(totals):.0f}, max {max(totals):.0f}, "
              f"{len(totals) / span:.2f} frames/s, acked {acks}/{len(totals)}")
            observe("Did all numbers 1-10 appear? Roughly how many seconds behind was the screen?", auto)

        if args.sweep or (not auto and ask("Run chunk-interval sweep (20/12/8/5 ms, 3 frames each)?",
                                           False, auto)):
            section("8. Chunk interval sweep")
            for interval in (20.0, 12.0, 8.0, 5.0):
                dev.settings.auto_pacing = False
                dev.settings.chunk_interval_ms = interval
                dev.reset_pacing()
                enc = FrameEncoder("jpeg160", 70)
                ok = 0
                times = []
                for i in range(3):
                    ef = enc.encode([make_number_card(160, 128, f"{interval:g}", f"{i + 1}/3")])
                    res = dev.upload(ef.payload, ef.fmt.start_extra)
                    times.append(res.total_ms)
                    ok += res.ok and res.acked and not res.resends
                R(f"  {interval:4.1f} ms: clean {ok}/3, avg {statistics.mean(times):.0f} ms")
                if ok < 3:
                    R("  stopping sweep (not clean at this interval)")
                    break
    finally:
        section("Summary")
        st = dev.stats
        R(f"go-ahead seen: {dev.go_ahead_supported}  ack seen: {dev.ack_supported}")
        R(f"tx {st.tx_bytes} B, rx {st.rx_bytes} B ({st.rx_frames} frames), uploads ok {st.uploads_ok}, "
          f"failed {st.uploads_failed}, resends {st.resends}")
        for h in st.other_frames[-10:]:
            R(f"  other reply: {h}")
        dev.disconnect()
        R.save()
        R()
        R(f"Saved: {REPORT}")
        R("Please send this file (or its contents) back.")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        R("Interrupted.")
        R.save()
        code = 130
    if not any(a == "--yes" for a in sys.argv[1:]) and sys.stdin and sys.stdin.isatty():
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass
    sys.exit(code)
