"""Reconnecting clients must not accept a different mDNS name on the same address."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

from tesmart.kit_window import _probe_live
from tesmart.mdns import MdnsError, mdns_identity_conflict


def test_auto_kit_keeps_browsing_after_the_first_connection(monkeypatch) -> None:
    """A listener found at launch must still be followed to a later host or port."""
    from tesmart import cli

    def resolve(**_kwargs: object) -> str:
        return "http://192.0.2.10:8080"

    monkeypatch.setattr("tesmart.via_client.resolve_base_url", resolve)
    args = SimpleNamespace(
        via="auto",
        mdns_name="OfficeHDMI",
        env_file=None,
        timeout=1,
        listen=8080,
        url=None,
    )
    base, discover = cli._kit_listener_base(args)
    assert base == "http://192.0.2.10:8080"
    assert discover is True


def test_typed_kit_url_does_not_keep_browsing() -> None:
    from tesmart import cli

    args = SimpleNamespace(
        via=None,
        mdns_name=None,
        env_file=None,
        timeout=1,
        listen=8080,
        url=None,
    )
    base, discover = cli._kit_listener_base(args)
    assert base == "http://127.0.0.1:8080"
    assert discover is False


def test_auto_kit_still_discovers_when_the_first_browse_is_empty(monkeypatch) -> None:
    from tesmart import cli

    def resolve(**_kwargs: object) -> str:
        raise MdnsError("none")

    monkeypatch.setattr("tesmart.via_client.resolve_base_url", resolve)
    monkeypatch.setattr("tesmart.config.read_via_url", lambda _env_file=None: "http://192.0.2.10:8080")
    args = SimpleNamespace(
        via="auto",
        mdns_name="OfficeHDMI",
        env_file=None,
        timeout=1,
        listen=8080,
        url=None,
    )
    base, discover = cli._kit_listener_base(args)
    assert base == "http://192.0.2.10:8080"
    assert discover is True


def test_identity_conflict_only_when_both_names_differ() -> None:
    assert mdns_identity_conflict(None, "DenHDMI") is False
    assert mdns_identity_conflict("", "DenHDMI") is False
    assert mdns_identity_conflict("OfficeHDMI", None) is False
    assert mdns_identity_conflict("OfficeHDMI", "") is False
    assert mdns_identity_conflict("OfficeHDMI", "OfficeHDMI") is False
    assert mdns_identity_conflict("officehdmi", "OfficeHDMI") is False
    assert mdns_identity_conflict("Office.HDMI", "Office-HDMI") is False
    assert mdns_identity_conflict("OfficeHDMI", "DenHDMI") is True


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/kit":
            body = b"ok"
            content_type = "text/html"
        elif path == "/version":
            body = json.dumps({"mdns_name": self.server.mdns_name}).encode("utf-8")  # type: ignore[attr-defined]
            content_type = "application/json"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


def _serve(name: str | None) -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.mdns_name = name  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, int(server.server_address[1])


def test_probe_fails_when_same_address_has_a_different_mdns_name() -> None:
    server, port = _serve("DenHDMI")
    url = f"http://127.0.0.1:{port}/kit?app=1&mdns=OfficeHDMI"
    try:
        ok, detail = _probe_live(url, timeout=2)
        assert ok is False
        assert "DenHDMI" in detail

        server.mdns_name = "OfficeHDMI"  # type: ignore[attr-defined]
        ok, detail = _probe_live(url, timeout=2)
        assert ok is True
        assert detail == "Listener ready"

        server.mdns_name = None  # type: ignore[attr-defined]
        ok, _detail = _probe_live(url, timeout=2)
        assert ok is True

        unbound = f"http://127.0.0.1:{port}/kit?app=1"
        server.mdns_name = "DenHDMI"  # type: ignore[attr-defined]
        ok, _detail = _probe_live(unbound, timeout=2)
        assert ok is True
    finally:
        server.shutdown()
        server.server_close()
