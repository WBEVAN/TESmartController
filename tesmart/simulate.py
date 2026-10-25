"""In-memory switch for demos and for development without hardware.

``tesmartctl listen --simulate`` and ``tesmartctl quickstart --simulate`` use
this in place of a LAN or RS232 session. Selecting an input, peek, next,
previous, and cycle update the active input here. Nothing is sent to a
TESmart switch.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Sequence

from .protocol import ASCII_KEYS, normalize_ascii_value
from .switch import (
    NetworkConfig,
    NetworkWriteResult,
    SwitchError,
    SwitchStatus,
    next_input,
    previous_input,
)


class SimulatedSwitch:
    """The same operations the listener uses, answered from memory."""

    simulated = True

    def __init__(self, input_count: int = 8, active_input: int = 1):
        if input_count < 1:
            raise SwitchError("a simulated switch needs at least one input")
        if not 1 <= active_input <= input_count:
            raise SwitchError(f"input must be between 1 and {input_count}, got {active_input}")
        self.input_count = input_count
        self._active = active_input
        self._network = NetworkConfig(
            ip="192.168.1.10",
            port="5000",
            gateway="192.168.1.1",
            mask="255.255.255.0",
        )
        self._lock = threading.Lock()

    def __enter__(self) -> "SimulatedSwitch":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        return None

    @property
    def endpoint(self) -> str:
        return f"sim://{self.input_count}"

    def _validate_input(self, number: int) -> None:
        if not 1 <= number <= self.input_count:
            raise SwitchError(f"input must be between 1 and {self.input_count}, got {number}")

    def get_active_input(self, retries: int = 2) -> int:
        with self._lock:
            return self._active

    def set_input(self, number: int, verify: bool = True) -> int | None:
        self._validate_input(number)
        with self._lock:
            self._active = number
        if not verify:
            return None
        return number

    def rotate(self, only: Sequence[int] | None = None, *, direction: int = 1) -> tuple[int, int]:
        if only:
            for number in only:
                self._validate_input(number)
        current = self.get_active_input()
        step = next_input if direction >= 0 else previous_input
        target = step(current, self.input_count, only)
        reported = self.set_input(target, verify=True)
        return current, reported if reported is not None else target

    def set_buzzer(self, enabled: bool) -> None:
        return None

    def set_led_timeout(self, timeout: object) -> None:
        return None

    def set_auto_detect(self, enabled: bool) -> None:
        return None

    def send_raw(self, data: bytes, listen: float | None = None) -> bytes:
        return b""

    def get_network_config(self) -> NetworkConfig:
        with self._lock:
            return NetworkConfig(**self._network.to_dict())

    def set_network_config(self, **values: str) -> dict[str, bool]:
        unknown = set(values) - set(ASCII_KEYS)
        if unknown:
            raise SwitchError(f"unknown network setting(s): {', '.join(sorted(unknown))}")
        with self._lock:
            for key, value in values.items():
                setattr(self._network, key, normalize_ascii_value(key, value))
        return {key: True for key in values}

    def write_network_config(self, **values: str) -> NetworkWriteResult:
        requested = {key: str(value).strip() for key, value in values.items()}
        acknowledged = self.set_network_config(**requested)
        stored = self.get_network_config().to_dict()
        verified = {
            key: stored.get(key) is not None and stored[key] == normalize_ascii_value(key, value)
            for key, value in requested.items()
        }
        return NetworkWriteResult(requested, acknowledged, stored, verified)

    def read_status(self, include_network: bool = True) -> SwitchStatus:
        network = self.get_network_config() if include_network else None
        return SwitchStatus(
            endpoint=self.endpoint,
            active_input=self.get_active_input(),
            input_count=self.input_count,
            network=network,
            unsolicited_reports=[],
        )

    def drain_unsolicited(self) -> list[int]:
        return []

    def _pump(self, timeout: float) -> list:
        """Stay quiet. The callback watcher calls this while it looks for front-panel reports."""
        if timeout > 0:
            time.sleep(timeout)
        return []

    def monitor(self, poll_interval: float = 0.5) -> Iterator[int]:
        last = self.get_active_input()
        while True:
            time.sleep(poll_interval)
            current = self.get_active_input()
            if current != last:
                last = current
                yield current
