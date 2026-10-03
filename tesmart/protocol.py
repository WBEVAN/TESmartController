"""Pure encoding/decoding of the TESmart wire protocols.

Nothing in this module touches a socket or serial port; it only builds and
parses bytes so it can be unit-tested in isolation.

Hex protocol (documented by TESmart)
------------------------------------
Every frame is six bytes::

    AA BB 03 <cmd> <value> EE

The switch replies to an input query (``cmd=0x10``) with an input report
(``cmd=0x11``) whose value is the *zero-based* active input.  The switch also
emits the same report unsolicited when the input changes.  The terminator byte
is nominally ``0xEE`` but ``0x16`` and ``0x1C`` have been observed in the
field, so the parser accepts any terminator.

ASCII protocol (undocumented, used by the vendor's Windows tool)
----------------------------------------------------------------
Network settings are read with ``IP?`` / ``PT?`` / ``GW?`` / ``MA?`` and
written with ``IP:<value>;`` etc.  Replies look like ``IP:192.168.001.010;``
and the trailing ``;`` may arrive in a separate TCP segment.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

HEADER = b"\xAA\xBB\x03"
TERMINATOR = 0xEE
FRAME_LENGTH = 6


class Command(IntEnum):
    """Command byte (4th byte) of a hex frame."""

    SET_INPUT = 0x01  # value: 1-based input number
    SET_BUZZER = 0x02  # value: 0 = mute, 1 = unmute
    SET_LED_TIMEOUT = 0x03  # value: LedTimeout
    QUERY_INPUT = 0x10  # value: 0x00
    INPUT_REPORT = 0x11  # (device -> host) value: 0-based active input
    SET_AUTO_DETECT = 0x81  # value: 0 = off, 1 = on


class LedTimeout(IntEnum):
    """Front-panel LED timeout values accepted by ``Command.SET_LED_TIMEOUT``."""

    NEVER = 0x00
    SECONDS_10 = 0x0A
    SECONDS_30 = 0x1E

    @classmethod
    def from_text(cls, text: str) -> "LedTimeout":
        normalized = text.strip().lower().rstrip("s")
        mapping = {
            "never": cls.NEVER,
            "off": cls.NEVER,
            "0": cls.NEVER,
            "10": cls.SECONDS_10,
            "30": cls.SECONDS_30,
        }
        try:
            return mapping[normalized]
        except KeyError as exc:
            raise ValueError(
                f"invalid LED timeout {text!r}; expected one of never, 10, 30"
            ) from exc

    def describe(self) -> str:
        return {
            LedTimeout.NEVER: "never",
            LedTimeout.SECONDS_10: "10s",
            LedTimeout.SECONDS_30: "30s",
        }[self]


@dataclass(frozen=True)
class Frame:
    """A decoded hex-protocol frame."""

    command: int
    value: int
    terminator: int = TERMINATOR

    @property
    def is_input_report(self) -> bool:
        return self.command == Command.INPUT_REPORT

    @property
    def input_number(self) -> int:
        """The 1-based input number carried by an input report."""
        if not self.is_input_report:
            raise ValueError("frame is not an input report")
        return self.value + 1

    def to_bytes(self) -> bytes:
        return HEADER + bytes((self.command, self.value, self.terminator))

    def hex(self) -> str:
        return self.to_bytes().hex(" ").upper()


def build_frame(command: int, value: int = 0) -> bytes:
    """Encode a host -> device frame."""
    if not 0 <= command <= 0xFF:
        raise ValueError(f"command byte out of range: {command}")
    if not 0 <= value <= 0xFF:
        raise ValueError(f"value byte out of range: {value}")
    return HEADER + bytes((command, value, TERMINATOR))


def parse_frames(buffer: bytes) -> tuple[list[Frame], bytes]:
    """Extract every complete frame from ``buffer``.

    Returns ``(frames, remainder)`` where ``remainder`` holds any trailing
    bytes that do not yet form a complete frame (so the caller can keep
    accumulating).  Bytes that precede a header and are not part of a frame
    (for example ASCII replies) are preserved in ``remainder`` so a caller
    interested in both protocols can still see them.
    """
    frames: list[Frame] = []
    remainder = bytearray()
    index = 0
    while index < len(buffer):
        start = buffer.find(HEADER, index)
        if start < 0:
            remainder.extend(buffer[index:])
            break
        remainder.extend(buffer[index:start])
        if start + FRAME_LENGTH > len(buffer):
            # Incomplete frame at the tail: keep it for the next read.
            remainder.extend(buffer[start:])
            break
        command = buffer[start + 3]
        value = buffer[start + 4]
        terminator = buffer[start + 5]
        frames.append(Frame(command, value, terminator))
        index = start + FRAME_LENGTH
    return frames, bytes(remainder)


def parse_hex_string(text: str) -> bytes:
    """Parse user-supplied hex such as ``"AA BB 03 10 00 EE"`` or ``"aabb031000ee"``."""
    cleaned = text.replace("0x", "").replace("0X", "").replace(",", " ")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise ValueError(f"invalid hex string: {text!r}") from exc


# --- ASCII network-configuration protocol ------------------------------------

ASCII_KEYS = {
    "ip": "IP",
    "port": "PT",
    "gateway": "GW",
    "mask": "MA",
}


def build_ascii_query(key: str) -> bytes:
    """``build_ascii_query("ip") -> b"IP?"``"""
    return f"{ASCII_KEYS[key]}?".encode("ascii")


def build_ascii_set(key: str, value: str) -> bytes:
    """``build_ascii_set("ip", "192.168.1.20") -> b"IP:192.168.1.20;"``"""
    return f"{ASCII_KEYS[key]}:{value};".encode("ascii")


def parse_ascii_reply(key: str, payload: bytes) -> str | None:
    """Return the value from a reply such as ``b"IP:192.168.001.010;"``.

    Leading zeros in dotted-quad octets and port numbers are stripped so the
    result is human- and ``ipaddress``-friendly.  Returns ``None`` if the
    expected ``KEY:`` prefix is not present.
    """
    prefix = f"{ASCII_KEYS[key]}:".encode("ascii")
    start = payload.find(prefix)
    if start < 0:
        return None
    end = payload.find(b";", start)
    raw = payload[start + len(prefix) : end if end >= 0 else None]
    return normalize_ascii_value(key, raw.decode("ascii", errors="replace"))


def normalize_ascii_value(key: str, text: str) -> str:
    """Canonical form of a network value: ``192.168.001.010`` -> ``192.168.1.10``, ``05000`` -> ``5000``.

    Used both for replies from the switch and for values a caller asks to
    write, so the two can be compared after a read-back.
    """
    text = text.strip()
    if key == "port":
        return str(int(text)) if text.isdigit() else text
    if "." in text:
        parts = text.split(".")
        if all(part.isdigit() for part in parts):
            return ".".join(str(int(part)) for part in parts)
    return text
