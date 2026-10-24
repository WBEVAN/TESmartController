"""Package version and client/server compatibility checks.

``tesmartctl`` versions are dotted integers (for example ``0.0.7.9``). Clients
such as Hubitat can ask ``GET /version`` or ``POST /compat`` with a minimum
server version and optional feature list; the reply says whether this process
meets the request.
"""

from __future__ import annotations

from typing import Any

# Keep in sync with ``pyproject.toml`` ``[project].version`` (fallback when the
# package metadata is unavailable, e.g. running from a raw source tree).
VERSION = "0.0.7.15"
PACKAGE_NAME = "tesmartctl"

# Capability tags clients may require. Add new ones when introducing a
# contract Hubitat (or others) should gate on.
FEATURES: tuple[str, ...] = (
    "status",
    "names",
    "callbacks",
    "callbacks.name",
    "callbacks.health",
    "callbacks.timeout",
    "names_changed",
    "input_changed",
    "banner",
    "sse",
    "last_sent",
    "panel",
    "compat",
    "version",
    "mdns",
)


def package_version() -> str:
    """Version this code advertises for ``/version`` and ``/compat``.

    Always uses :data:`VERSION` (kept in sync with ``pyproject.toml``) so a
    stale site-packages install cannot under-report features that this source
    tree actually implements.
    """
    return VERSION


def parse_version(text: str | None) -> tuple[int, ...]:
    """Parse ``1.2.3`` / ``v0.0.7.9`` into a comparable integer tuple."""
    if text is None:
        return ()
    raw = str(text).strip()
    if raw.lower().startswith("v"):
        raw = raw[1:]
    if not raw:
        return ()
    parts: list[int] = []
    for chunk in raw.split("."):
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def version_at_least(have: str | None, need: str | None) -> bool:
    """True when ``have`` is greater than or equal to ``need`` (component-wise)."""
    if not need:
        return True
    left = parse_version(have)
    right = parse_version(need)
    if not right:
        return True
    if not left:
        return False
    width = max(len(left), len(right))
    left = left + (0,) * (width - len(left))
    right = right + (0,) * (width - len(right))
    return left >= right


def version_info() -> dict[str, Any]:
    return {
        "name": PACKAGE_NAME,
        "version": package_version(),
        "features": list(FEATURES),
    }


def check_compat(
    *,
    min_server: str | None = None,
    features: list[str] | None = None,
    client: str | None = None,
    client_version: str | None = None,
) -> dict[str, Any]:
    """Return whether this server satisfies ``min_server`` and ``features``.

    Always includes the running version. ``compatible`` is true only when the
    version floor and every requested feature are met.
    """
    have = package_version()
    required_features = [str(item).strip() for item in (features or []) if str(item).strip()]
    known = set(FEATURES)
    missing = [name for name in required_features if name not in known]
    version_ok = version_at_least(have, min_server)
    features_ok = not missing
    compatible = version_ok and features_ok

    if compatible:
        message = f"compatible with tesmartctl {have}"
    elif not version_ok:
        message = (
            f"server version {have} is older than required {min_server}; "
            f"upgrade tesmartctl on the host"
        )
    else:
        message = f"missing features: {', '.join(missing)}"

    payload: dict[str, Any] = {
        "compatible": compatible,
        "version": have,
        "name": PACKAGE_NAME,
        "version_ok": version_ok,
        "features_ok": features_ok,
        "features": list(FEATURES),
        "missing_features": missing,
        "message": message,
    }
    if min_server:
        payload["min_server"] = str(min_server).strip()
    if required_features:
        payload["requested_features"] = required_features
    if client:
        payload["client"] = str(client).strip()
    if client_version:
        payload["client_version"] = str(client_version).strip()
    return payload


def not_supported(path: str, *, method: str | None = None, detail: str | None = None) -> dict[str, Any]:
    """Standard payload for an unknown or disabled HTTP route / command."""
    verb = (method or "").upper()
    target = path if path.startswith("/") else f"/{path}"
    if verb:
        summary = f"{verb} {target} is not supported by this tesmartctl server"
    else:
        summary = f"{target} is not supported by this tesmartctl server"
    if detail:
        summary = f"{summary}: {detail}"
    return {
        "error": "not supported",
        "supported": False,
        "path": target,
        "method": verb or None,
        "message": summary,
        "version": package_version(),
    }
