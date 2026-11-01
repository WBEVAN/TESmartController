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


# Virtual / tunnel NICs — publishing their IPs makes --via auto open the VPN
# address instead of the LAN when a tunnel is up.
_VPN_IFACE_PREFIXES = (
    "utun",
    "tun",
    "tap",
    "ppp",
    "ipsec",
    "wg",
    "tailscale",
    "zt",
    "nebula",
    "awdl",
    "llw",
    "bridge",
)


def _looks_like_vpn_iface(name: str) -> bool:
    text = (name or "").strip().lower()
    if not text:
        return False
    return any(text == prefix or text.startswith(prefix) for prefix in _VPN_IFACE_PREFIXES)


def _is_cgnat(ip: str) -> bool:
    """True for 100.64.0.0/10 (Tailscale / carrier-grade NAT — often VPN)."""
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return a == 100 and 64 <= b <= 127


def _is_rfc1918(ip: str) -> bool:
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    if a == 10:
        return True
    if a == 192 and b == 168:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    return False


def rank_ipv4(ip: str) -> tuple[int, str]:
    """Sort key for client/server address choice (lower is better).

    Prefer RFC1918 LAN addresses, then other globals, then CGNAT/VPN-ish,
    and put link-local / loopback last (normally filtered out earlier).
    """
    text = (ip or "").strip()
    if not text or ":" in text:
        return (90, text)
    if text.startswith("127.") or text.startswith("169.254."):
        return (80, text)
    if _is_cgnat(text):
        return (40, text)
    if _is_rfc1918(text):
        return (0, text)
    return (20, text)


def address_rank_label(ip: str) -> str:
    """Short reason ``pick_best_ipv4`` would prefer or avoid this address."""
    rank = rank_ipv4(ip)[0]
    if rank <= 0:
        return "lan"
    if rank <= 20:
        return "public"
    if rank <= 40:
        return "cgnat"
    if rank <= 80:
        return "local"
    return "other"


def format_adapter_line(name: str, nice: str, addresses: list[str], *, vpn: bool) -> str:
    """One local interface for ``--netdebug`` (VPN interfaces are marked skip)."""
    label = name or nice or "?"
    if nice and name and nice != name:
        label = f"{name} ({nice})"
    shown: list[str] = []
    for addr in addresses:
        if ":" in addr:
            shown.append(f"{addr} (ipv6)")
        else:
            shown.append(f"{addr} ({address_rank_label(addr)})")
    body = ", ".join(shown) if shown else "no address"
    kind = "skip vpn" if vpn else "use"
    return f"if {label} [{kind}] {body}"


def local_interface_lines() -> list[str]:
    """Every local interface, including tunnel NICs that advertising skips."""
    try:
        import ifaddr  # type: ignore[import-untyped]
    except ImportError:
        return ["ifaddr not installed"]
    lines: list[str] = []
    for adapter in ifaddr.get_adapters():
        name = getattr(adapter, "name", "") or ""
        nice = getattr(adapter, "nice_name", "") or ""
        addresses: list[str] = []
        for entry in adapter.ips:
            raw = entry.ip
            if isinstance(raw, tuple):
                addresses.append(str(raw[0]))
            elif isinstance(raw, str) and raw:
                addresses.append(raw)
        lines.append(
            format_adapter_line(
                name,
                nice,
                addresses,
                vpn=_looks_like_vpn_iface(name) or _looks_like_vpn_iface(nice),
            )
        )
    return lines or ["no interfaces"]


def describe_listener(service: DiscoveredListener) -> str:
    """One line: every advertised address, why it ranked, and the URL we call."""
    parts = [f"{ip} ({address_rank_label(ip)})" for ip in service.addresses]
    listed = ", ".join(parts) if parts else "none"
    host = service.hostname or "-"
    return (
        f"{service.instance_name} hostname={host} port={service.port} "
        f"addresses={listed} chosen={service.base_url}"
    )


def pick_best_ipv4(addresses: list[str] | tuple[str, ...]) -> str | None:
    """Choose the LAN-friendly IPv4 from a discovered/advertised set."""
    v4 = [ip for ip in addresses if ip and ":" not in ip]
    if not v4:
        return None
    return sorted(v4, key=rank_ipv4)[0]


