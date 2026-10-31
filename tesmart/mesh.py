"""Server mesh: one TCP switch owner among several HTTP listeners.

With ``--mesh`` / ``TESMART_HTTP_MESH=1``, listeners that target the same
TCP switch elect the oldest process as **proxy**. Younger peers join as
**mesh clients**, pull configuration from the proxy, and relay switch and
shared-state HTTP routes. Serial switches are out of scope.

See ``Docs/mesh.md``.
"""

from __future__ import annotations

import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from . import config
from .config_bundle import apply_bundle, export_bundle, parse_bundle, summarize_bundle
from .mdns import DiscoveredListener, MdnsError, browse
from .switch import SwitchError
from .version import package_version

HTTP_MESH_KEY = "TESMART_HTTP_MESH"
HTTP_MESH_PROXY_KEY = "TESMART_HTTP_MESH_PROXY"
HTTP_MESH_MDNS_NAME_KEY = "TESMART_HTTP_MESH_MDNS_NAME"

ROLE_PROXY = "proxy"
ROLE_CLIENT = "client"

# Env keys that stay on each host. Mesh config pull adopts shared switch /
# panel state from the proxy, but must not rewrite this process's instance
# mDNS name, HTTP bind, or client-via prefs. Only ``TESMART_HTTP_MESH_MDNS_NAME``
# (the shared failover alias) is mesh identity that should converge.
MESH_LOCAL_ENV_KEYS = frozenset(
    {
        config.MDNS_NAME_KEY,
        config.HTTP_KEY,
        config.HTTP_MDNS_KEY,
        HTTP_MESH_PROXY_KEY,
        config.VIA_KEY,
        config.CLIENT_VIA_KEY,
        config.CLIENT_MDNS_NAME_KEY,
    }
)

# mDNS / discover: does this process own a live TCP session to the HDMI switch?
LINK_UP = "up"
LINK_DOWN = "down"


def switch_link_state(switch: Any) -> str:
    """``up`` when this process owns a local switch session, else ``down``.

    Mesh clients use :class:`ProxySwitch` (HTTP to the proxy) — that is ``down``
    here even though the *proxy* may have the cable. Discover shows ``link`` on
    the proxy row so operators can see a promote that never opened TCP.
    """
    if switch is None:
        return LINK_DOWN
    if bool(getattr(switch, "mesh_remote", False) or getattr(switch, "proxy_url", None)):
        return LINK_DOWN
    if bool(getattr(switch, "simulated", False)):
        return LINK_UP
    transport = getattr(switch, "_mesh_owned_transport", None)
    if transport is None:
        transport = getattr(switch, "_transport", None)
    if transport is None:
        # TesmartSwitch still present — treat as up unless proven remote.
        return LINK_UP
    sock = getattr(transport, "_sock", None)
    if sock is not None:
        try:
            # Fileno fails after close.
            sock.fileno()
            return LINK_UP
        except (OSError, ValueError):
            return LINK_DOWN
    return LINK_UP
ROLE_OFF = "off"


def default_mesh_mdns_name(instance_name: str | None) -> str:
    """Failover DNS-SD name when none is configured: ``{instance}-mesh``."""
    base = (instance_name or "").strip().replace(".", "-") or "tesmart"
    # Instance already ends with ``-mesh`` → avoid colliding with itself.
    if base.casefold().endswith("-mesh"):
        return f"{base}-alias"
    return f"{base}-mesh"


def read_mesh_mdns_name(env_file: str | None = None) -> str | None:
    raw = config.read_env_file(config.env_file_path(env_file)).get(HTTP_MESH_MDNS_NAME_KEY, "")
    text = str(raw or "").strip().replace(".", "-")
    return text or None


def apply_mesh_shared_config(bundle: dict[str, Any], env_file: str | None = None) -> dict[str, Any]:
    """Apply a proxy config bundle without overwriting this host's instance identity.

    Shared: switch host/port, names, banners, callbacks, cycle, peek, mesh flag,
    and the failover alias ``TESMART_HTTP_MESH_MDNS_NAME``.

    Local (preserved): ``TESMART_MDNS_NAME``, HTTP bind / mDNS enable, via prefs,
    and ``TESMART_HTTP_MESH_PROXY``.
    """
    parsed = parse_bundle(bundle)
    path = config.env_file_path(env_file)
    local = config.read_env_file(path)
    incoming = dict(parsed["env"])
    for key in MESH_LOCAL_ENV_KEYS:
        incoming.pop(key, None)
        if key in local:
            incoming[key] = local[key]
    # Mesh peers stay meshed; alias comes from join / ensure_mesh_mdns_name.
    incoming[HTTP_MESH_KEY] = local.get(HTTP_MESH_KEY) or incoming.get(HTTP_MESH_KEY) or "1"
    alias = (local.get(HTTP_MESH_MDNS_NAME_KEY) or incoming.get(HTTP_MESH_MDNS_NAME_KEY) or "").strip()
    if alias:
        incoming[HTTP_MESH_MDNS_NAME_KEY] = alias
    wrapped = {
        "format": parsed["format"],
        "version": parsed["version"],
        "env": incoming,
        "kit": None,
    }
    return apply_bundle(wrapped, env_file, include_kit=False, replace=True)


def write_mesh_mdns_name(name: str | None, env_file: str | None = None) -> None:
    path = config.env_file_path(env_file)
    values = config.read_env_file(path)
    text = (name or "").strip().replace(".", "-")
    if text:
        values[HTTP_MESH_MDNS_NAME_KEY] = text
    else:
        values.pop(HTTP_MESH_MDNS_NAME_KEY, None)
    config.write_env_file(path, values)

BrowseFn = Callable[[], list[DiscoveredListener]]


class MeshError(Exception):
    """Mesh join, election, or relay failed."""


