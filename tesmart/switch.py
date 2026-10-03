"""High-level device model for a TESmart HDMI switch.

:class:`TesmartSwitch` sits on top of a :class:`~tesmart.transport.Transport`
and exposes the operations the device supports.  It owns the receive buffer
so that unsolicited input reports (sent by the switch whenever the input
changes, e.g. via the front panel or IR remote) are captured rather than
corrupting the reply to whatever command is in flight.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterator

from .protocol import (
    ASCII_KEYS,
    Command,
    Frame,
    LedTimeout,
    build_ascii_query,
    build_ascii_set,
    build_frame,
    normalize_ascii_value,
    parse_ascii_reply,
    parse_frames,
)
from .transport import Transport, TransportError

# The vendor's own tooling and community reports agree the switch misbehaves
# if commands are fired faster than roughly once a second.
MIN_COMMAND_INTERVAL = 1.0


class SwitchError(Exception):
    """Raised for protocol-level failures (no/invalid reply, bad argument)."""


def parse_input_list(text: str) -> tuple[int, ...]:
    """Parse ``"1,2,4"`` into input numbers, preserving order and dropping duplicates."""
    parts = [part.strip() for part in text.replace(" ", ",").split(",") if part.strip()]
    if not parts:
        raise ValueError("input list is empty")
    numbers: list[int] = []
    for part in parts:
        if not part.isdigit():
            raise ValueError(f"input must be a number, got {part!r}")
        number = int(part)
        if number not in numbers:
            numbers.append(number)
    return tuple(numbers)


def _cycle_pool(input_count: int, only: Sequence[int] | None) -> tuple[int, ...]:
    pool = tuple(only) if only else tuple(range(1, input_count + 1))
    if not pool:
        raise SwitchError("no inputs to rotate through")
    return pool


def next_input(current: int, input_count: int, only: Sequence[int] | None = None) -> int:
    """The input after ``current``, wrapping to the first.

    ``only`` restricts the cycle to those inputs, in the order given. The
    switch cannot report which inputs have a signal, so the cycle is the
    configured range ``1..input_count`` unless the caller passes a list.
    """
    pool = _cycle_pool(input_count, only)
    higher = [number for number in pool if number > current]
    return higher[0] if higher else pool[0]


def previous_input(current: int, input_count: int, only: Sequence[int] | None = None) -> int:
    """The input before ``current``, wrapping to the last of the cycle."""
    pool = _cycle_pool(input_count, only)
    lower = [number for number in pool if number < current]
    return lower[-1] if lower else pool[-1]


@dataclass
class NetworkConfig:
    ip: str | None = None
    port: str | None = None
    gateway: str | None = None
    mask: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass
class NetworkWriteResult:
    """Outcome of :meth:`TesmartSwitch.write_network_config`.

    ``acknowledged``: the switch answered ``OK`` to each write.
    ``stored``: what the switch reports *after* the write (all four values).
    ``verified``: for each written key, the stored value equals the requested one.
    ``pending_power_cycle``: always true; the switch applies these only after power-cycling.
    """

    requested: dict[str, str]
    acknowledged: dict[str, bool]
    stored: dict[str, str | None]
    verified: dict[str, bool]
    pending_power_cycle: bool = True

    @property
    def all_verified(self) -> bool:
        return all(self.verified.values()) and all(self.acknowledged.values())

    def to_dict(self) -> dict:
        data = asdict(self)
        data["all_verified"] = self.all_verified
        return data


@dataclass
class SwitchStatus:
    """Everything the device lets us *read*.

    Buzzer, LED timeout and auto-detect are write-only in the TESmart protocol
    so they are intentionally absent here.
    """

    endpoint: str
    active_input: int | None
    input_count: int
    network: NetworkConfig | None = None
    unsolicited_reports: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        return data


class TesmartSwitch:
    def __init__(
        self,
        transport: Transport,
        input_count: int = 8,
        reply_timeout: float = 1.5,
        command_interval: float = MIN_COMMAND_INTERVAL,
    ):
        self._transport = transport
        self.input_count = input_count
        self.reply_timeout = reply_timeout
        self.command_interval = command_interval
        self._buffer = b""
        self._pending_reports: list[Frame] = []
        self._last_command_at = 0.0

    # --- context management -------------------------------------------------

    def __enter__(self) -> "TesmartSwitch":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._transport.close()

    @property
    def endpoint(self) -> str:
        return self._transport.describe()

    # --- low-level helpers --------------------------------------------------

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_command_at
        if elapsed < self.command_interval:
            time.sleep(self.command_interval - elapsed)

    def _send(self, data: bytes) -> None:
        self._throttle()
        self._flush_stale()
        self._transport.write(data)
        self._last_command_at = time.monotonic()

    def _flush_stale(self) -> None:
        """Consume anything already waiting before a command goes out.

        The switch announces input changes to every connected client with the
        same ``0x11`` frame it uses to answer a query. A report that arrived
        before we send cannot be our reply, so it is moved to the unsolicited
        list instead of being mistaken for one.
        """
        for _ in range(16):
            frames = self._pump(0.02)
            if not frames:
                return
            for frame in frames:
                if frame.is_input_report:
                    self._pending_reports.append(frame)

    def _pump(self, timeout: float) -> list[Frame]:
        """Read once from the transport and return any complete hex frames."""
        chunk = self._transport.read(timeout)
        if not chunk:
            return []
        frames, self._buffer = parse_frames(self._buffer + chunk)
        return frames

    def _await_frame(self, predicate: Callable[[Frame], bool], timeout: float) -> Frame | None:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            for frame in self._pump(min(remaining, 0.25)):
                if predicate(frame):
                    return frame
                if frame.is_input_report:
                    self._pending_reports.append(frame)

    def _await_ascii(self, key: str, timeout: float) -> str | None:
        """Wait for an ASCII ``KEY:value;`` reply, tolerating a split ``;``."""
        deadline = time.monotonic() + timeout
        prefix = f"{ASCII_KEYS[key]}:".encode("ascii")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            for frame in self._pump(min(remaining, 0.25)):
                if frame.is_input_report:
                    self._pending_reports.append(frame)
            start = self._buffer.find(prefix)
            if start >= 0 and self._buffer.find(b";", start) >= 0:
                break
        if prefix not in self._buffer:
            return None
        value = parse_ascii_reply(key, self._buffer)
        # Drop the consumed reply (up to and including the ';' if present).
        start = self._buffer.find(prefix)
        end = self._buffer.find(b";", start)
        self._buffer = self._buffer[:start] + (self._buffer[end + 1 :] if end >= 0 else b"")
        return value

    def _await_ok(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for frame in self._pump(0.25):
                if frame.is_input_report:
                    self._pending_reports.append(frame)
            if b"OK" in self._buffer:
                self._buffer = self._buffer.replace(b"OK", b"", 1)
                return True
        return False

    def drain_unsolicited(self) -> list[int]:
        """Return (and clear) 1-based inputs from reports we did not ask for."""
        reports = [frame.input_number for frame in self._pending_reports]
        self._pending_reports.clear()
        return reports

    # --- hex protocol: reads ------------------------------------------------

    def get_active_input(self, retries: int = 2) -> int:
        """Return the 1-based active input."""
        last_error: str = "no reply"
        for _attempt in range(retries + 1):
            self._send(build_frame(Command.QUERY_INPUT, 0x00))
            frame = self._await_frame(lambda f: f.is_input_report, self.reply_timeout)
            if frame is not None:
                return frame.input_number
            last_error = f"no input report within {self.reply_timeout:.1f}s"
        raise SwitchError(f"failed to read active input: {last_error}")

    # --- hex protocol: writes -----------------------------------------------

    def _validate_input(self, number: int) -> None:
        if not 1 <= number <= self.input_count:
            raise SwitchError(f"input must be between 1 and {self.input_count}, got {number}")

    def rotate(self, only: Sequence[int] | None = None, *, direction: int = 1) -> tuple[int, int]:
        """Step along the cycle and return ``(previous, reported)``.

        ``direction`` 1 moves forward, -1 moves backward. ``only`` is an
        explicit cycle, for example ``(1, 2, 4)``. Without it the cycle is
        every input from 1 through ``input_count``.
        """
        if only:
            for number in only:
                self._validate_input(number)
        current = self.get_active_input()
        step = next_input if direction >= 0 else previous_input
        target = step(current, self.input_count, only)
        reported = self.set_input(target, verify=True)
        return current, reported if reported is not None else target

    def set_input(self, number: int, verify: bool = True) -> int | None:
        """Switch to ``number`` (1-based). Returns the input the switch reports, if any."""
        self._validate_input(number)
        self._send(build_frame(Command.SET_INPUT, number))
        if not verify:
            return None
        frame = self._await_frame(lambda f: f.is_input_report, self.reply_timeout)
        if frame is not None:
            return frame.input_number
        # Not every firmware echoes a report after a switch; ask explicitly.
        return self.get_active_input()

    def set_buzzer(self, enabled: bool) -> None:
        self._send(build_frame(Command.SET_BUZZER, 0x01 if enabled else 0x00))

    def set_led_timeout(self, timeout: LedTimeout) -> None:
        self._send(build_frame(Command.SET_LED_TIMEOUT, int(timeout)))

    def set_auto_detect(self, enabled: bool) -> None:
        self._send(build_frame(Command.SET_AUTO_DETECT, 0x01 if enabled else 0x00))

    def send_raw(self, data: bytes, listen: float | None = None) -> bytes:
        """Send arbitrary bytes and return whatever comes back within ``listen`` seconds."""
        self._send(data)
        deadline = time.monotonic() + (listen if listen is not None else self.reply_timeout)
        received = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            chunk = self._transport.read(min(remaining, 0.25))
            received.extend(chunk)
        return bytes(received)

    # --- ASCII protocol: network configuration ------------------------------

    def get_network_config(self) -> NetworkConfig:
        config = NetworkConfig()
        for key in ASCII_KEYS:
            self._send(build_ascii_query(key))
            setattr(config, key, self._await_ascii(key, self.reply_timeout))
        return config

    def set_network_config(self, **values: str) -> dict[str, bool]:
        """Write one or more of ``ip``, ``port``, ``gateway``, ``mask``.

        The switch persists each value immediately but only applies them after
        a power cycle.  Returns a mapping of key -> whether ``OK`` was seen.
        """
        unknown = set(values) - set(ASCII_KEYS)
        if unknown:
            raise SwitchError(f"unknown network setting(s): {', '.join(sorted(unknown))}")
        results: dict[str, bool] = {}
        for key, value in values.items():
            self._send(build_ascii_set(key, value))
            results[key] = self._await_ok(self.reply_timeout)
        return results

    def write_network_config(self, **values: str) -> NetworkWriteResult:
        """Write, then read every value back and compare.

        This is the only confirmation the protocol offers that a value is
        stored: ``OK`` says the frame was accepted, the read-back says what
        the switch will use after its next power cycle.
        """
        requested = {key: str(value).strip() for key, value in values.items()}
        acknowledged = self.set_network_config(**requested)
        stored = self.get_network_config().to_dict()
        verified = {
            key: stored.get(key) is not None and stored[key] == normalize_ascii_value(key, value)
            for key, value in requested.items()
        }
        return NetworkWriteResult(requested, acknowledged, stored, verified)

    # --- composite ----------------------------------------------------------

    def read_status(self, include_network: bool = True) -> SwitchStatus:
        active: int | None
        try:
            active = self.get_active_input()
        except (SwitchError, TransportError):
            active = None
        network = self.get_network_config() if include_network else None
        return SwitchStatus(
            endpoint=self.endpoint,
            active_input=active,
            input_count=self.input_count,
            network=network,
            unsolicited_reports=self.drain_unsolicited(),
        )

    def monitor(self, poll_interval: float = 0.5) -> Iterator[int]:
        """Yield the 1-based input every time the switch reports a change."""
        while True:
            for frame in self._pump(poll_interval):
                if frame.is_input_report:
                    yield frame.input_number