def _ipv4_addresses(preferred: str | None = None) -> list[bytes]:
    """Local IPv4 addresses to publish (excludes loopback and VPN/tunnel NICs).

    Previously a connect-to-``8.8.8.8`` probe was used to learn the "default"
    source IP. With a VPN that returns the tunnel address, so mDNS clients
    tried to reach the listener over the VPN instead of the LAN. Prefer
    real interface enumeration (``ifaddr``, pulled in with zeroconf) and
    rank RFC1918 ahead of CGNAT / other globals.
    """
    ranked: list[tuple[tuple[int, str], str]] = []
    seen: set[str] = set()

    def add(ip: str, *, boost: int = 0) -> None:
        ip = ip.strip()
        if not ip or ip in seen or ":" in ip:
            return
        if ip.startswith("127.") or ip.startswith("169.254."):
            return
        try:
            socket.inet_aton(ip)
        except OSError:
            return
        seen.add(ip)
        score = rank_ipv4(ip)
        ranked.append(((score[0] + boost, score[1]), ip))

    if preferred and not is_loopback_host(preferred) and preferred not in {"0.0.0.0", "::", ""}:
        # Explicit bind IP always wins (operator chose this interface).
        add(preferred, boost=-10)

    try:
        import ifaddr  # type: ignore[import-untyped]

        for adapter in ifaddr.get_adapters():
            name = getattr(adapter, "name", "") or ""
            nice = getattr(adapter, "nice_name", "") or ""
            if _looks_like_vpn_iface(name) or _looks_like_vpn_iface(nice):
                continue
            for entry in adapter.ips:
                raw = entry.ip
                if isinstance(raw, tuple):
                    # IPv6 comes as (addr, flowinfo, scope_id)
                    continue
                if isinstance(raw, str):
                    add(raw)
    except Exception:
        # Fall back to hostname records only — never the default-route probe.
        try:
            for info in socket.getaddrinfo(
                socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM
            ):
                add(info[4][0])
        except OSError:
            pass
        try:
            add(socket.gethostbyname(socket.gethostname()))
        except OSError:
            pass

    ranked.sort(key=lambda item: item[0])
    return [socket.inet_aton(ip) for _, ip in ranked]


def local_lan_ipv4s() -> list[str]:
    """Non-tunnel local IPv4 addresses, best LAN candidate first.

    Used when a connection to the listener gets "No route to host" on the
    default route (often a VPN) and should be retried from a LAN interface.
    """
    return [socket.inet_ntoa(raw) for raw in _ipv4_addresses()]


def build_properties(
    *,
    version: str,
    panel: bool,
    input_count: int,
    path: str = "/",
    mesh: bool = False,
    switch_id: str | None = None,
    started_at: int | None = None,
    role: str | None = None,
    mesh_name: str | None = None,
    kind: str | None = None,
    link: str | None = None,
) -> dict[str, str]:
    """TXT records advertised with the service."""
    props = {
        "path": path,
        "version": version,
        "panel": "1" if panel else "0",
        "inputs": str(input_count),
        "proto": "http",
        "domain": MDNS_DOMAIN,
    }
    # Publish switch identity whenever known so discover can flag a standalone
    # listener that targets the same HDMI switch as a mesh group.
    if switch_id:
        props["switch"] = str(switch_id)
    if mesh and switch_id:
        props["mesh"] = "1"
        if started_at is not None:
            props["started"] = str(int(started_at))
        if role:
            props["role"] = str(role)
        if mesh_name:
            props["mesh_name"] = str(mesh_name)
        # Proxy TCP session to the HDMI switch (``up`` / ``down``). Clients are
        # ``down`` locally — discover reads this on the proxy / alias row.
        if link:
            props["link"] = str(link).strip().lower()
    if kind:
        props["kind"] = str(kind)
    return props