def mesh_enabled(env_file: str | None = None, *, flag: bool | None = None) -> bool:
    """True when CLI ``--mesh`` or the env flag enables mesh."""
    if flag is True:
        return True
    if flag is False:
        return False
    raw = config.read_env_file(config.env_file_path(env_file)).get(HTTP_MESH_KEY, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def write_mesh_enabled(enabled: bool, env_file: str | None = None) -> None:
    path = config.env_file_path(env_file)
    values = config.read_env_file(path)
    if enabled:
        values[HTTP_MESH_KEY] = "1"
    else:
        values.pop(HTTP_MESH_KEY, None)
        values.pop(HTTP_MESH_PROXY_KEY, None)
    config.write_env_file(path, values)


def write_mesh_proxy(url: str | None, env_file: str | None = None) -> None:
    path = config.env_file_path(env_file)
    values = config.read_env_file(path)
    if url:
        values[HTTP_MESH_PROXY_KEY] = url.rstrip("/")
    else:
        values.pop(HTTP_MESH_PROXY_KEY, None)
    config.write_env_file(path, values)


def switch_id_from_settings(
    *,
    host: str | None,
    port: int | None,
    serial: str | None = None,
) -> str:
    """Normalize the switch identity used for mesh grouping.

    Serial is out of scope: raises :class:`MeshError` when a serial device is set.
    """
    if serial and str(serial).strip():
        raise MeshError(
            "mesh mode supports TCP switches only; unset TESMART_SERIAL / --serial"
        )
    text_host = (host or "").strip()
    if not text_host:
        raise MeshError("mesh mode needs TESMART_HOST (TCP switch address)")
    try:
        port_i = int(port if port is not None else config.DEFAULT_TCP_PORT)
    except (TypeError, ValueError) as exc:
        raise MeshError(f"invalid switch port for mesh: {port!r}") from exc
    return f"tcp:{text_host}:{port_i}"


def switch_id_from_env(env_file: str | None = None) -> str:
    settings = config.resolve(env_file)
    return switch_id_from_settings(
        host=settings.host,
        port=settings.port,
        serial=settings.serial,
    )


def now_ms() -> int:
    return int(time.time() * 1000)


def peer_from_listener(service: DiscoveredListener) -> dict[str, Any] | None:
    """Build a mesh peer dict from an mDNS hit, or None when not mesh-capable.

    Skips the shared mesh failover alias (``kind=mesh``) so election counts each
    process once under its instance name.
    """
    from .mdns import is_mesh_alias

    props = service.properties or {}
    if is_mesh_alias(service):
        return None
    if str(props.get("mesh") or "").strip() != "1":
        return None
    switch = str(props.get("switch") or "").strip()
    if not switch.startswith("tcp:"):
        return None
    try:
        started = int(float(props.get("started") or 0))
    except (TypeError, ValueError):
        started = 0
    role = str(props.get("role") or ROLE_CLIENT).strip().lower()
    if role not in {ROLE_PROXY, ROLE_CLIENT}:
        role = ROLE_CLIENT
    mesh_name = str(props.get("mesh_name") or "").strip() or None
    return {
        "mdns_name": service.instance_name,
        "url": service.base_url.rstrip("/"),
        "switch_id": switch,
        "started_at": started,
        "role": role,
        "version": props.get("version"),
        "panel": props.get("panel") == "1",
        "mesh_mdns_name": mesh_name,
    }


def find_mesh_alias_owner(
    services: list[DiscoveredListener],
    *,
    mesh_name: str | None,
    switch_id: str | None = None,
) -> dict[str, Any] | None:
    """Return the peer dict for whoever currently advertises the mesh failover alias.

    Used when this process fails to claim ``…-mesh``: the alias already on the
    LAN is the live proxy, even if a stale instance peer mis-advertises
    ``role=proxy``.
    """
    from .mdns import is_mesh_alias

    want = (mesh_name or "").strip()
    if not want:
        return None
    for service in services:
        if not is_mesh_alias(service):
            continue
        if service.instance_name.casefold() != want.casefold():
            continue
        props = service.properties or {}
        switch = str(props.get("switch") or "").strip()
        if switch_id and switch and not same_switch(switch, switch_id):
            continue
        try:
            started = int(float(props.get("started") or 0))
        except (TypeError, ValueError):
            started = 0
        return {
            "mdns_name": service.instance_name,
            "url": service.base_url.rstrip("/"),
            "switch_id": switch or None,
            "started_at": started,
            "role": ROLE_PROXY,
            "mesh_mdns_name": want,
            "alias": True,
        }
    return None


def same_switch(left: str, right: str) -> bool:
    return (left or "").strip().casefold() == (right or "").strip().casefold()


def peer_rank(item: dict[str, Any]) -> tuple:
    """Sort key for proxy election: oldest ``started_at`` wins.

    Missing ``started_at`` (0) sorts as *newest*, not oldest — a stale TXT
    without ``started`` must not beat a live proxy. Ties break on mDNS name,
    then base URL.
    """
    started = int(item.get("started_at") or 0)
    order = started if started > 0 else (1 << 62)
    return (
        order,
        str(item.get("mdns_name") or "").casefold(),
        str(item.get("url") or "").casefold(),
    )


def should_yield_to_proxy(
    *,
    self_started: int,
    self_name: str,
    self_url: str,
    other: dict[str, Any],
) -> bool:
    """True when another advertised proxy outranks this process by start time."""
    self_peer = {
        "mdns_name": self_name,
        "url": (self_url or "").rstrip("/"),
        "started_at": self_started,
    }
    return peer_rank(other) < peer_rank(self_peer)


def elect_proxy(peers: list[dict[str, Any]], *, self_started: int, self_name: str, self_url: str) -> dict[str, Any]:
    """Pick the mesh proxy.

    If any LAN peer already advertises ``role=proxy``, join that peer (oldest
    proxy wins ties). That is what lets a restarted listener rejoin instead of
    stealing the role. Otherwise fall back to oldest ``started_at`` including
    self (first node / orphan recovery).
    """
    self_peer = {
        "mdns_name": self_name,
        "url": self_url.rstrip("/"),
        "started_at": self_started,
        "role": ROLE_PROXY,
        "self": True,
    }

    lan_proxies = [
        peer
        for peer in peers
        if str(peer.get("role") or "").strip().lower() == ROLE_PROXY
    ]
    if lan_proxies:
        return min(lan_proxies, key=peer_rank)

    return min([*peers, self_peer], key=peer_rank)


def warn_non_mesh_same_switch(
    services: list[DiscoveredListener],
    *,
    switch_id: str,
    file=sys.stderr,
) -> list[str]:
    """Log and return instance names that look like the same switch without mesh."""
    warned = [
        service.instance_name
        for service in same_switch_standalones(services, switch_id=switch_id)
    ]
    for name in warned:
        print(
            f"warning: listener {name!r} advertises the same switch "
            f"({switch_id}) without mesh; prefer one mesh group or stop the other listener",
            file=file,
        )
    return warned


def same_switch_standalones(
    services: list[DiscoveredListener],
    *,
    switch_id: str,
) -> list[DiscoveredListener]:
    """Standalone (non-mesh) listeners that advertise the same HDMI switch."""
    from .mdns import is_mesh_alias

    hits: list[DiscoveredListener] = []
    for service in services:
        if is_mesh_alias(service):
            continue
        props = service.properties or {}
        other = str(props.get("switch") or "").strip()
        if not other or not same_switch(other, switch_id):
            continue
        if str(props.get("mesh") or "").strip() == "1":
            continue
        hits.append(service)
    return sorted(hits, key=lambda item: item.instance_name.casefold())


def live_url_is_local(live_url: str | None, peers: list[dict[str, Any]] | None = None) -> bool:
    """True when ``live_url`` points at a listener on this computer."""
    from urllib.parse import urlparse

    from .mdns import local_lan_ipv4s

    text = (live_url or "").strip().rstrip("/")
    if not text:
        return False
    try:
        parsed = urlparse(text)
    except Exception:
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    if host in {"127.0.0.1", "localhost", "::1"}:
        return True
    local_ips = {ip.casefold() for ip in local_lan_ipv4s()}
    if host.casefold() in local_ips:
        return True
    # Hostname match against browsed peers that themselves use a local IP.
    for peer in peers or []:
        url = str(peer.get("url") or "").rstrip("/")
        if not url:
            continue
        try:
            peer_host = (urlparse(url).hostname or "").strip().lower()
        except Exception:
            continue
        if peer_host.casefold() not in local_ips and peer_host not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            continue
        name = str(peer.get("mdns_name") or "").strip().casefold()
        if name and (host == name or host.startswith(f"{name}.")):
            return True
    return False


def prefer_live_peer_url(peers: list[dict[str, Any]]) -> str | None:
    """Pick which peer's ``/mesh`` to show for ``mesh status``.

    Prefer a listener on this computer (so LIVE matches where the CLI runs),
    then the advertised proxy, then any peer.
    """
    from urllib.parse import urlparse

    from .mdns import local_lan_ipv4s

    local_ips = {ip.casefold() for ip in local_lan_ipv4s()}

    def _host(url: str) -> str:
        try:
            return (urlparse(url).hostname or "").strip().lower()
        except Exception:
            return ""

    local_peers = []
    for peer in peers:
        url = str(peer.get("url") or "").rstrip("/")
        if not url:
            continue
        host = _host(url)
        if host in {"127.0.0.1", "localhost", "::1"} or host.casefold() in local_ips:
            local_peers.append(peer)
    if local_peers:
        # Prefer a local client/proxy that is not only the alias; any local works.
        preferred = next(
            (p for p in local_peers if str(p.get("role") or "") == ROLE_PROXY),
            local_peers[0],
        )
        return str(preferred.get("url") or "").rstrip("/") or None

    preferred = next((p for p in peers if p.get("role") == ROLE_PROXY), peers[0] if peers else None)
    if preferred is None:
        return None
    return str(preferred.get("url") or "").rstrip("/") or None


def format_mesh_status_text(
    *,
    switch_id: str,
    local_enabled: bool,
    services: list[DiscoveredListener],
    peers: list[dict[str, Any]],
    live: dict[str, Any] | None,
    live_url: str | None,
    browse_error: str | None = None,
    live_error: str | None = None,
    color: bool | None = None,
) -> str:
    """Human ``mesh status`` listing (MESH / conflicts / live), with optional color."""
    from datetime import datetime, timezone

    from .ansi import ansi_supported, paint
    from .mdns import group_discover_listeners

    enabled = ansi_supported() if color is None else bool(color)

    def _c(text: str, style: str | None) -> str:
        return paint(text, style, enabled=enabled)

    def _started_label(value: object) -> str:
        try:
            ms = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return "unknown start"
        if ms <= 0:
            return "unknown start"
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

    def _host_port(service: DiscoveredListener) -> str:
        host = service.hostname or (service.addresses[0] if service.addresses else "?")
        return f"{host}:{service.port}"

    live_peers = list((live or {}).get("peers") or [])
    member_count = len(live_peers) if live_peers else len(peers)
    only_member = member_count == 1
    conflicts = same_switch_standalones(services, switch_id=switch_id)

    # Prefer the live mesh alias, else TXT from any peer / alias for this switch.
    mesh_mdns = str((live or {}).get("mesh_mdns_name") or "").strip() or None
    if not mesh_mdns:
        for peer in peers:
            name = str(peer.get("mesh_mdns_name") or "").strip()
            if name:
                mesh_mdns = name
                break
    if not mesh_mdns:
        for service in services:
            props = service.properties or {}
            if not same_switch(str(props.get("switch") or ""), switch_id):
                continue
            name = str(props.get("mesh_name") or "").strip()
            if name:
                mesh_mdns = name
                break

    lines: list[str] = []

    # Verdict line.
    if not local_enabled and not peers and live is None and not conflicts:
        verdict = "Mesh: off on this computer, and no mesh listeners were found for this switch."
    elif browse_error and live is None:
        verdict = f"Mesh: could not browse the LAN ({browse_error})."
    elif live and only_member:
        name = live.get("mdns_name") or live_url or "this listener"
        verdict = (
            f"Mesh: only one listener for this switch — {name} is the proxy "
            "(no other mesh machines yet)."
        )
    elif live and live.get("role") == "proxy":
        others = max(0, member_count - 1)
        if live_url_is_local(live_url, peers):
            verdict = "Mesh: this computer is the proxy" + (
                f" with {others} other mesh machine(s)." if others else "."
            )
        else:
            name = live.get("mdns_name") or live_url or "another host"
            verdict = f"Mesh: proxy is {name}" + (
                f" with {others} other mesh machine(s)." if others else "."
            )
    elif live and live.get("role") == "client":
        if live_url_is_local(live_url, peers):
            verdict = (
                "Mesh: this computer is a mesh client; switch traffic goes through "
                f"{live.get('proxy') or 'the proxy'}."
            )
        else:
            verdict = (
                f"Mesh: viewing a mesh client ({live.get('mdns_name') or live_url}); "
                f"switch traffic goes through {live.get('proxy') or 'the proxy'}."
            )
    elif peers and only_member:
        name = peers[0].get("mdns_name") or peers[0].get("url") or "one listener"
        verdict = (
            f"Mesh: only one mesh listener found for this switch ({name}); "
            "it advertises as the proxy."
        )
    elif peers:
        verdict = f"Mesh: found {len(peers)} mesh listener(s) for this switch on the LAN."
    else:
        verdict = (
            "Mesh: "
            + ("enabled on this computer, but " if local_enabled else "")
            + "no mesh listeners are advertising for this switch yet."
        )
    if conflicts:
        verdict += f"  {_c(f'⚠ {len(conflicts)} standalone conflict(s)', 'warn')}"
    lines.append(verdict)
    lines.append("")
    lines.append(f"HDMI switch:   {_c(switch_id, 'dim')}")
    lines.append(
        "This computer: mesh is "
        + (
            _c("ON", "ok") + " (TESMART_HTTP_MESH / --mesh)"
            if local_enabled
            else _c("OFF", "dim")
        )
    )
    if mesh_mdns:
        lines.append(
            f"Failover mDNS: {_c(mesh_mdns, 'heading')}  "
            f"{_c('(pin this name for HA clients)', 'dim')}"
        )
    if only_member and (live or peers):
        lines.append("Members:       only one mesh listener")
    elif member_count:
        lines.append(f"Members:       {member_count} mesh listener(s)")

    # MESH block from browse (scoped to this switch).
    grouped = group_discover_listeners(services)
    mesh_groups = [
        group
        for group in grouped.get("mesh") or []
        if same_switch(str(group.get("switch_id") or ""), switch_id)
        or any(
            same_switch(str((m.properties or {}).get("switch") or ""), switch_id)
            for m in (group.get("members") or [])
        )
        or (
            group.get("alias") is not None
            and same_switch(
                str((group["alias"].properties or {}).get("switch") or ""),
                switch_id,
            )
        )
    ]

    lines.append("")
    if browse_error:
        lines.append(f"{_c('LAN browse', 'warn')}: failed — {browse_error}")
    elif mesh_groups or peers:
        lines.append(_c("MESH", "mesh"))
        if mesh_groups:
            for group in mesh_groups:
                name = group.get("mesh_name") or mesh_mdns or "(mesh)"
                lines.append(f"  {_c(str(name), 'heading')}  ·  {_c(switch_id, 'dim')}")
                alias = group.get("alias")
                if alias is not None:
                    lines.append(
                        f"    {_c('failover →', 'dim')} {alias.base_url:<28} {_host_port(alias)}"
                    )
                elif mesh_mdns:
                    lines.append(
                        f"    {_c('failover → (alias not advertised yet)', 'dim')}"
                    )
                members = list(group.get("members") or [])
                if not members:
                    lines.append(f"    {_c('(no instance peers visible yet)', 'dim')}")
                for item in members:
                    role = str((item.properties or {}).get("role") or "?").strip().lower()
                    if role == "proxy":
                        label, raw = _c("proxy", "proxy"), "proxy"
                    elif role == "client":
                        label, raw = _c("client", "client"), "client"
                    else:
                        label, raw = role or "?", role or "?"
                    pad = max(0, 7 - len(raw))
                    started = _started_label((item.properties or {}).get("started"))
                    lines.append(
                        f"    {label}{' ' * pad} {item.instance_name:<18} "
                        f"{item.base_url:<28} {_host_port(item)}"
                    )
                    lines.append(f"            {_c(f'started {started}', 'dim')}")
        else:
            # Browse returned peers via peer_from_listener but grouping missed them.
            for peer in sorted(
                peers,
                key=lambda item: (
                    0 if item.get("role") == "proxy" else 1,
                    str(item.get("mdns_name") or "").casefold(),
                ),
            ):
                role = str(peer.get("role") or "?")
                if role == "proxy":
                    label, raw = _c("proxy", "proxy"), "proxy"
                elif role == "client":
                    label, raw = _c("client", "client"), "client"
                else:
                    label, raw = role, role
                pad = max(0, 7 - len(raw))
                who = str(peer.get("mdns_name") or "(unnamed)")
                url = str(peer.get("url") or "—")
                lines.append(f"    {label}{' ' * pad} {who:<18} {url:<28}")
                lines.append(
                    f"            {_c('started ' + _started_label(peer.get('started_at')), 'dim')}"
                )
    else:
        lines.append(_c("MESH", "mesh"))
        lines.append(f"  {_c('(none advertising for this switch)', 'dim')}")

    if conflicts:
        lines.append("")
        lines.append(_c("CONFLICTS", "warn"))
        for item in conflicts:
            lines.append(
                f"  {_c('standalone', 'warn')} {item.instance_name:<16} "
                f"{item.base_url:<28} {_host_port(item)}"
            )
            lines.append(
                f"            {_c('same switch, mesh off — can fight over HDMI', 'warn')}"
            )

    if live_error and live is None:
        lines.append("")
        lines.append(f"{_c('LIVE', 'warn')}: could not reach the listener — {live_error}")
    elif live:
        lines.append("")
        local_live = live_url_is_local(live_url, peers)
        where = "this computer" if local_live else "remote listener"
        lines.append(f"{_c('LIVE', 'ok')}  {where} · {live_url}")
        role = str(live.get("role") or "").strip().lower()
        if role == "proxy":
            role_text = _c("proxy", "proxy") + " (owns the switch connection)"
        elif role == "client":
            role_text = _c("client", "client") + " (relays through the proxy)"
        else:
            role_text = role or "—"
        lines.append(f"  Role:         {role_text}")
        lines.append(f"  Instance:     {live.get('mdns_name') or '—'}")
        if live.get("mesh_mdns_name"):
            lines.append(
                f"  Failover mDNS: {_c(str(live.get('mesh_mdns_name')), 'heading')}"
            )
        link = str(live.get("link") or "").strip().lower()
        if link == LINK_UP:
            owner_note = (
                "this computer owns the session"
                if local_live
                else "answering listener owns the session"
            )
            lines.append(f"  Switch TCP:   {_c('up', 'proxy')}  ({owner_note})")
        elif link == LINK_DOWN:
            if role == "proxy":
                lines.append(
                    f"  Switch TCP:   {_c('down', 'warn')}  "
                    f"{_c('(proxy has no HDMI connection)', 'dim')}"
                )
            else:
                lines.append(
                    f"  Switch TCP:   {_c('down', 'dim')}  (relay — session is on the proxy)"
                )
        if live.get("proxy"):
            lines.append(f"  Proxy URL:    {live.get('proxy')}")
        lines.append(
            f"  Generation:   {live.get('generation')} "
            f"{_c('(increments when the proxy changes)', 'dim')}"
        )
        if only_member:
            lines.append(f"  Peers:        {_c('none — only member', 'dim')}")
        elif live_peers:
            lines.append("  Peers:")
            for peer in live_peers:
                who = peer.get("mdns_name") or peer.get("url") or "peer"
                peer_role = str(peer.get("role") or "?")
                if peer_role == "proxy":
                    role_bit = _c("proxy", "proxy")
                    raw = "proxy"
                elif peer_role == "client":
                    role_bit = _c("client", "client")
                    raw = "client"
                else:
                    role_bit = peer_role
                    raw = peer_role
                pad = max(0, 7 - len(raw))
                self_note = ""
                if peer.get("self"):
                    self_note = (
                        f"  {_c('(this computer)', 'dim')}"
                        if local_live
                        else f"  {_c('(answering listener)', 'dim')}"
                    )
                lines.append(f"    {role_bit}{' ' * pad} {who}{self_note}")

    return "\n".join(lines)


@dataclass
class MeshState:
    enabled: bool = False
    role: str = ROLE_OFF
    switch_id: str | None = None
    started_at: int = 0
    node_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    mdns_name: str | None = None
    mesh_mdns_name: str | None = None
    base_url: str | None = None
    proxy_url: str | None = None
    # Local HDMI TCP session: ``up`` / ``down`` (see :func:`switch_link_state`).
    link: str = LINK_DOWN
    generation: int = 1
    peers: list[dict[str, Any]] = field(default_factory=list)
    last_error: str | None = None


class MeshController:
    """Owns mesh role, peer list, join/leave, and failover watching."""

    def __init__(
        self,
        *,
        env_file: str | None,
        switch_id: str,
        started_at: int | None = None,
        mesh_mdns_name: str | None = None,
        browse_fn: BrowseFn | None = None,
        request_json: Callable[..., dict[str, Any]] | None = None,
        on_become_proxy: Callable[[], None] | None = None,
        on_become_client: Callable[[str], None] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        file=sys.stderr,
    ) -> None:
        self.env_file = env_file
        self.switch_id = switch_id
        self.started_at = int(started_at if started_at is not None else now_ms())
        self._browse = browse_fn or (lambda: browse(timeout=2.0))
        self._request_json = request_json
        self._on_become_proxy = on_become_proxy
        self._on_become_client = on_become_client
        self._on_event = on_event
        self._file = file
        self._lock = threading.RLock()
        initial_mesh_name = (mesh_mdns_name or read_mesh_mdns_name(env_file) or "").strip() or None
        self.state = MeshState(
            enabled=True,
            role=ROLE_PROXY,
            switch_id=switch_id,
            started_at=self.started_at,
            mesh_mdns_name=initial_mesh_name,
        )
        self._clients: dict[str, dict[str, Any]] = {}
        self._stop = threading.Event()
        self._watcher: threading.Thread | None = None
        self._generation = 1

    # --- snapshots -----------------------------------------------------------

    def set_switch_link(self, link: str) -> None:
        """Record whether this process currently owns HDMI TCP (for TXT + /mesh)."""
        value = LINK_UP if str(link).strip().lower() == LINK_UP else LINK_DOWN
        with self._lock:
            self.state.link = value

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            peers = [dict(item) for item in self._peer_list_locked()]
            return {
                "enabled": True,
                "role": self.state.role,
                "switch_id": self.state.switch_id,
                "started_at": self.state.started_at,
                "node_id": self.state.node_id,
                "mdns_name": self.state.mdns_name,
                "mesh_mdns_name": self.state.mesh_mdns_name,
                "base_url": self.state.base_url,
                "proxy": self.state.proxy_url if self.state.role == ROLE_CLIENT else None,
                "link": self.state.link,
                "generation": self._generation,
                "peers": peers,
                "last_error": self.state.last_error,
            }

    def _peer_list_locked(self) -> list[dict[str, Any]]:
        """Joined clients + self, enriched with mDNS ``started_at`` / role from browse."""
        by_url: dict[str, dict[str, Any]] = {}

        def _put(entry: dict[str, Any]) -> None:
            url = str(entry.get("url") or "").rstrip("/")
            if not url:
                # Keep self / incomplete rows that only have a name.
                key = f"name:{(entry.get('mdns_name') or entry.get('node_id') or id(entry))}"
                by_url[key] = dict(entry)
                return
            existing = by_url.get(url)
            if existing is None:
                by_url[url] = dict(entry)
                return
            merged = dict(existing)
            for key, value in entry.items():
                if value is None or value == "":
                    continue
                if key == "started_at":
                    try:
                        incoming = int(value)
                    except (TypeError, ValueError):
                        continue
                    if incoming > 0:
                        merged["started_at"] = incoming
                    continue
                if key == "self":
                    merged["self"] = bool(existing.get("self")) or bool(value)
                    continue
                merged[key] = value
            by_url[url] = merged

        for peer in self.state.peers:
            _put({**peer, "self": False})
        for peer in self._clients.values():
            _put({**peer, "role": peer.get("role") or ROLE_CLIENT, "self": False})

        if self.state.role == ROLE_PROXY and self.state.base_url:
            _put(
                {
                    "mdns_name": self.state.mdns_name,
                    "url": self.state.base_url,
                    "role": ROLE_PROXY,
                    "started_at": self.state.started_at,
                    "node_id": self.state.node_id,
                    "self": True,
                }
            )
        elif self.state.role == ROLE_CLIENT:
            if self.state.proxy_url:
                _put(
                    {
                        "url": self.state.proxy_url,
                        "role": ROLE_PROXY,
                        "self": False,
                    }
                )
            if self.state.base_url:
                _put(
                    {
                        "mdns_name": self.state.mdns_name,
                        "url": self.state.base_url,
                        "role": ROLE_CLIENT,
                        "started_at": self.state.started_at,
                        "node_id": self.state.node_id,
                        "self": True,
                    }
                )

        peers = list(by_url.values())
        peers.sort(
            key=lambda item: (
                0 if item.get("self") else 1,
                0 if str(item.get("role") or "") == ROLE_PROXY else 1,
                str(item.get("mdns_name") or item.get("url") or "").casefold(),
            )
        )
        return peers

    def publish_event(self, kind: str, **extra: Any) -> None:
        payload = {"event": "mesh", "kind": kind, "mesh": self.snapshot(), **extra}
        if self._on_event is not None:
            try:
                self._on_event(payload)
            except Exception:
                pass

    # --- discovery / election ------------------------------------------------

    def discover(self) -> list[dict[str, Any]]:
        try:
            services = self._browse()
        except MdnsError as exc:
            with self._lock:
                self.state.last_error = str(exc)
            print(f"warning: mesh browse failed: {exc}", file=self._file)
            return []
        warn_non_mesh_same_switch(services, switch_id=self.switch_id, file=self._file)
        peers: list[dict[str, Any]] = []
        for service in services:
            peer = peer_from_listener(service)
            if peer is None:
                continue
            if not same_switch(peer["switch_id"], self.switch_id):
                continue
            # Skip ourselves when the advertised URL matches.
            if self.state.base_url and peer["url"].rstrip("/") == self.state.base_url.rstrip("/"):
                continue
            if self.state.mdns_name and peer["mdns_name"].casefold() == self.state.mdns_name.casefold():
                continue
            peers.append(peer)
        with self._lock:
            self.state.peers = peers
        return peers

    def set_identity(self, *, mdns_name: str | None, base_url: str) -> None:
        with self._lock:
            self.state.mdns_name = mdns_name
            self.state.base_url = base_url.rstrip("/")
        # First proxy with no configured alias defaults to ``{instance}-mesh``.
        if self.state.role == ROLE_PROXY:
            self.ensure_mesh_mdns_name()

    def ensure_mesh_mdns_name(self, preferred: str | None = None) -> str | None:
        """Resolve and persist the shared failover DNS-SD name for this mesh.

        Returns ``None`` until an instance name (or env / join override) is known
        so startup does not permanently store a placeholder like ``tesmart-mesh``.
        """
        with self._lock:
            existing = (preferred or "").strip().replace(".", "-") or None
            if not existing:
                existing = (self.state.mesh_mdns_name or "").strip().replace(".", "-") or None
            if not existing:
                existing = read_mesh_mdns_name(self.env_file)
            if not existing:
                if not self.state.mdns_name:
                    return None
                existing = default_mesh_mdns_name(self.state.mdns_name)
            # Never collide with this process's instance name.
            if (
                self.state.mdns_name
                and existing.casefold() == self.state.mdns_name.casefold()
            ):
                existing = default_mesh_mdns_name(self.state.mdns_name)
            self.state.mesh_mdns_name = existing
        write_mesh_mdns_name(existing, self.env_file)
        return existing

    def _alias_url_is_self(self, url: str) -> bool:
        """True when the mesh alias resolves to this process (URL or node_id)."""
        want = (url or "").rstrip("/")
        if not want:
            return False
        with self._lock:
            self_url = (self.state.base_url or "").rstrip("/")
            node_id = self.state.node_id
        if self_url and want == self_url:
            return True
        try:
            payload = self._http(want, "/mesh")
        except MeshError:
            return False
        return bool(node_id) and str(payload.get("node_id") or "") == node_id

    def find_live_proxy_url(self) -> str | None:
        """Return a live proxy URL if one already owns this switch on the LAN.

        Prefers the shared ``…-mesh`` failover alias (authoritative), then any
        instance peer advertising ``role=proxy``. Used at startup **before**
        opening TCP or claiming the alias.
        """
        with self._lock:
            mesh_name = (self.state.mesh_mdns_name or "").strip() or None
            self_url = (self.state.base_url or "").rstrip("/")
        if not mesh_name:
            mesh_name = read_mesh_mdns_name(self.env_file)
        try:
            services = self._browse()
        except MdnsError as exc:
            print(f"warning: mesh browse failed: {exc}", file=self._file)
            services = []

        if mesh_name:
            owner = find_mesh_alias_owner(
                services, mesh_name=mesh_name, switch_id=self.switch_id
            )
            if owner is not None:
                url = str(owner.get("url") or "").rstrip("/")
                if url and not self._alias_url_is_self(url):
                    if self._proxy_alive(url):
                        return url
                    print(
                        f"  mesh: failover alias {mesh_name} points at unreachable "
                        f"{url}; ignoring stale alias",
                        file=self._file,
                    )

        peers = []
        for service in services:
            peer = peer_from_listener(service)
            if peer is None:
                continue
            if not same_switch(peer["switch_id"], self.switch_id):
                continue
            if self_url and peer["url"].rstrip("/") == self_url:
                continue
            if self.state.mdns_name and peer["mdns_name"].casefold() == self.state.mdns_name.casefold():
                continue
            peers.append(peer)
        with self._lock:
            self.state.peers = peers

        lan_proxies = [
            peer
            for peer in peers
            if str(peer.get("role") or "").strip().lower() == ROLE_PROXY
            and str(peer.get("url") or "").rstrip("/")
        ]
        if not lan_proxies:
            return None
        best = min(lan_proxies, key=peer_rank)
        url = str(best.get("url") or "").rstrip("/")
        if not url or (self_url and url == self_url):
            return None
        if not self._proxy_alive(url):
            return None
        return url

    def try_join_alias_owner(self) -> bool:
        """If the shared ``…-mesh`` alias is held by another live host, join it.

        Returns ``True`` after demoting to client. A stale alias pointing at a
        dead host returns ``False`` so this node can keep (or take) proxy.
        """
        url = self.find_live_proxy_url()
        if not url:
            return False
        # find_live_proxy_url may have returned a role=proxy peer; prefer logging
        # the alias path when that is what we will join.
        with self._lock:
            mesh_name = (self.state.mesh_mdns_name or "").strip() or None
        if not mesh_name:
            mesh_name = read_mesh_mdns_name(self.env_file)
        print(
            f"  mesh: live proxy already on LAN at {url}"
            + (f" (alias {mesh_name})" if mesh_name else "")
            + "; joining",
            file=self._file,
        )
        try:
            self._join(url)
            return True
        except MeshError as exc:
            print(
                f"  mesh: join live proxy {url} failed ({exc})",
                file=self._file,
            )
            return False

    def handle_alias_claim_failed(self) -> bool:
        """Proxy could not register the mesh alias — join whoever already holds it.

        Returns ``True`` when this node demoted to client.
        """
        with self._lock:
            if self.state.role != ROLE_PROXY:
                return False
        if self.try_join_alias_owner():
            return True
        print(
            "  mesh: mesh alias not registered yet; staying proxy and will retry",
            file=self._file,
        )
        return False

    def bootstrap(self) -> str:
        """Elect role. Returns ``proxy`` or ``client``."""
        # Prefer the live failover alias over a stale role=proxy instance peer.
        if self.try_join_alias_owner():
            return ROLE_CLIENT
        peers = self.discover()
        winner = elect_proxy(
            peers,
            self_started=self.started_at,
            self_name=self.state.mdns_name or self.state.node_id,
            self_url=self.state.base_url or f"node:{self.state.node_id}",
        )
        if winner.get("self"):
            self._become_proxy(reason="elected")
            return ROLE_PROXY
        proxy_url = str(winner.get("url") or "").rstrip("/")
        if not proxy_url:
            self._become_proxy(reason="no-proxy-url")
            return ROLE_PROXY
        try:
            self._join(proxy_url)
            return ROLE_CLIENT
        except MeshError as exc:
            print(f"warning: mesh join failed ({exc})", file=self._file)
            if self.try_join_alias_owner():
                return ROLE_CLIENT
            print("  mesh: no live alias owner; becoming proxy", file=self._file)
            self._become_proxy(reason="join-failed")
            return ROLE_PROXY

    def _become_proxy(self, *, reason: str) -> None:
        with self._lock:
            self.state.role = ROLE_PROXY
            self.state.proxy_url = None
            self.state.last_error = None
            self._generation += 1
            generation = self._generation
        write_mesh_proxy(None, self.env_file)
        mesh_name = self.ensure_mesh_mdns_name()
        alias = f" mesh_mdns={mesh_name}" if mesh_name else ""
        print(
            f"  mesh: role=proxy generation={generation}{alias} ({reason})",
            file=self._file,
        )
        if self._on_become_proxy is not None:
            self._on_become_proxy()
        self.publish_event("role", reason=reason)

    def _become_client(self, proxy_url: str, *, reason: str) -> None:
        with self._lock:
            self.state.role = ROLE_CLIENT
            self.state.proxy_url = proxy_url.rstrip("/")
            self.state.last_error = None
        write_mesh_proxy(proxy_url, self.env_file)
        print(f"  mesh: role=client proxy={proxy_url} ({reason})", file=self._file)
        if self._on_become_client is not None:
            self._on_become_client(proxy_url)
        self.publish_event("role", reason=reason)

    def _http(self, base: str, path: str, *, method: str = "GET", body: dict | None = None) -> dict[str, Any]:
        # request_json raises SystemExit for CLI use. That must not kill the mesh
        # watcher thread — otherwise proxy death ends health checks forever and a
        # failed promote never retries opening TCP.
        try:
            if self._request_json is not None:
                return self._request_json(base, path, method=method, body=body)
            from .via_client import request_json

            return request_json(base, path, method=method, body=body, timeout=8.0)
        except SystemExit as exc:
            raise MeshError(str(exc) or "mesh http failed") from exc
        except MeshError:
            raise
        except Exception as exc:
            raise MeshError(f"mesh http failed: {exc}") from exc

    def _join(self, proxy_url: str) -> None:
        body = {
            "switch_id": self.switch_id,
            "started_at": self.started_at,
            "mdns_name": self.state.mdns_name,
            "base_url": self.state.base_url,
            "node_id": self.state.node_id,
            "version": package_version(),
        }
        try:
            reply = self._http(proxy_url, "/mesh/join", method="POST", body=body)
        except Exception as exc:
            raise MeshError(str(exc)) from exc
        if reply.get("error"):
            raise MeshError(str(reply["error"]))
        if reply.get("accepted") is False:
            raise MeshError(reply.get("message") or "join rejected")
        # Adopt the group's shared failover DNS-SD name from the proxy.
        learned = (
            str(reply.get("mesh_mdns_name") or "").strip()
            or str((reply.get("mesh") or {}).get("mesh_mdns_name") or "").strip()
        )
        if learned:
            self.ensure_mesh_mdns_name(learned)
        # Pull shared switch/panel config; keep this host's instance identity.
        try:
            config_payload = self._http(proxy_url, "/api/config")
            bundle = config_payload.get("bundle") or config_payload
            parsed = parse_bundle(bundle)
            apply_mesh_shared_config(parsed, self.env_file)
        except Exception as exc:
            print(f"warning: mesh config pull failed: {exc}", file=self._file)
        self._become_client(proxy_url, reason="joined")
        if reply.get("generation") is not None:
            with self._lock:
                try:
                    self._generation = int(reply["generation"])
                except (TypeError, ValueError):
                    pass

    # --- proxy-side join / leave ---------------------------------------------

    def handle_join(self, body: dict[str, Any]) -> dict[str, Any]:
        switch = str(body.get("switch_id") or "").strip()
        if not same_switch(switch, self.switch_id):
            return {
                "accepted": False,
                "message": f"switch_id mismatch (want {self.switch_id})",
            }
        with self._lock:
            if self.state.role != ROLE_PROXY:
                return {
                    "accepted": False,
                    "message": "this node is not the mesh proxy",
                    "proxy": self.state.proxy_url,
                }
            node_id = str(body.get("node_id") or uuid.uuid4().hex[:12])
            url = str(body.get("base_url") or "").rstrip("/")
            entry = {
                "node_id": node_id,
                "mdns_name": body.get("mdns_name"),
                "url": url or None,
                "started_at": int(body.get("started_at") or 0),
                "role": ROLE_CLIENT,
                "version": body.get("version"),
                "last_seen": now_ms(),
            }
            self._clients[node_id] = entry
            generation = self._generation
            digest = summarize_bundle(export_bundle(self.env_file, include_kit=False))
        mesh_name = self.ensure_mesh_mdns_name()
        self.publish_event("join", peer=entry)
        return {
            "accepted": True,
            "role": ROLE_PROXY,
            "generation": generation,
            "switch_id": self.switch_id,
            "proxy": self.state.base_url,
            "mesh_mdns_name": mesh_name,
            "config_digest": digest,
            "mesh": self.snapshot(),
        }

    def handle_leave(self, body: dict[str, Any]) -> dict[str, Any]:
        node_id = str(body.get("node_id") or "").strip()
        with self._lock:
            removed = self._clients.pop(node_id, None) if node_id else None
            if removed is None and body.get("base_url"):
                url = str(body.get("base_url")).rstrip("/")
                for key, peer in list(self._clients.items()):
                    if (peer.get("url") or "").rstrip("/") == url:
                        removed = self._clients.pop(key, None)
                        break
        if removed:
            self.publish_event("leave", peer=removed)
        return {"ok": True, "removed": bool(removed), "mesh": self.snapshot()}

    def handle_proxy_leaving(self, body: dict[str, Any]) -> dict[str, Any]:
        """Proxy pushed that it is shutting down — elect immediately (do not wait for the watcher)."""
        switch = str(body.get("switch_id") or "").strip()
        if switch and not same_switch(switch, self.switch_id):
            return {
                "ok": False,
                "message": f"switch_id mismatch (want {self.switch_id})",
            }
        with self._lock:
            role = self.state.role
        if role != ROLE_CLIENT:
            return {"ok": True, "ignored": True, "role": role, "mesh": self.snapshot()}
        print(
            "  mesh: proxy announced it is leaving; electing now",
            file=self._file,
        )
        self.publish_event("proxy_leaving", source=body.get("from") or body.get("base_url"))
        new_role = self.bootstrap()
        if new_role == ROLE_PROXY:
            print("  mesh: this node won the election and is now the proxy", file=self._file)
        else:
            with self._lock:
                print(f"  mesh: joined new proxy {self.state.proxy_url}", file=self._file)
        return {"ok": True, "role": new_role, "mesh": self.snapshot()}

    def _client_notify_urls(self) -> list[str]:
        """URLs of mesh clients that should hear a proxy resign (join table + mDNS)."""
        urls: set[str] = set()
        with self._lock:
            self_url = (self.state.base_url or "").rstrip("/")
            for peer in self._clients.values():
                url = (peer.get("url") or "").rstrip("/")
                if url and url != self_url:
                    urls.add(url)
        for peer in self.discover():
            if str(peer.get("role") or "") != ROLE_CLIENT:
                continue
            url = (peer.get("url") or "").rstrip("/")
            if url and url != self_url:
                urls.add(url)
        return sorted(urls)

    def notify_clients_proxy_leaving(self) -> list[str]:
        """Push resign to every known mesh client. Returns URLs that were notified."""
        with self._lock:
            payload = {
                "switch_id": self.switch_id,
                "reason": "proxy_resigning",
                "from": self.state.base_url,
                "base_url": self.state.base_url,
                "mdns_name": self.state.mdns_name,
                "node_id": self.state.node_id,
                "started_at": self.state.started_at,
            }
        notified: list[str] = []
        for url in self._client_notify_urls():
            try:
                self._http(url, "/mesh/proxy-leaving", method="POST", body=payload)
                notified.append(url)
            except Exception as exc:
                print(
                    f"warning: mesh could not push resign to {url}: {exc}",
                    file=self._file,
                )
        return notified

    def resign(self) -> dict[str, Any]:
        """Graceful leave so another peer can become proxy.

        Clients notify the proxy via ``/mesh/leave``. A resigning proxy
        **pushes** ``POST /mesh/proxy-leaving`` to known clients so they elect
        immediately; the watcher health check remains as a fallback.
        """
        with self._lock:
            role = self.state.role
            proxy = self.state.proxy_url
            payload = {
                "node_id": self.state.node_id,
                "base_url": self.state.base_url,
                "mdns_name": self.state.mdns_name,
                "switch_id": self.switch_id,
            }
        notified: list[str] = []
        if role == ROLE_CLIENT and proxy:
            try:
                self._http(proxy, "/mesh/leave", method="POST", body=payload)
            except Exception as exc:
                print(f"warning: mesh leave notify failed: {exc}", file=self._file)
        elif role == ROLE_PROXY:
            notified = self.notify_clients_proxy_leaving()
            if notified:
                print(
                    f"  mesh: proxy resigning — notified {len(notified)} client(s) to elect now",
                    file=self._file,
                )
            else:
                print(
                    "  mesh: proxy resigning — no clients to notify "
                    "(any survivors will still re-elect via the health check)",
                    file=self._file,
                )
        self.publish_event("leave", reason="resign", notified=notified)
        return {"ok": True, "notified": notified, "mesh": self.snapshot()}

    # --- failover watcher ----------------------------------------------------

    def start_watcher(self, *, interval: float = 3.0) -> None:
        if self._watcher is not None:
            return

        def loop() -> None:
            while not self._stop.wait(interval):
                try:
                    self.tick()
                except Exception as exc:  # noqa: BLE001
                    print(f"warning: mesh watcher: {exc}", file=self._file)

        self._watcher = threading.Thread(target=loop, name="tesmart-mesh", daemon=True)
        self._watcher.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.resign()
        except Exception:
            pass

    def tick(self) -> None:
        with self._lock:
            role = self.state.role
            proxy = self.state.proxy_url
        if role == ROLE_CLIENT:
            peers = self.discover()
            lan_has_proxy = any(str(p.get("role") or "") == ROLE_PROXY for p in peers)
            remembered_dead = not proxy or not self._proxy_alive(proxy)
            # Orphan mesh: every visible peer is also a client (failover left nobody
            # owning the switch). Re-elect even if a stale proxy URL still answers HTTP.
            orphaned = bool(peers) and not lan_has_proxy
            if remembered_dead or orphaned:
                why = "proxy unreachable" if remembered_dead else "no proxy on LAN (all clients)"
                print(f"  mesh: {why}; running election", file=self._file)
                role = self.bootstrap()
                if role == ROLE_PROXY:
                    print("  mesh: this node won the election and is now the proxy", file=self._file)
                else:
                    with self._lock:
                        print(
                            f"  mesh: joined new proxy {self.state.proxy_url}",
                            file=self._file,
                        )
            return
        # Proxy: if the shared …-mesh alias is already held by another live host,
        # we lost the claim — join that owner (avoids dual proxies after a bad promote).
        if self.try_join_alias_owner():
            return
        peers = self.discover()
        # Fallback: another instance still advertises role=proxy; older started_at wins.
        other_proxies = [
            peer
            for peer in peers
            if str(peer.get("role") or "").strip().lower() == ROLE_PROXY
            and str(peer.get("url") or "").rstrip("/")
        ]
        if other_proxies:
            best = min(other_proxies, key=peer_rank)
            with self._lock:
                self_name = self.state.mdns_name or self.state.node_id
                self_url = self.state.base_url or f"node:{self.state.node_id}"
                self_started = self.state.started_at
            if should_yield_to_proxy(
                self_started=self_started,
                self_name=self_name,
                self_url=self_url,
                other=best,
            ):
                other_url = str(best.get("url") or "").rstrip("/")
                other_name = best.get("mdns_name") or other_url
                print(
                    f"  mesh: another proxy {other_name} started earlier "
                    f"(started_at={best.get('started_at')}); yielding",
                    file=self._file,
                )
                try:
                    self._join(other_url)
                    return
                except MeshError as exc:
                    print(
                        f"  mesh: yield to {other_url} failed ({exc}); staying proxy",
                        file=self._file,
                    )
        with self._lock:
            # Drop stale joined clients (no heartbeat for 60s) if they also vanished from mDNS.
            now = now_ms()
            live_urls = {p["url"].rstrip("/") for p in peers}
            for key, peer in list(self._clients.items()):
                last = int(peer.get("last_seen") or 0)
                url = (peer.get("url") or "").rstrip("/")
                if last and now - last > 60_000 and url and url not in live_urls:
                    self._clients.pop(key, None)

    def _proxy_alive(self, proxy_url: str) -> bool:
        try:
            payload = self._http(proxy_url, "/mesh")
        except Exception:
            return False
        if payload.get("error"):
            return False
        if str(payload.get("role") or "") != ROLE_PROXY:
            return False
        if not same_switch(str(payload.get("switch_id") or ""), self.switch_id):
            return False
        return True

    def touch_client(self, node_id: str | None) -> None:
        if not node_id:
            return
        with self._lock:
            peer = self._clients.get(node_id)
            if peer is not None:
                peer["last_seen"] = now_ms()


def client_proxy_url(server: Any) -> str | None:
    """Base URL of the mesh proxy when ``server`` is a mesh client, else ``None``."""
    mesh = getattr(server, "mesh", None)
    if mesh is None:
        return None
    try:
        snap = mesh.snapshot()
    except Exception:
        return None
    if str(snap.get("role") or "") != ROLE_CLIENT:
        return None
    url = (snap.get("proxy") or getattr(getattr(server, "switch", None), "proxy_url", None) or "").strip()
    return url.rstrip("/") or None


def forward_to_proxy(
    server: Any,
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """HTTP JSON call to this mesh client's proxy. Raises :class:`MeshError` when not a client."""
    base = client_proxy_url(server)
    if not base:
        raise MeshError("not a mesh client")
    from .via_client import request_json

    try:
        return request_json(base, path, method=method, body=body, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        raise MeshError(f"mesh proxy request failed: {exc}") from exc


def sync_shared_state_from_proxy(server: Any, state: dict[str, Any]) -> None:
    """Copy proxy-owned peek-follow / banner / active into this client's memory."""
    peek = state.get("peek") if isinstance(state, dict) else None
    if isinstance(peek, dict):
        follow = peek.get("follow")
        if follow in {"off", "once", "always"}:
            seconds = peek.get("seconds")
            try:
                secs = int(seconds) if follow != "off" and seconds is not None else None
            except (TypeError, ValueError):
                secs = None
            try:
                server.peek_follow.set_mode(str(follow), secs)
            except Exception:
                pass
    hub = getattr(server, "callbacks", None)
    if hub is not None and isinstance(state, dict):
        active = state.get("active_input")
        try:
            number = int(active) if active is not None else None
        except (TypeError, ValueError):
            number = None
        if number is not None:
            try:
                hub._last_active = number
            except Exception:
                pass
    board = getattr(server, "banner", None)
    if board is not None and isinstance(state, dict) and "banner_history" in state:
        history = state.get("banner_history")
        if isinstance(history, list):
            try:
                board.replace_history(history)
            except Exception:
                pass


def mesh_txt_properties(
    *,
    enabled: bool,
    switch_id: str | None,
    started_at: int | None,
    role: str | None,
    mesh_name: str | None = None,
    kind: str | None = None,
) -> dict[str, str]:
    """Extra TXT keys when mesh is on."""
    if not enabled or not switch_id:
        return {}
    out = {
        "mesh": "1",
        "switch": switch_id,
        "started": str(int(started_at or now_ms())),
        "role": (role or ROLE_PROXY),
    }
    if mesh_name:
        out["mesh_name"] = str(mesh_name)
    if kind:
        out["kind"] = str(kind)
    return out


class ProxySwitch:
    """TesmartSwitch-shaped facade that talks to the mesh proxy over HTTP.

    Mesh clients use this instead of a TCP session. There is no local serial/
    TCP receive path, so :meth:`_pump` / :meth:`drain_unsolicited` are empty
    no-ops — Hubitat callbacks and unsolicited input watching stay on the proxy.
    """

    def __init__(self, proxy_url: str, *, input_count: int, endpoint: str) -> None:
        self.proxy_url = proxy_url.rstrip("/")
        self.input_count = input_count
        self._endpoint = endpoint
        self.simulated = False
        # CallbackHub / peeker treat this as a remote facade (no local transport).
        self.mesh_remote = True

    def close(self) -> None:
        return None

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def _pump(self, timeout: float = 0.2) -> list:
        """No local transport — unsolicited frames are watched on the proxy."""
        return []

    def _get(self, path: str) -> dict[str, Any]:
        return self._request(path)

    def _post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request(path, method="POST", body=body or {})

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from .via_client import request_json

        # request_json raises SystemExit for CLI use; mesh must not abort the
        # listener process (or a failover rebind) when the proxy is briefly down.
        try:
            return request_json(
                self.proxy_url, path, method=method, body=body, timeout=10.0
            )
        except SystemExit as exc:
            raise SwitchError(str(exc) or "mesh proxy request failed") from exc
        except Exception as exc:
            raise SwitchError(f"mesh proxy request failed: {exc}") from exc

    def get_active_input(self, retries: int = 2) -> int:
        data = self._get("/input")
        value = data.get("active_input")
        if value is None:
            raise SwitchError("proxy did not return active_input")
        return int(value)

    def set_input(self, number: int, verify: bool = True) -> int | None:
        data = self._get(f"/input/{int(number)}")
        reported = data.get("active_input", number)
        return int(reported) if reported is not None else int(number)

    def rotate(self, only=None, *, direction: int = 1) -> tuple[int, int]:
        if direction < 0:
            data = self._get("/previous")
        else:
            data = self._get("/next")
        previous = data.get("previous")
        active = data.get("active_input")
        if active is None:
            raise SwitchError("proxy rotate failed")
        return (int(previous) if previous is not None else int(active), int(active))

    def set_buzzer(self, enabled: bool) -> None:
        self._post("/api/buzzer", {"on": bool(enabled)})

    def set_led_timeout(self, timeout) -> None:
        described = getattr(timeout, "describe", None)
        value = described() if callable(described) else str(timeout)
        self._post("/api/led", {"timeout": value})

    def set_auto_detect(self, enabled: bool) -> None:
        self._post("/api/autodetect", {"on": bool(enabled)})

    def get_network_config(self):
        from .switch import NetworkConfig

        data = self._get("/api/network")
        net = data.get("network") or {}
        return NetworkConfig(
            ip=net.get("ip"),
            port=net.get("port"),
            gateway=net.get("gateway"),
            mask=net.get("mask"),
        )

    def write_network_config(self, **values: str):
        from .switch import NetworkWriteResult

        body = dict(values)
        body["confirm"] = True
        body["accept_risk"] = True
        data = self._post("/api/network", body)
        stored = data.get("stored") or {}
        requested = {k: values[k] for k in ("ip", "port", "gateway", "mask") if k in values}
        acknowledged = {key: True for key in requested}
        verified = {
            key: str(stored.get(key) or "") == str(values[key]) for key in requested
        }
        return NetworkWriteResult(
            requested=requested,
            acknowledged=acknowledged,
            stored={
                "ip": stored.get("ip"),
                "port": stored.get("port"),
                "gateway": stored.get("gateway"),
                "mask": stored.get("mask"),
            },
            verified=verified,
        )

    def read_status(self, include_network: bool = True):
        from .switch import NetworkConfig, SwitchStatus

        path = "/status" if include_network else "/status?network=0"
        try:
            data = self._get(path)
        except Exception:
            active = self.get_active_input()
            return SwitchStatus(
                endpoint=self._endpoint,
                active_input=active,
                input_count=self.input_count,
            )
        active = data.get("active_input")
        if active is None:
            raise SwitchError("proxy status missing active_input")
        network = None
        raw_net = data.get("network")
        if isinstance(raw_net, dict):
            network = NetworkConfig(
                ip=raw_net.get("ip"),
                port=raw_net.get("port"),
                gateway=raw_net.get("gateway"),
                mask=raw_net.get("mask"),
            )
        return SwitchStatus(
            endpoint=str(data.get("endpoint") or self._endpoint),
            active_input=int(active),
            input_count=int(data.get("input_count") or self.input_count),
            network=network,
        )

    def drain_unsolicited(self) -> list[int]:
        return []
