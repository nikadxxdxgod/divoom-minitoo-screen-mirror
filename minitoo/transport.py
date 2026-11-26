"""Byte transport: Bluetooth RFCOMM socket or a virtual COM port.

Both give device.py the same interface:
    open() · close() · send(bytes) · recv(timeout) -> bytes | None
When the link drops, both raise ConnectionLost.
"""

from __future__ import annotations

import select
import socket
import threading
import time
from typing import Optional

from .i18n import N_, _


class TransportError(Exception):
    """The connection could not be opened."""


class ConnectionLost(TransportError):
    """An open connection was lost."""


# Common Windows (WinSock) errors -> hint for the user (translated at use time)
WINSOCK_HINTS = {
    10013: N_("Access denied — Windows blocked the connection."),
    10048: N_("Address in use — another program is already connected to the device."),
    10049: N_("Invalid Bluetooth address."),
    10050: N_("The Bluetooth adapter is turned off."),
    10051: N_("Device unreachable — pair it in Windows Settings, make sure it is on and not "
              "connected to a phone."),
    10053: N_("The connection was aborted by this PC."),
    10054: N_("The connection was reset by the device."),
    10060: N_("The device did not answer in time — it may be off, out of range or connected "
              "to another device."),
    10061: N_("The device refused the connection — wrong channel, or it is busy with another "
              "device."),
    10064: N_("The device is off or unreachable."),
    10065: N_("No route to the device — pair it in Windows Settings first."),
}


def explain_os_error(err: BaseException) -> str:
    code = getattr(err, "winerror", None) or getattr(err, "errno", None)
    hint = WINSOCK_HINTS.get(code or 0)
    base = f"{type(err).__name__}: {err}"
    return f"{base} — {_(hint)}" if hint else base


class Transport:
    name = "transport"

    def open(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def send(self, data: bytes) -> None:
        raise NotImplementedError

    def recv(self, timeout: float) -> Optional[bytes]:
        raise NotImplementedError

    @property
    def is_open(self) -> bool:
        raise NotImplementedError

    def describe(self) -> str:
        return self.name


class RfcommTransport(Transport):
    """Python's built-in Windows Bluetooth socket (AF_BLUETOOTH / RFCOMM).
    No extra driver or package; the device must be paired in Windows."""

    name = "rfcomm"

    def __init__(self, address: str, channel: int = 1, connect_timeout: float = 20.0,
                 write_timeout: float = 8.0) -> None:
        self.address = address
        self.channel = int(channel)
        self.connect_timeout = connect_timeout
        self.write_timeout = write_timeout
        self._sock: Optional[socket.socket] = None
        self._send_lock = threading.Lock()

    def describe(self) -> str:
        return f"Bluetooth RFCOMM {self.address} · {_('channel')} {self.channel}"

    def open(self) -> None:
        if not all(hasattr(socket, n) for n in ("AF_BLUETOOTH", "BTPROTO_RFCOMM")):
            raise TransportError(_("This Python has no Bluetooth socket support — "
                                   "use the COM port connection."))
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,  # type: ignore[attr-defined]
                             socket.BTPROTO_RFCOMM)  # type: ignore[attr-defined]
        # On Windows connect() with settimeout() often hangs for the whole
        # timeout, so connect in blocking mode in a helper thread and enforce
        # the timeout ourselves (closing the socket aborts connect()).
        result: dict = {}

        def worker() -> None:
            try:
                sock.connect((self.address, self.channel))
                result["ok"] = True
            except OSError as e:
                result["err"] = e

        t = threading.Thread(target=worker, name="rfcomm-connect", daemon=True)
        t.start()
        t.join(self.connect_timeout)
        if t.is_alive():
            try:
                sock.close()
            finally:
                t.join(2.0)
            raise TransportError(_("Connection timed out after {s} s.", s=int(self.connect_timeout))
                                 + " " + _(WINSOCK_HINTS[10060]))
        if "err" in result:
            sock.close()
            raise TransportError(explain_os_error(result["err"])) from result["err"]
        sock.settimeout(self.write_timeout)
        self._sock = sock

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    def send(self, data: bytes) -> None:
        sock = self._sock
        if sock is None:
            raise ConnectionLost("connection closed")
        try:
            with self._send_lock:
                sock.sendall(data)
        except OSError as e:
            raise ConnectionLost(explain_os_error(e)) from e

    def recv(self, timeout: float) -> Optional[bytes]:
        sock = self._sock
        if sock is None:
            raise ConnectionLost("connection closed")
        try:
            ready, _w, _x = select.select([sock], [], [], max(0.0, timeout))
            if not ready:
                return None
            data = sock.recv(4096)
        except socket.timeout:
            return None
        except (OSError, ValueError) as e:
            if self._sock is None:
                raise ConnectionLost("connection closed") from e
            raise ConnectionLost(explain_os_error(e)) from e
        if not data:
            raise ConnectionLost(_("The device closed the connection."))
        return data


class SerialTransport(Transport):
    """Windows virtual COM port ("Standard Serial over Bluetooth link") that
    Windows creates when pairing. A fallback if the socket does not work."""

    name = "serial"

    def __init__(self, port: str, baudrate: int = 115200, write_timeout: float = 8.0) -> None:
        self.port = port
        self.baudrate = baudrate
        self.write_timeout = write_timeout
        self._ser = None
        self._send_lock = threading.Lock()

    def describe(self) -> str:
        return f"{_('COM port')} {self.port}"

    def open(self) -> None:
        try:
            import serial  # pyserial
        except ImportError as e:
            raise TransportError("pyserial is not installed (pip install pyserial)") from e
        try:
            self._ser = serial.Serial(self.port, self.baudrate, timeout=0.2,
                                      write_timeout=self.write_timeout)
        except (serial.SerialException, OSError, ValueError) as e:
            raise TransportError(_("{port} could not be opened: {err}", port=self.port, err=e)) from e

    def close(self) -> None:
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    @property
    def is_open(self) -> bool:
        return self._ser is not None

    def send(self, data: bytes) -> None:
        ser = self._ser
        if ser is None:
            raise ConnectionLost("port closed")
        try:
            with self._send_lock:
                ser.write(data)
                ser.flush()
        except Exception as e:
            raise ConnectionLost(f"{self.port}: {e}") from e

    def recv(self, timeout: float) -> Optional[bytes]:
        ser = self._ser
        if ser is None:
            raise ConnectionLost("port closed")
        try:
            ser.timeout = max(0.01, timeout)
            first = ser.read(1)
            if not first:
                return None
            waiting = ser.in_waiting
            return first + (ser.read(waiting) if waiting else b"")
        except Exception as e:
            if self._ser is None:
                raise ConnectionLost("port closed") from e
            raise ConnectionLost(f"{self.port}: {e}") from e


def precise_sleep(seconds: float) -> None:
    """Short pause between chunks. On Python 3.11+ time.sleep already uses a
    high-resolution timer on Windows; below 1 ms we just yield."""
    if seconds <= 0:
        return
    if seconds < 0.001:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            time.sleep(0)
        return
    time.sleep(seconds)
