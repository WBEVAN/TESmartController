"""mDNS / DNS-SD for the HTTP listener (advertise + browse).

When ``tesmartctl listen --http`` is reachable on the LAN, this module can
register a ``_wbevan-tesmart._tcp.local.`` service so clients (and macOS
``dns-sd``) can find host and port without a typed URL. Clients use
:func:`browse` / :func:`resolve_listener` the same way.

The service type is namespaced under wbevan.com (15-character DNS-SD limit)
so it does not collide with a generic ``_tesmartctl`` type from another project.

Requires the optional ``zeroconf`` package (``pip install tesmartctl[mdns]``).
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

# DNS-SD service names must be ≤15 characters (RFC 6763). Namespaced to the
# project owner's domain so LAN browses do not clash with other tesmart tools.
MDNS_DOMAIN = "wbevan.com"
SERVICE_NAME = "_wbevan-tesmart"  # 13 chars
SERVICE_TYPE = f"{SERVICE_NAME}._tcp.local."
DEFAULT_BROWSE_TIMEOUT = 2.0

# Hint printed when advertisement is requested but zeroconf is missing.
INSTALL_HINT = "pip install 'tesmartctl[mdns]'   # or: pip install zeroconf"


class MdnsError(Exception):
    """Advertisement or discovery failed."""


def is_loopback_host(host: str) -> bool:
    """True when ``host`` only accepts connections from this machine."""
    text = (host or "").strip().lower()
    return text in {"127.0.0.1", "::1", "localhost"}


def default_instance_name() -> str:
    """Human-readable instance name derived from the machine hostname."""
    name = socket.gethostname().strip() or "tesmartctl"
    if name.endswith(".local"):
        name = name[: -len(".local")]
    return name or "tesmartctl"


def should_advertise(*, bind_host: str, force: bool | None) -> bool:
    """Decide whether to register an mDNS service for this HTTP bind.

    ``force`` is True for ``--mdns``, False for ``--no-mdns``, None for default
    (advertise when the bind is not loopback-only).
    """
    if force is False:
        return False
    if force is True:
        return True
    return not is_loopback_host(bind_host)


def _ipv4_addresses(preferred: str | None = None) -> list[bytes]:
    """Local IPv4 addresses to publish (excludes loopback)."""
    found: list[bytes] = []
    seen: set[str] = set()

    def add(ip: str) -> None:
        ip = ip.strip()
        if not ip or ip in seen or ip.startswith("127.") or ip.startswith("169.254."):
            return
        try:
            found.append(socket.inet_aton(ip))
            seen.add(ip)
        except OSError:
            return

    if preferred and not is_loopback_host(preferred) and preferred not in {"0.0.0.0", "::", ""}:
        add(preferred)

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM):
            add(info[4][0])
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        add(socket.gethostbyname(hostname))
    except OSError:
        pass

    # Route trick: ask the kernel which source IP it would use for LAN traffic.
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            add(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass

    return found


def build_properties(
    *,
    version: str,
    panel: bool,
    input_count: int,
    path: str = "/",
) -> dict[str, str]:
    """TXT records advertised with the service."""
    return {
        "path": path,
        "version": version,
        "panel": "1" if panel else "0",
        "inputs": str(input_count),
        "proto": "http",
        "domain": MDNS_DOMAIN,
    }


@dataclass
class MdnsAdvertisement:
    """Active registration; call :meth:`close` on listener shutdown."""

    zeroconf: Any
    info: Any
    instance_name: str
    service_type: str = SERVICE_TYPE

    @property
    def service_name(self) -> str:
        return f"{self.instance_name}.{self.service_type}"

    def close(self) -> None:
        try:
            self.zeroconf.unregister_service(self.info)
        except Exception:
            pass
        try:
            self.zeroconf.close()
        except Exception:
            pass


def advertise(
    *,
    port: int,
    bind_host: str,
    instance_name: str | None = None,
    version: str,
    panel: bool,
    input_count: int,
) -> MdnsAdvertisement:
    """Register ``_wbevan-tesmart._tcp`` on the LAN. Raises :class:`MdnsError` on failure."""
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError as exc:
        raise MdnsError(
            f"mDNS advertisement needs the zeroconf package ({INSTALL_HINT})"
        ) from exc

    name = (instance_name or default_instance_name()).strip() or default_instance_name()
    # DNS-SD instance labels must not contain dots in the instance portion.
    name = name.replace(".", "-")

    preferred = None if bind_host in {"0.0.0.0", "::", ""} else bind_host
    addresses = _ipv4_addresses(preferred)
    if not addresses:
        raise MdnsError("no non-loopback IPv4 address available to advertise")

    hostname = default_instance_name()
    server = f"{hostname}.local."
    properties = build_properties(
        version=version,
        panel=panel,
        input_count=input_count,
    )
    info = ServiceInfo(
        SERVICE_TYPE,
        f"{name}.{SERVICE_TYPE}",
        addresses=addresses,
        port=int(port),
        properties=properties,
        server=server,
    )
    try:
        zc = Zeroconf()
        zc.register_service(info)
    except Exception as exc:
        raise MdnsError(f"mDNS registration failed: {exc}") from exc

    return MdnsAdvertisement(zeroconf=zc, info=info, instance_name=name)


def start_for_listener(
    *,
    bind_host: str,
    port: int,
    force: bool | None,
    instance_name: str | None,
    version: str,
    panel: bool,
    input_count: int,
    file=sys.stderr,
) -> MdnsAdvertisement | None:
    """Start advertisement when policy says so; print status lines to ``file``.

    Returns the active registration, or ``None`` when skipped / failed soft.
    A hard failure (``--mdns`` requested but zeroconf missing) raises
    :class:`MdnsError`.
    """
    if not should_advertise(bind_host=bind_host, force=force):
        return None

    if is_loopback_host(bind_host) and force is True:
        print(
            "warning: --mdns with a loopback bind advertises LAN addresses, "
            "but HTTP on this bind is not reachable from other hosts",
            file=file,
        )

    try:
        adv = advertise(
            port=port,
            bind_host=bind_host,
            instance_name=instance_name,
            version=version,
            panel=panel,
            input_count=input_count,
        )
    except MdnsError as exc:
        if force is True:
            raise
        print(f"warning: mDNS not advertised: {exc}", file=file)
        return None

    props = build_properties(version=version, panel=panel, input_count=input_count)
    txt = " ".join(f"{key}={value}" for key, value in props.items())
    print(
        f"  mDNS: {adv.service_name} port {port} ({txt})",
        file=file,
    )
    print(
        "  browse: tesmartctl discover   # cross-platform; macOS also: "
        f"dns-sd -B {SERVICE_NAME}._tcp local.",
        file=file,
    )
    return adv


# --- client browse / resolve -------------------------------------------------


@dataclass(frozen=True)
class DiscoveredListener:
    """One ``_wbevan-tesmart._tcp`` instance seen on the LAN."""

    instance_name: str
    port: int
    addresses: tuple[str, ...] = ()
    hostname: str | None = None
    properties: dict[str, str] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        """HTTP base URL clients should call (prefers IPv4, then hostname)."""
        host = next((ip for ip in self.addresses if ":" not in ip), None)
        if host is None and self.addresses:
            host = self.addresses[0]
        if host is None:
            host = (self.hostname or "127.0.0.1").rstrip(".")
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_name": self.instance_name,
            "hostname": self.hostname,
            "port": self.port,
            "addresses": list(self.addresses),
            "base_url": self.base_url,
            "properties": dict(self.properties),
        }


def _instance_from_service_name(name: str) -> str:
    suffix = f".{SERVICE_TYPE}"
    if name.endswith(suffix):
        return name[: -len(suffix)]
    if name.endswith("."):
        name = name[:-1]
    parts = name.split(".")
    return parts[0] if parts else name


def _normalize_instance(name: str | None) -> str | None:
    if name is None:
        return None
    text = name.strip()
    if not text:
        return None
    return text.replace(".", "-")


def _properties_from_info(info: Any) -> dict[str, str]:
    decoded = getattr(info, "decoded_properties", None)
    if isinstance(decoded, dict) and decoded:
        return {str(key): "" if value is None else str(value) for key, value in decoded.items()}
    props: dict[str, str] = {}
    raw = getattr(info, "properties", None) or {}
    for key, value in raw.items():
        k = key.decode("utf-8", "replace") if isinstance(key, bytes) else str(key)
        if value is None:
            props[k] = ""
        elif isinstance(value, bytes):
            props[k] = value.decode("utf-8", "replace")
        else:
            props[k] = str(value)
    return props


def _listener_from_info(name: str, info: Any) -> DiscoveredListener | None:
    if info is None or not info.port:
        return None
    addresses: list[str] = []
    try:
        addresses = [str(addr) for addr in info.parsed_addresses()]
    except Exception:
        addresses = []
    hostname = (info.server or "").rstrip(".") or None
    return DiscoveredListener(
        instance_name=_instance_from_service_name(name),
        port=int(info.port),
        addresses=tuple(addresses),
        hostname=hostname,
        properties=_properties_from_info(info),
    )


def browse(*, timeout: float = DEFAULT_BROWSE_TIMEOUT) -> list[DiscoveredListener]:
    """Browse for ``_wbevan-tesmart._tcp`` listeners. Raises :class:`MdnsError` if zeroconf is missing."""
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError as exc:
        raise MdnsError(
            f"mDNS discovery needs the zeroconf package ({INSTALL_HINT})"
        ) from exc

    found: dict[str, DiscoveredListener] = {}
    lock = threading.Lock()

    class _Listener(ServiceListener):
        def add_service(self, zc: Any, type_: str, name: str) -> None:
            info = zc.get_service_info(type_, name, timeout=int(max(timeout, 0.5) * 1000))
            service = _listener_from_info(name, info)
            if service is None:
                return
            with lock:
                found[service.instance_name.lower()] = service

        def update_service(self, zc: Any, type_: str, name: str) -> None:
            self.add_service(zc, type_, name)

        def remove_service(self, zc: Any, type_: str, name: str) -> None:
            key = _instance_from_service_name(name).lower()
            with lock:
                found.pop(key, None)

    zc = Zeroconf()
    try:
        browser = ServiceBrowser(zc, SERVICE_TYPE, _Listener())
        time.sleep(max(0.1, float(timeout)))
        browser.cancel()
    finally:
        zc.close()

    with lock:
        return sorted(found.values(), key=lambda item: item.instance_name.lower())


def pick_listener(
    services: list[DiscoveredListener],
    *,
    name: str | None = None,
    prefer_url: str | None = None,
) -> DiscoveredListener:
    """Choose one service from a browse result. Raises :class:`MdnsError` when ambiguous/empty."""
    if not services:
        raise MdnsError(
            "no tesmartctl HTTP listeners found on the LAN "
            "(is `tesmartctl listen --http 0.0.0.0:PORT` running with mDNS?)"
        )

    wanted = _normalize_instance(name)
    if wanted:
        matches = [item for item in services if item.instance_name.lower() == wanted.lower()]
        if not matches:
            available = ", ".join(item.instance_name for item in services)
            raise MdnsError(
                f"no listener named {wanted!r}; found: {available}"
            )
        return matches[0]

    if prefer_url:
        prefer = prefer_url.rstrip("/")
        for item in services:
            if item.base_url.rstrip("/") == prefer:
                return item
            # Also match hostname form if the cached URL used an IP (or vice versa).
            if item.hostname and prefer.endswith(f":{item.port}"):
                host = urlparse(prefer).hostname or ""
                if host in item.addresses or host.rstrip(".") == item.hostname.rstrip("."):
                    return item

    if len(services) == 1:
        return services[0]

    available = ", ".join(item.instance_name for item in services)
    raise MdnsError(
        f"multiple listeners on the LAN ({available}); pass --mdns-name NAME"
    )


def resolve_listener(
    *,
    name: str | None = None,
    prefer_url: str | None = None,
    timeout: float = DEFAULT_BROWSE_TIMEOUT,
) -> DiscoveredListener:
    """Browse then :func:`pick_listener`. Convenience for ``--via auto``."""
    return pick_listener(browse(timeout=timeout), name=name, prefer_url=prefer_url)


def parse_via_target(text: str) -> str:
    """Normalize ``--via`` value: ``auto`` stays ``auto``, else an absolute http(s) base URL."""
    raw = (text or "").strip()
    if not raw:
        raise MdnsError("empty --via value")
    if raw.lower() == "auto":
        return "auto"
    if not raw.startswith(("http://", "https://")):
        raise MdnsError("--via must be 'auto' or an absolute http(s) URL")
    parsed = urlparse(raw)
    if not parsed.netloc:
        raise MdnsError(f"invalid --via URL: {raw}")
    return raw.rstrip("/")