def is_mesh_alias(service: "DiscoveredListener | dict[str, str]") -> bool:
    """True when this DNS-SD instance is the shared mesh failover name (not a peer)."""
    props = service.properties if isinstance(service, DiscoveredListener) else service
    return str((props or {}).get("kind") or "").strip().lower() == "mesh"


@dataclass
class MdnsAdvertisement:
    """Active registration; call :meth:`close` on listener shutdown.

    When this node is the mesh proxy it may also register a second DNS-SD
    instance (the mesh alias) on the same Zeroconf object so sticky clients can
    keep using ``--mdns-name OfficeHDMI1-mesh`` across failover.
    """

    zeroconf: Any
    info: Any
    instance_name: str
    service_type: str = SERVICE_TYPE
    alias_info: Any | None = None
    alias_name: str | None = None
    _addresses: list[bytes] = field(default_factory=list)
    _port: int = 0
    _server: str = ""

    @property
    def service_name(self) -> str:
        return f"{self.instance_name}.{self.service_type}"

    def update_properties(self, properties: dict[str, str]) -> None:
        """Replace TXT records on the instance advertisement.

        ``ServiceInfo.properties`` is read-only in python-zeroconf; updates go
        through ``_set_properties`` then ``Zeroconf.update_service``.
        """
        self._apply_properties(self.info, properties)

    def _apply_properties(self, info: Any, properties: dict[str, str]) -> None:
        try:
            set_props = getattr(info, "_set_properties", None)
            if not callable(set_props):
                raise RuntimeError("zeroconf ServiceInfo has no _set_properties")
            set_props(properties)
            update = getattr(self.zeroconf, "update_service", None)
            if callable(update):
                update(info)
        except Exception as exc:
            print(f"warning: mDNS TXT update failed: {exc}", file=sys.stderr)

    def set_alias(
        self,
        name: str,
        properties: dict[str, str],
        *,
        _retry_delays: tuple[float, ...] = (0.0, 1.0, 2.0, 4.0),
    ) -> bool:
        """Register or refresh the mesh failover DNS-SD name (proxy only).

        Returns ``True`` when this process holds the alias. ``False`` means
        registration failed after retries (usually another live proxy already
        owns ``OfficeHDMI…-mesh``) — callers should join that owner instead of
        staying a second proxy.

        ``NonUniqueNameException`` is retried with backoff: hard-killed proxies
        and same-host Zeroconf ghosts often look like a live claim for a few
        seconds. Returning false immediately left ``…-mesh`` unpublished forever
        while ``tesmartctl discover`` showed ``alias not advertised yet``.
        """
        wanted = _normalize_instance(name)
        if not wanted:
            self.clear_alias()
            return False
        if (
            self.alias_info is not None
            and self.alias_name
            and self.alias_name.casefold() == wanted.casefold()
        ):
            self._apply_properties(self.alias_info, properties)
            return True
        self.clear_alias()
        try:
            from zeroconf import ServiceInfo
        except ImportError as exc:
            print(f"warning: mDNS mesh alias not registered: {exc}", file=sys.stderr)
            return False
        info = ServiceInfo(
            self.service_type,
            f"{wanted}.{self.service_type}",
            addresses=list(self._addresses),
            port=int(self._port),
            properties=properties,
            server=self._server or None,
        )
        registered = False
        last_exc: Exception | None = None
        delays = _retry_delays or (0.0,)
        for attempt, delay in enumerate(delays):
            if delay:
                time.sleep(delay)
            try:
                self.zeroconf.register_service(info)
                registered = True
                break
            except Exception as exc:
                last_exc = exc
                detail = str(exc).strip() or type(exc).__name__
                unique = type(exc).__name__ == "NonUniqueNameException"
                # Drop a half-registered / ghost record this Zeroconf may hold
                # so the next attempt can probe cleanly.
                try:
                    self.zeroconf.unregister_service(info)
                except BaseException:
                    pass
                if attempt + 1 < len(delays):
                    why = "already claimed" if unique else "conflict"
                    print(
                        f"warning: mDNS mesh alias {wanted!r} {why} ({detail}); retrying",
                        file=sys.stderr,
                    )
                    continue
                if unique:
                    print(
                        f"warning: mDNS mesh alias {wanted!r} already claimed "
                        f"({detail}); not stealing",
                        file=sys.stderr,
                    )
        if not registered:
            if last_exc is not None and type(last_exc).__name__ != "NonUniqueNameException":
                detail = (
                    str(last_exc).strip() or type(last_exc).__name__
                )
                print(
                    f"warning: mDNS mesh alias {wanted!r} not registered: {detail}",
                    file=sys.stderr,
                )
            return False
        self.alias_info = info
        self.alias_name = wanted
        print(f"  mDNS mesh alias: {wanted}.{self.service_type}", file=sys.stderr)
        return True

    def clear_alias(self) -> None:
        """Drop the mesh failover name (on demote / shutdown)."""
        if self.alias_info is None:
            self.alias_name = None
            return
        try:
            self.zeroconf.unregister_service(self.alias_info)
        except BaseException:
            pass
        if self.alias_name:
            print(f"  mDNS mesh alias cleared: {self.alias_name}", file=sys.stderr)
        self.alias_info = None
        self.alias_name = None

    def close(self) -> None:
        # KeyboardInterrupt is a BaseException, so a second Ctrl-C during
        # zeroconf's thread join must not escape as a traceback.
        try:
            self.clear_alias()
        except BaseException:
            pass
        try:
            self.zeroconf.unregister_service(self.info)
        except BaseException:
            pass
        try:
            self.zeroconf.close()
        except BaseException:
            pass


