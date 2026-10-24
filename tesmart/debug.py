"""Console diagnostics for ``tesmartctl --debug``.

Off unless the process was started with ``--debug`` or ``TESMART_DEBUG=1``.
``--netdebug`` (or ``TESMART_NETDEBUG=1``) also prints local interfaces, the
default-route source, and the saved host / via settings. Child kit processes
inherit both environment variables.
"""

from __future__ import annotations

import os
import socket
import sys

_TRUE = {"1", "true", "yes", "on"}
_ENABLED = os.environ.get("TESMART_DEBUG", "").strip().lower() in _TRUE
_NETDEBUG = os.environ.get("TESMART_NETDEBUG", "").strip().lower() in _TRUE


def enabled() -> bool:
    return _ENABLED


def netdebug_enabled() -> bool:
    return _NETDEBUG


def set_enabled(on: bool) -> None:
    """Turn console diagnostics on or off for this process and its children."""
    global _ENABLED
    _ENABLED = bool(on)
    if _ENABLED:
        os.environ["TESMART_DEBUG"] = "1"
    else:
        os.environ.pop("TESMART_DEBUG", None)


def set_netdebug(on: bool) -> None:
    """Turn the network dump on. Implies ``--debug`` for this process."""
    global _NETDEBUG
    _NETDEBUG = bool(on)
    if _NETDEBUG:
        os.environ["TESMART_NETDEBUG"] = "1"
        set_enabled(True)
    else:
        os.environ.pop("TESMART_NETDEBUG", None)


def log(message: str) -> None:
    """Print one diagnostic line to stderr. No-op when debug is off."""
    if not _ENABLED:
        return
    print(f"debug: {message}", file=sys.stderr, flush=True)


def default_source() -> str:
    """IPv4 the OS would use as the source toward a non-local address.

    A UDP connect does not transmit. A VPN default route shows up here as the
    tunnel address.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        return str(sock.getsockname()[0])
    except OSError as exc:
        return exc.strerror or str(exc)
    finally:
        sock.close()


def log_network(
    env_file: str | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
) -> None:
    """Print saved connection settings and this machine's interfaces."""
    if not _NETDEBUG:
        return
    set_enabled(True)
    from . import config
    from .mdns import local_interface_lines

    try:
        settings = config.resolve(env_file, host=host, port=port)
    except config.ConfigError as exc:
        log(f"network settings unreadable ({exc})")
        settings = None
    if settings is not None:
        log(f"network env file {settings.env_file}")
        log(
            f"network switch host={settings.host} ({settings.sources.get('host', '?')}) "
            f"port={settings.port} ({settings.sources.get('port', '?')})"
        )
    log(f"network via={config.read_via_url(env_file) or '-'}")
    log(f"network mdns_name={config.read_mdns_name(env_file) or '-'}")
    if config.client_connection_active(env_file):
        log(f"network client via={config.read_client_via_url(env_file) or '-'}")
        log(f"network client mdns_name={config.read_client_mdns_name(env_file) or '-'}")
    log(f"network default source {default_source()}")
    for line in local_interface_lines():
        log(f"network {line}")
