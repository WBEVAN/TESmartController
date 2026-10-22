"""HTTP GET/POST that can still reach a LAN listener when the default route cannot.

A browser and ``ping`` can succeed while this process gets ``EHOSTUNREACH``
("No route to host"). Two common causes: the default route is a VPN with no
path to that subnet, and (on macOS) Local Network privacy denying the kit
app or Python. The first attempt uses the normal route. If that is
unreachable, the same host is tried from each non-tunnel local IPv4.
"""

from __future__ import annotations

import errno
import http.client
import socket
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

# EHOSTUNREACH is 65 on macOS and 113 on Linux. Windows reports 10065.
# ENETUNREACH is the same symptom (network unreachable).
_UNREACHABLE = {
    errno.EHOSTUNREACH,
    errno.ENETUNREACH,
    65,
    113,
    51,
    101,
    10065,
    10051,
}

_HINTED = False


class _BytesResponse:
    """Minimal urlopen result for a retry that already read the body."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _BytesResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def host_unreachable(exc: BaseException) -> bool:
    """True when ``exc`` is the OS saying this process has no route to the host."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for attr in ("errno", "winerror"):
            code = getattr(current, attr, None)
            if isinstance(code, int) and code in _UNREACHABLE:
                return True
        reason = getattr(current, "reason", None)
        current = reason if isinstance(reason, BaseException) else None
    return False


def open_url(request: urllib.request.Request, *, timeout: float):  # type: ignore[no-untyped-def]
    """GET or POST ``request``. Retry from each LAN address on no-route."""
    try:
        return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310
    except urllib.error.URLError as exc:
        if not host_unreachable(exc):
            raise
        return _open_from_lan(request, timeout=timeout, first=exc)


def _open_from_lan(request: urllib.request.Request, *, timeout: float, first: BaseException):  # type: ignore[no-untyped-def]
    from .debug import log
    from .mdns import local_lan_ipv4s

    parts = urlsplit(request.full_url)
    if parts.scheme not in {"http", ""}:
        raise first
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    targets = _ipv4_targets(host)
    sources = [ip for ip in local_lan_ipv4s() if ip not in targets]
    log(f"no route to {host}:{port} via the default route; retrying from {sources or 'nowhere'}")
    last: BaseException = first
    headers = {key: value for key, value in request.header_items()}
    data = request.data
    method = request.get_method()
    for dest in targets:
        for src in sources:
            log(f"retry {dest}:{port} from {src}")
            try:
                status, body = _exchange(
                    dest,
                    port,
                    path,
                    method=method,
                    data=data,
                    headers=headers,
                    timeout=timeout,
                    source=src,
                )
            except OSError as exc:
                last = urllib.error.URLError(exc)
                continue
            if status >= 400:
                raise urllib.error.HTTPError(
                    request.full_url,
                    status,
                    f"HTTP {status}",
                    hdrs=None,
                    fp=None,
                )
            log(f"reached {dest}:{port} from {src}")
            return _BytesResponse(status, body)
    _hint_once(host, port, sources)
    raise last


def _ipv4_targets(host: str) -> list[str]:
    text = (host or "").strip()
    if not text:
        return []
    try:
        socket.inet_aton(text)
    except OSError:
        pass
    else:
        return [text]
    found: list[str] = []
    try:
        for info in socket.getaddrinfo(text, None, socket.AF_INET, socket.SOCK_STREAM):
            ip = info[4][0]
            if ip not in found:
                found.append(ip)
    except OSError:
        return []
    return found


def _exchange(
    host: str,
    port: int,
    path: str,
    *,
    method: str,
    data: bytes | None,
    headers: dict[str, str],
    timeout: float,
    source: str,
) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection(
        host,
        port,
        timeout=timeout,
        source_address=(source, 0),
    )
    try:
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        return int(response.status), response.read()
    finally:
        conn.close()


def _hint_once(host: str, port: int, sources: list[str]) -> None:
    global _HINTED
    if _HINTED:
        return
    _HINTED = True
    where = f"{host}:{port}" if host else "the listener"
    if sys.platform == "darwin":
        print(
            f"No route to {where}. macOS is blocking this app's LAN connection. "
            "Allow TESmart Kit in System Settings → Privacy & Security → Local Network, "
            "then relaunch the kit.",
            file=sys.stderr,
        )
        return
    tried = ", ".join(sources) if sources else "no LAN address"
    print(
        f"No route to {where} from the default route or from {tried}.",
        file=sys.stderr,
    )