def _instance_label_from_service_name(service_name: str) -> str:
    """Strip ``._wbevan-tesmart._tcp.local.`` from a full DNS-SD service name."""
    text = (service_name or "").strip()
    suffix = f".{SERVICE_TYPE}"
    if text.endswith(suffix):
        return text[: -len(suffix)]
    if text.endswith(SERVICE_TYPE):
        return text[: -len(SERVICE_TYPE)].rstrip(".")
    return text


def advertise(
    *,
    port: int,
    bind_host: str,
    instance_name: str | None = None,
    version: str,
    panel: bool,
    input_count: int,
    mesh: bool = False,
    switch_id: str | None = None,
    started_at: int | None = None,
    role: str | None = None,
    mesh_name: str | None = None,
    file=sys.stderr,
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
    alias = (mesh_name or "").strip().replace(".", "-") or None
    # The proxy already owns the shared failover label. Using it as this
    # process's instance name always raises NonUniqueNameException and used to
    # abort startup with an empty error string.
    if alias and name.casefold() == alias.casefold():
        fallback = default_instance_name().replace(".", "-")
        if fallback.casefold() == name.casefold():
            fallback = f"{name}-node"
        print(
            f"warning: mDNS instance {name!r} is the mesh alias; advertising as {fallback!r}",
            file=file,
        )
        name = fallback

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
        mesh=mesh,
        switch_id=switch_id,
        started_at=started_at,
        role=role,
        mesh_name=mesh_name,
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
        # Rename Name → Name-2 when another host (or a stale cache) holds the
        # label. Mesh alias registration must NOT use this — see set_alias.
        zc.register_service(info, allow_name_change=True)
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        raise MdnsError(
            f"mDNS registration failed for {name!r}: {detail}"
        ) from exc

    actual = _instance_label_from_service_name(getattr(info, "name", "") or name)
    if actual and actual.casefold() != name.casefold():
        print(
            f"warning: mDNS instance {name!r} was taken; advertising as {actual!r}",
            file=file,
        )
        name = actual

    return MdnsAdvertisement(
        zeroconf=zc,
        info=info,
        instance_name=name,
        _addresses=list(addresses),
        _port=int(port),
        _server=server,
    )


def start_for_listener(
    *,
    bind_host: str,
    port: int,
    force: bool | None,
    instance_name: str | None,
    version: str,
    panel: bool,
    input_count: int,
    mesh: bool = False,
    switch_id: str | None = None,
    started_at: int | None = None,
    role: str | None = None,
    mesh_name: str | None = None,
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
            mesh=mesh,
            switch_id=switch_id,
            started_at=started_at,
            role=role,
            mesh_name=mesh_name,
            file=file,
        )
    except MdnsError as exc:
        if force is True:
            raise
        print(f"warning: mDNS not advertised: {exc}", file=file)
        return None

    props = build_properties(
        version=version,
        panel=panel,
        input_count=input_count,
        mesh=mesh,
        switch_id=switch_id,
        started_at=started_at,
        role=role,
        mesh_name=mesh_name,
    )
    txt = " ".join(f"{key}={value}" for key, value in props.items())
    try:
        published = [socket.inet_ntoa(addr) for addr in (adv.info.addresses or [])]
    except Exception:
        published = []
    addr_note = f" addrs={','.join(published)}" if published else ""
    print(
        f"  mDNS: {adv.service_name} port {port}{addr_note} ({txt})",
        file=file,
    )
    print(
        "  browse: tesmartctl discover   # cross-platform; macOS also: "
        f"dns-sd -B {SERVICE_NAME}._tcp local.",
        file=file,
    )
    return adv


def _mesh_member_sort_key(service: DiscoveredListener) -> tuple:
    """Proxy first, then clients, then by instance name."""
    role = str((service.properties or {}).get("role") or "").strip().lower()
    rank = 0 if role == "proxy" else 1 if role == "client" else 2
    return (rank, service.instance_name.casefold())


def group_discover_listeners(
    services: list[DiscoveredListener],
) -> dict[str, Any]:
    """Split browse hits into mesh groups and standalone listeners.

    Returns::

        {
          "mesh": [
            {
              "mesh_name": "OfficeHDMI1-mesh",
              "switch_id": "tcp:…",
              "alias": DiscoveredListener | None,   # failover DNS-SD name
              "members": [proxy, …clients],         # instance peers only
            },
            …
          ],
          "standalone": [DiscoveredListener, …],
        }
    """
    aliases: dict[str, DiscoveredListener] = {}
    members_by_key: dict[str, list[DiscoveredListener]] = {}
    key_meta: dict[str, dict[str, str | None]] = {}
    standalone: list[DiscoveredListener] = []

    for service in services:
        props = service.properties or {}
        if is_mesh_alias(service):
            key = service.instance_name.casefold()
            aliases[key] = service
            meta = key_meta.setdefault(key, {"mesh_name": service.instance_name, "switch_id": None})
            meta["mesh_name"] = service.instance_name
            switch = str(props.get("switch") or "").strip() or None
            if switch:
                meta["switch_id"] = switch
            continue

        if str(props.get("mesh") or "").strip() == "1":
            mesh_name = str(props.get("mesh_name") or "").strip()
            switch = str(props.get("switch") or "").strip()
            if mesh_name:
                key = mesh_name.casefold()
                label = mesh_name
            elif switch:
                key = f"switch:{switch.casefold()}"
                label = switch
            else:
                key = f"instance:{service.instance_name.casefold()}"
                label = service.instance_name
            members_by_key.setdefault(key, []).append(service)
            meta = key_meta.setdefault(key, {"mesh_name": label, "switch_id": switch or None})
            if mesh_name:
                meta["mesh_name"] = mesh_name
            if switch and not meta.get("switch_id"):
                meta["switch_id"] = switch
            continue

        standalone.append(service)

    mesh_groups: list[dict[str, Any]] = []
    seen: set[str] = set()

    for key in sorted(
        set(aliases) | set(members_by_key),
        key=lambda item: str((key_meta.get(item) or {}).get("mesh_name") or item).casefold(),
    ):
        if key in seen:
            continue
        seen.add(key)
        meta = key_meta.get(key) or {}
        alias = aliases.get(key)
        members = sorted(members_by_key.get(key, []), key=_mesh_member_sort_key)
        mesh_groups.append(
            {
                "mesh_name": meta.get("mesh_name") or (alias.instance_name if alias else key),
                "switch_id": meta.get("switch_id"),
                "alias": alias,
                "members": members,
            }
        )

    return {
        "mesh": mesh_groups,
        "standalone": sorted(standalone, key=lambda item: item.instance_name.casefold()),
    }


def discover_switch_conflicts(
    grouped: dict[str, Any],
) -> dict[str, dict[str, str]]:
    """Map standalone instance name → mesh that already owns the same switch.

    Keys are instance names; values have ``switch_id`` and ``mesh_name``.
    """
    mesh_by_switch: dict[str, str] = {}
    for group in grouped.get("mesh") or []:
        switch = str(group.get("switch_id") or "").strip()
        if not switch:
            continue
        mesh_by_switch[switch.casefold()] = str(
            group.get("mesh_name") or switch
        )

    conflicts: dict[str, dict[str, str]] = {}
    for item in grouped.get("standalone") or []:
        switch = str((item.properties or {}).get("switch") or "").strip()
        if not switch:
            continue
        mesh_name = mesh_by_switch.get(switch.casefold())
        if mesh_name:
            conflicts[item.instance_name] = {
                "switch_id": switch,
                "mesh_name": mesh_name,
            }
    return conflicts


def format_discover_text(
    services: list[DiscoveredListener],
    *,
    color: bool | None = None,
) -> str:
    """Human discover listing with MESH / Standalone sections.

    When ``color`` is None, ANSI is used only if stdout looks like a color
    terminal (respects ``NO_COLOR`` / ``FORCE_COLOR``).
    """
    from .ansi import ansi_supported, paint

    enabled = ansi_supported() if color is None else bool(color)
    grouped = group_discover_listeners(services)
    conflicts = discover_switch_conflicts(grouped)
    lines: list[str] = []

    def _host_port(item: DiscoveredListener) -> str:
        host = item.hostname or (item.addresses[0] if item.addresses else "?")
        return f"{host}:{item.port}"

    def _short_meta(item: DiscoveredListener) -> str:
        props = item.properties or {}
        bits: list[str] = []
        version = props.get("version")
        if version:
            bits.append(f"version={version}")
        panel = props.get("panel")
        if panel is not None:
            bits.append(f"panel={panel}")
        inputs = props.get("inputs")
        if inputs:
            bits.append(f"inputs={inputs}")
        return " ".join(bits)

    def _c(text: str, style: str | None) -> str:
        return paint(text, style, enabled=enabled)

    mesh_groups = list(grouped.get("mesh") or [])
    standalone = list(grouped.get("standalone") or [])

    if mesh_groups:
        lines.append(_c("MESH", "mesh"))
        for group in mesh_groups:
            mesh_name = group.get("mesh_name") or "(unnamed mesh)"
            switch_id = group.get("switch_id")
            alias = group.get("alias")
            members = list(group.get("members") or [])
            header = f"  {_c(str(mesh_name), 'heading')}"
            if switch_id:
                header += f"  ·  {_c(str(switch_id), 'dim')}"
            lines.append(header)
            if alias is not None:
                alias_link = str((alias.properties or {}).get("link") or "").strip().lower()
                alias_link_bit = ""
                if alias_link == "up":
                    alias_link_bit = f"  {_c('switch up', 'proxy')}"
                elif alias_link == "down":
                    alias_link_bit = f"  {_c('switch down', 'standalone')}"
                lines.append(
                    f"    {_c('failover →', 'dim')} {alias.base_url:<28} "
                    f"{_host_port(alias)}{alias_link_bit}"
                )
            elif mesh_name:
                lines.append(
                    f"    {_c('failover → (alias not advertised yet)', 'dim')}"
                )
            if not members:
                lines.append(f"    {_c('(no instance peers visible yet)', 'dim')}")
            member_roles = [
                str((item.properties or {}).get("role") or "").strip().lower()
                for item in members
            ]
            proxy_count = sum(1 for role in member_roles if role == "proxy")
            if members and proxy_count == 0:
                lines.append(
                    f"    {_c('⚠ no proxy — nobody owns the switch; restart mesh listeners', 'standalone')}"
                )
            elif proxy_count > 1:
                lines.append(
                    f"    {_c('⚠ dual proxy — node that cannot claim the mesh alias should join the alias owner', 'standalone')}"
                )
            proxy_link_down = False

            def _started_bit(props: dict) -> str:
                raw = props.get("started")
                try:
                    ms = int(float(raw)) if raw is not None else 0
                except (TypeError, ValueError):
                    return ""
                if ms <= 0:
                    return ""
                from datetime import datetime, timezone

                stamp = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime(
                    "%H:%M:%S UTC"
                )
                return f"  {_c(f'started {stamp}', 'dim')}"

            for item in members:
                props = item.properties or {}
                role = str(props.get("role") or "?").strip().lower() or "?"
                if role == "proxy":
                    label = _c("proxy", "proxy")
                elif role == "client":
                    label = _c("client", "client")
                else:
                    label = role
                # Pad visible role label to 7 cols (ANSI codes don't count).
                pad = max(0, 7 - len(role if role in {"proxy", "client"} else label))
                link = str(props.get("link") or "").strip().lower()
                link_bit = ""
                if role == "proxy" and link in {"up", "down"}:
                    if link == "up":
                        link_bit = f"  {_c('switch up', 'proxy')}"
                    else:
                        link_bit = f"  {_c('switch down', 'standalone')}"
                        proxy_link_down = True
                elif role == "client" and link == "down":
                    link_bit = f"  {_c('via proxy', 'dim')}"
                lines.append(
                    f"    {label}{' ' * pad} {item.instance_name:<18} "
                    f"{item.base_url:<28} {_host_port(item)}{link_bit}"
                    f"{_started_bit(props)}"
                )
            if proxy_link_down:
                lines.append(
                    f"    {_c('⚠ proxy has no TCP to the HDMI switch — kit stays offline', 'standalone')}"
                )
            # Alias TXT can show link even when instance peers lagged.
            if alias is not None and not proxy_link_down:
                alias_link = str((alias.properties or {}).get("link") or "").strip().lower()
                if alias_link == "down":
                    lines.append(
                        f"    {_c('⚠ mesh alias reports switch down', 'standalone')}"
                    )

    if standalone:
        if lines:
            lines.append("")
        lines.append(_c("STANDALONE", "standalone"))
        for item in standalone:
            extra = _short_meta(item)
            line = (
                f"  {item.instance_name:<20} {item.base_url:<28} {_host_port(item)}"
            )
            if extra:
                line = f"{line}  {_c(extra, 'dim')}"
            hit = conflicts.get(item.instance_name)
            if hit:
                warn = (
                    f"WARNING: same switch as mesh {hit['mesh_name']} "
                    f"({hit['switch_id']}) — prefer mesh or stop this listener"
                )
                line = f"{line}  {_c(warn, 'warn')}"
            lines.append(line)

    if conflicts:
        lines.append("")
        lines.append(
            _c(
                "Note: standalone listeners above share a switch with a mesh group "
                "and can fight over the HDMI connection.",
                "warn",
            )
        )

    if not lines:
        return "(no listeners)"
    return "\n".join(lines)


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
        """HTTP base URL clients should call (LAN IPv4 over VPN/CGNAT, then hostname)."""
        host = pick_best_ipv4(self.addresses)
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
    text = str(name).strip()
    if not text:
        return None
    return text.replace(".", "-")


def attach_mdns_identity(payload: dict[str, Any], server: object) -> None:
    """Set ``mdns_name`` / ``mesh_mdns_name`` on an API payload from the listener."""
    payload["mdns_name"] = getattr(server, "mdns_name", None)
    mesh = getattr(server, "mesh", None)
    if mesh is None:
        payload.pop("mesh_mdns_name", None)
        return
    name = None
    try:
        snap = mesh.snapshot()  # type: ignore[attr-defined]
        if isinstance(snap, dict):
            name = snap.get("mesh_mdns_name")
    except Exception:
        state = getattr(mesh, "state", None)
        name = getattr(state, "mesh_mdns_name", None) if state is not None else None
    text = str(name or "").strip() or None
    if text:
        payload["mesh_mdns_name"] = text
    else:
        payload.pop("mesh_mdns_name", None)


def mdns_identity_conflict(
    expected: str | None,
    reported: str | None,
    *,
    also_known_as: str | list[str] | tuple[str, ...] | None = None,
) -> bool:
    """True when a client bound to ``expected`` must not attach to this listener.

    A client with no mDNS name accepts whatever answers. A listener that is
    not advertising (no reported name) does not contradict a bound client.

    ``also_known_as`` lists extra DNS-SD names this listener answers for — the
    shared mesh failover alias (``OfficeHDMI1-mesh``) when the process is (or
    knows) the mesh group name. Clients that resolved via that alias must not
    be rejected just because ``/version`` reports the instance name.
    """
    want = _normalize_instance(expected)
    if not want:
        return False
    aliases: list[str] = []
    for raw in (reported,):
        got = _normalize_instance(raw)
        if got:
            aliases.append(got.casefold())
    extra: list[str]
    if also_known_as is None:
        extra = []
    elif isinstance(also_known_as, (list, tuple)):
        extra = [str(item) for item in also_known_as]
    else:
        extra = [str(also_known_as)]
    for raw in extra:
        got = _normalize_instance(raw)
        if got:
            aliases.append(got.casefold())
    if not aliases:
        return False
    return want.casefold() not in aliases


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


def listeners_for_name(
    services: list[DiscoveredListener],
    name: str,
) -> list[DiscoveredListener]:
    """Candidates for a DNS-SD name, including mesh failover aliases.

    Order: instance peers with ``mesh_name=NAME`` and ``role=proxy`` first (live
    owner), then exact instance / ``kind=mesh`` alias matches, then other peers
    with that ``mesh_name``. Live proxies win over a stale alias still pointing
    at a hard-killed previous proxy.
    """
    wanted = _normalize_instance(name)
    if not wanted:
        return []
    want = wanted.casefold()
    exact: list[DiscoveredListener] = []
    proxies: list[DiscoveredListener] = []
    others: list[DiscoveredListener] = []
    seen: set[str] = set()

    def _add(bucket: list[DiscoveredListener], service: DiscoveredListener) -> None:
        key = f"{service.instance_name.casefold()}|{service.base_url.rstrip('/')}"
        if key in seen:
            return
        seen.add(key)
        bucket.append(service)

    for service in services:
        if service.instance_name.casefold() == want:
            _add(exact, service)

    for service in services:
        props = service.properties or {}
        mesh_name = _normalize_instance(str(props.get("mesh_name") or ""))
        if not mesh_name or mesh_name.casefold() != want:
            continue
        if is_mesh_alias(service):
            _add(exact, service)
            continue
        role = str(props.get("role") or "").strip().lower()
        if role == "proxy":
            _add(proxies, service)
        else:
            _add(others, service)

    return proxies + exact + others


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
        matches = listeners_for_name(services, wanted)
        # Sticky instance name after mesh failover (env still has OfficeHDMI2,
        # LAN only has OfficeHDMI2-mesh / OfficeHDMI2-2): try the mesh alias.
        if not matches and not wanted.casefold().endswith("-mesh"):
            matches = listeners_for_name(services, f"{wanted}-mesh")
        if not matches:
            # Last resort on a single-mesh LAN: the sole advertising proxy.
            proxies = [
                item
                for item in services
                if str((item.properties or {}).get("mesh") or "").strip() == "1"
                and str((item.properties or {}).get("role") or "").strip().lower()
                == "proxy"
                and str((item.properties or {}).get("kind") or "").strip().lower()
                != "mesh"
            ]
            if len(proxies) == 1:
                matches = proxies
        if not matches:
            available = ", ".join(item.instance_name for item in services)
            raise MdnsError(
                f"no listener named {wanted!r}; found: {available}"
            )
        # Named lookup ignores prefer_url: a cached via often points at the
        # previous proxy after mesh failover and would pin the dead endpoint.
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
    from .debug import log

    services = browse(timeout=timeout)
    for item in services:
        log(f"browse {describe_listener(item)}")
    if prefer_url:
        log(f"saved via {prefer_url}")
    if name:
        log(f"want name {name}")
    chosen = pick_listener(services, name=name, prefer_url=prefer_url)
    log(f"using {chosen.base_url}")
    return chosen


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
