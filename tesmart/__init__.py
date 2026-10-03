"""Control library and CLI for TESmart HDMI switches (hex + ASCII protocols)."""

from .protocol import Command, Frame, LedTimeout, build_frame, parse_frames
from .switch import NetworkConfig, SwitchStatus, TesmartSwitch
from .transport import SerialTransport, TcpTransport, Transport

__all__ = [
    "Command",
    "Frame",
    "LedTimeout",
    "NetworkConfig",
    "SerialTransport",
    "SwitchStatus",
    "TcpTransport",
    "TesmartSwitch",
    "Transport",
    "build_frame",
    "parse_frames",
]
