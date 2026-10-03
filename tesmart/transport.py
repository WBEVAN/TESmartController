"""Byte transports for talking to a TESmart switch.

Two concrete transports are provided:

* :class:`TcpTransport` – the switch's LAN port (default ``192.168.1.10:5000``).
* :class:`SerialTransport` – the 3-pin RS232 port at 9600 8N1 (needs ``pyserial``).

Both expose the same tiny interface so :class:`tesmart.switch.TesmartSwitch`
does not care which one it is given.
"""

from __future__ import annotations

import socket
import time
from abc import ABC, abstractmethod

DEFAULT_TCP_PORT = 5000
DEFAULT_BAUD_RATE = 9600


class TransportError(Exception):
    """Raised when the underlying link cannot be opened or fails mid-transfer."""


class Transport(ABC):
    """Minimal duplex byte stream."""

    @abstractmethod
    def write(self, data: bytes) -> None: ...

    @abstractmethod
    def read(self, timeout: float) -> bytes:
        """Return whatever bytes arrive within ``timeout`` seconds (may be empty)."""

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def describe(self) -> str:
        """Human-readable description of the endpoint, for log/CLI output."""

    def __enter__(self) -> "Transport":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class TcpTransport(Transport):
    def __init__(self, host: str, port: int = DEFAULT_TCP_PORT, connect_timeout: float = 3.0):
        self.host = host
        self.port = port
        try:
            self._sock = socket.create_connection((host, port), timeout=connect_timeout)
        except OSError as exc:
            raise TransportError(f"cannot connect to {host}:{port}: {exc}") from exc
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def write(self, data: bytes) -> None:
        try:
            self._sock.sendall(data)
        except OSError as exc:
            raise TransportError(f"write failed: {exc}") from exc

    def read(self, timeout: float) -> bytes:
        self._sock.settimeout(timeout)
        try:
            chunk = self._sock.recv(256)
        except socket.timeout:
            return b""
        except OSError as exc:
            raise TransportError(f"read failed: {exc}") from exc
        if not chunk:
            raise TransportError("connection closed by switch")
        return chunk

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def describe(self) -> str:
        return f"tcp://{self.host}:{self.port}"


class SerialTransport(Transport):
    def __init__(self, device: str, baud_rate: int = DEFAULT_BAUD_RATE):
        try:
            import serial  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise TransportError(
                "pyserial is required for RS232 control: pip install 'tesmartctl[serial]'"
            ) from exc
        self.device = device
        self.baud_rate = baud_rate
        try:
            self._port = serial.Serial(
                device,
                baudrate=baud_rate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0,
            )
        except (OSError, serial.SerialException) as exc:
            raise TransportError(f"cannot open serial port {device}: {exc}") from exc

    def write(self, data: bytes) -> None:
        self._port.write(data)
        self._port.flush()

    def read(self, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            waiting = self._port.in_waiting
            if waiting:
                return self._port.read(waiting)
            if time.monotonic() >= deadline:
                return b""
            time.sleep(0.02)

    def close(self) -> None:
        self._port.close()

    def describe(self) -> str:
        return f"serial://{self.device}@{self.baud_rate}"
