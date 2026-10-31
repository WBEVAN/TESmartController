"""Mesh election, switch id, and join handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from tesmart import config
from tesmart.mdns import DiscoveredListener, build_properties, group_discover_listeners, is_mesh_alias
from tesmart.mesh import (
    MeshController,
    MeshError,
    default_mesh_mdns_name,
    elect_proxy,
    find_mesh_alias_owner,
    format_mesh_status_text,
    mesh_enabled,
    peer_from_listener,
    same_switch_standalones,
    switch_id_from_settings,
    write_mesh_enabled,
)


def test_switch_id_tcp() -> None:
    assert switch_id_from_settings(host="192.168.1.10", port=5000) == "tcp:192.168.1.10:5000"


def test_switch_id_rejects_serial() -> None:
    with pytest.raises(MeshError, match="TCP"):
        switch_id_from_settings(host="192.168.1.10", port=5000, serial="/dev/tty.usb")


def test_elect_oldest_wins() -> None:
    peers = [
        {"mdns_name": "B", "url": "http://10.0.0.2:8080", "started_at": 2000},
        {"mdns_name": "A", "url": "http://10.0.0.1:8080", "started_at": 1000},
    ]
    winner = elect_proxy(peers, self_started=3000, self_name="C", self_url="http://10.0.0.3:8080")
    assert winner["mdns_name"] == "A"
    assert winner.get("self") is not True


def test_elect_self_when_oldest() -> None:
    peers = [{"mdns_name": "B", "url": "http://10.0.0.2:8080", "started_at": 5000}]
    winner = elect_proxy(peers, self_started=1000, self_name="A", self_url="http://10.0.0.1:8080")
    assert winner.get("self") is True


def test_elect_prefers_existing_proxy_on_rejoin() -> None:
    """Restarted peer must join the live proxy even when it is "older" by clock."""
    peers = [
        {
            "mdns_name": "OfficeHDMI2-2",
            "url": "http://10.0.0.2:7273",
            "started_at": 5000,
            "role": "proxy",
        },
        {
            "mdns_name": "Other",
            "url": "http://10.0.0.3:7274",
            "started_at": 100,
            "role": "client",
        },
    ]
    # Self claims an older started_at than the proxy — still must not steal.
    winner = elect_proxy(
        peers, self_started=1, self_name="OfficeHDMI2", self_url="http://10.0.0.1:7272"
    )
    assert winner["url"] == "http://10.0.0.2:7273"
    assert winner.get("self") is not True


def test_elect_ignores_missing_started_at() -> None:
    peers = [
        {"mdns_name": "Stale", "url": "http://10.0.0.9:8080", "started_at": 0, "role": "client"},
        {"mdns_name": "Live", "url": "http://10.0.0.2:8080", "started_at": 2000, "role": "client"},
    ]
    winner = elect_proxy(
        peers, self_started=3000, self_name="C", self_url="http://10.0.0.3:8080"
    )
    assert winner["mdns_name"] == "Live"


def test_peer_from_listener_requires_mesh_txt() -> None:
    plain = DiscoveredListener("Office", 8080, ("10.0.0.1",), properties={"version": "0.0.7.18"})
    assert peer_from_listener(plain) is None
    meshed = DiscoveredListener(
        "Office",
        8080,
        ("10.0.0.1",),
        properties={
            "mesh": "1",
            "switch": "tcp:192.168.1.10:5000",
            "started": "1234567890123",
            "role": "proxy",
        },
    )
    peer = peer_from_listener(meshed)
    assert peer is not None
    assert peer["switch_id"] == "tcp:192.168.1.10:5000"
    assert peer["started_at"] == 1234567890123
    assert peer["role"] == "proxy"


def test_build_properties_mesh() -> None:
    props = build_properties(
        version="0.0.7.18",
        panel=True,
        input_count=8,
        mesh=True,
        switch_id="tcp:10.0.0.1:5000",
        started_at=99,
        role="proxy",
        mesh_name="OfficeHDMI1-mesh",
        kind="mesh",
        link="up",
    )
    assert props["mesh"] == "1"
    assert props["switch"] == "tcp:10.0.0.1:5000"
    assert props["started"] == "99"
    assert props["role"] == "proxy"
    assert props["mesh_name"] == "OfficeHDMI1-mesh"
    assert props["kind"] == "mesh"
    assert props["link"] == "up"


def test_discover_shows_proxy_switch_link() -> None:
    from tesmart.mdns import format_discover_text

    proxy = DiscoveredListener(
        instance_name="OfficeHDMI2-2",
        port=7273,
        addresses=("172.16.0.131",),
        hostname="mini.local",
        properties={
            "mesh": "1",
            "switch": "tcp:172.16.0.222:5000",
            "role": "proxy",
            "mesh_name": "OfficeHDMI2-mesh",
            "link": "up",
        },
    )
    client = DiscoveredListener(
        instance_name="OfficeHDMI2",
        port=7272,
        addresses=("172.16.0.131",),
        hostname="mini.local",
        properties={
            "mesh": "1",
            "switch": "tcp:172.16.0.222:5000",
            "role": "client",
            "mesh_name": "OfficeHDMI2-mesh",
            "link": "down",
        },
    )
    text = format_discover_text([proxy, client], color=False)
    assert "switch up" in text
    assert "via proxy" in text

    down = DiscoveredListener(
        instance_name="OfficeHDMI2-2",
        port=7273,
        addresses=("172.16.0.131",),
        properties={
            "mesh": "1",
            "switch": "tcp:x:5000",
            "role": "proxy",
            "mesh_name": "OfficeHDMI2-mesh",
            "link": "down",
        },
    )
    bad = format_discover_text([down], color=False)
    assert "switch down" in bad
    assert "no TCP to the HDMI switch" in bad


def test_switch_link_state_remote() -> None:
    from tesmart.mesh import LINK_DOWN, LINK_UP, switch_link_state

    class Remote:
        mesh_remote = True
        proxy_url = "http://x"

    class Local:
        simulated = True

    assert switch_link_state(Remote()) == LINK_DOWN
    assert switch_link_state(Local()) == LINK_UP
    assert switch_link_state(None) == LINK_DOWN


def test_pick_listener_falls_back_to_mesh_alias_suffix() -> None:
    """Env still has OfficeHDMI2 after failover; LAN only has OfficeHDMI2-mesh."""
    from tesmart.mdns import pick_listener

    alias = DiscoveredListener(
        instance_name="OfficeHDMI2-mesh",
        port=7273,
        addresses=("172.16.0.131",),
        properties={
            "mesh": "1",
            "switch": "tcp:x:5000",
            "role": "proxy",
            "kind": "mesh",
            "mesh_name": "OfficeHDMI2-mesh",
        },
    )
    proxy = DiscoveredListener(
        instance_name="OfficeHDMI2-2",
        port=7273,
        addresses=("172.16.0.131",),
        properties={
            "mesh": "1",
            "switch": "tcp:x:5000",
            "role": "proxy",
            "mesh_name": "OfficeHDMI2-mesh",
        },
    )
    chosen = pick_listener(
        [alias, proxy],
        name="OfficeHDMI2",
        prefer_url="http://172.16.0.131:7272",
    )
    assert chosen.port == 7273
    assert chosen.instance_name in {"OfficeHDMI2-2", "OfficeHDMI2-mesh"}


def test_listeners_for_name_prefers_live_proxy_over_stale_alias() -> None:
    from tesmart.mdns import listeners_for_name, pick_listener

    stale_alias = DiscoveredListener(
        instance_name="OfficeHDMI1-mesh",
        port=7272,
        addresses=("172.16.0.131",),
        properties={
            "mesh": "1",
            "switch": "tcp:x:5000",
            "role": "proxy",
            "kind": "mesh",
            "mesh_name": "OfficeHDMI1-mesh",
        },
    )
    live_proxy = DiscoveredListener(
        instance_name="OfficeHDMI2",
        port=7273,
        addresses=("172.16.0.131",),
        properties={
            "mesh": "1",
            "switch": "tcp:x:5000",
            "role": "proxy",
            "mesh_name": "OfficeHDMI1-mesh",
        },
    )
    client = DiscoveredListener(
        instance_name="OfficeHDMI3",
        port=7274,
        addresses=("172.16.0.131",),
        properties={
            "mesh": "1",
            "switch": "tcp:x:5000",
            "role": "client",
            "mesh_name": "OfficeHDMI1-mesh",
        },
    )
    ordered = listeners_for_name([stale_alias, client, live_proxy], "OfficeHDMI1-mesh")
    assert [item.instance_name for item in ordered] == [
        "OfficeHDMI2",
        "OfficeHDMI1-mesh",
        "OfficeHDMI3",
    ]
    # Cached via of the dead proxy must not win named lookup.
    chosen = pick_listener(
        [stale_alias, live_proxy],
        name="OfficeHDMI1-mesh",
        prefer_url="http://172.16.0.131:7272",
    )
    assert chosen.instance_name == "OfficeHDMI2"
    assert chosen.port == 7273


def test_default_mesh_mdns_name() -> None:
    assert default_mesh_mdns_name("OfficeHDMI1") == "OfficeHDMI1-mesh"
    assert default_mesh_mdns_name("already-mesh") == "already-mesh-alias"
    assert default_mesh_mdns_name(None) == "tesmart-mesh"


def test_peer_from_listener_skips_mesh_alias() -> None:
    alias = DiscoveredListener(
        instance_name="OfficeHDMI1-mesh",
        port=7272,
        addresses=("172.16.0.131",),
        properties={
            "mesh": "1",
            "switch": "tcp:172.16.0.222:5000",
            "started": "1",
            "role": "proxy",
            "kind": "mesh",
            "mesh_name": "OfficeHDMI1-mesh",
        },
    )
    assert is_mesh_alias(alias) is True
    assert peer_from_listener(alias) is None


def test_group_discover_listeners_nests_peers() -> None:
    from tesmart.mdns import format_discover_text

    alias = DiscoveredListener(
        instance_name="OfficeHDMI1-mesh",
        port=7272,
        addresses=("172.16.0.131",),
        hostname="Waynes-Mac-Mini.local",
        properties={
            "mesh": "1",
            "switch": "tcp:x:5000",
            "role": "proxy",
            "kind": "mesh",
            "mesh_name": "OfficeHDMI1-mesh",
        },
    )
    proxy = DiscoveredListener(
        instance_name="OfficeHDMI1",
        port=7272,
        addresses=("172.16.0.131",),
        hostname="Waynes-Mac-Mini.local",
        properties={
            "mesh": "1",
            "switch": "tcp:x:5000",
            "role": "proxy",
            "mesh_name": "OfficeHDMI1-mesh",
        },
    )
    client = DiscoveredListener(
        instance_name="OfficeHDMI2",
        port=7273,
        addresses=("172.16.0.131",),
        hostname="Waynes-Mac-Mini.local",
        properties={
            "mesh": "1",
            "switch": "tcp:x:5000",
            "role": "client",
            "mesh_name": "OfficeHDMI1-mesh",
        },
    )
    alone = DiscoveredListener(
        instance_name="Den",
        port=8080,
        addresses=("172.16.0.10",),
        hostname="den.local",
        properties={"panel": "1"},
    )
    grouped = group_discover_listeners([alone, client, alias, proxy])
    assert len(grouped["mesh"]) == 1
    assert grouped["mesh"][0]["mesh_name"] == "OfficeHDMI1-mesh"
    assert grouped["mesh"][0]["alias"] is alias
    assert [m.instance_name for m in grouped["mesh"][0]["members"]] == [
        "OfficeHDMI1",
        "OfficeHDMI2",
    ]
    assert [m.instance_name for m in grouped["standalone"]] == ["Den"]

    text = format_discover_text([alone, client, alias, proxy], color=False)
    assert "MESH" in text
    assert "STANDALONE" in text
    assert "proxy   OfficeHDMI1" in text
    assert "client  OfficeHDMI2" in text
    assert "failover →" in text
    assert "Den" in text
    # Proxy listed before client inside the mesh block.
    assert text.index("proxy   OfficeHDMI1") < text.index("client  OfficeHDMI2")
    assert text.index("MESH") < text.index("STANDALONE")
    assert "\033[" not in text

    colored = format_discover_text([alone, client, alias, proxy], color=True)
    assert "\033[" in colored
    assert "MESH" in colored


def test_format_mesh_status_text() -> None:
    proxy = DiscoveredListener(
        instance_name="OfficeHDMI1",
        port=7272,
        addresses=("172.16.0.131",),
        hostname="mini.local",
        properties={
            "mesh": "1",
            "switch": "tcp:172.16.0.222:5000",
            "role": "proxy",
            "mesh_name": "OfficeHDMI1-mesh",
            "started": "1000",
        },
    )
    client = DiscoveredListener(
        instance_name="OfficeHDMI2",
        port=7273,
        addresses=("172.16.0.131",),
        hostname="mini.local",
        properties={
            "mesh": "1",
            "switch": "tcp:172.16.0.222:5000",
            "role": "client",
            "mesh_name": "OfficeHDMI1-mesh",
            "started": "2000",
        },
    )
    alias = DiscoveredListener(
        instance_name="OfficeHDMI1-mesh",
        port=7272,
        addresses=("172.16.0.131",),
        hostname="mini.local",
        properties={
            "mesh": "1",
            "switch": "tcp:172.16.0.222:5000",
            "role": "proxy",
            "kind": "mesh",
            "mesh_name": "OfficeHDMI1-mesh",
        },
    )
    rogue = DiscoveredListener(
        instance_name="OldListener",
        port=8080,
        addresses=("172.16.0.50",),
        hostname="old.local",
        properties={"switch": "tcp:172.16.0.222:5000", "panel": "1"},
    )
    peers = [
        peer_from_listener(proxy),
        peer_from_listener(client),
    ]
    assert all(p is not None for p in peers)
    conflicts = same_switch_standalones(
        [proxy, client, alias, rogue],
        switch_id="tcp:172.16.0.222:5000",
    )
    assert [c.instance_name for c in conflicts] == ["OldListener"]

    text = format_mesh_status_text(
        switch_id="tcp:172.16.0.222:5000",
        local_enabled=True,
        services=[proxy, client, alias, rogue],
        peers=peers,  # type: ignore[arg-type]
        live={
            "role": "proxy",
            "mdns_name": "OfficeHDMI1",
            "mesh_mdns_name": "OfficeHDMI1-mesh",
            "generation": 2,
            "peers": [
                {"mdns_name": "OfficeHDMI1", "role": "proxy", "self": True},
                {"mdns_name": "OfficeHDMI2", "role": "client"},
            ],
        },
        live_url="http://172.16.0.131:7272",
        color=False,
    )
    assert "MESH" in text
    assert "CONFLICTS" in text
    assert "LIVE" in text
    assert "proxy   OfficeHDMI1" in text
    assert "client  OfficeHDMI2" in text
    assert "Failover mDNS: OfficeHDMI1-mesh" in text
    assert "OldListener" in text
    assert "\033[" not in text
    # Remote LIVE must not claim "(this computer)" for the answering proxy.
    remote = format_mesh_status_text(
        switch_id="tcp:172.16.0.222:5000",
        local_enabled=True,
        services=[proxy, client, alias],
        peers=peers,  # type: ignore[arg-type]
        live={
            "role": "proxy",
            "mdns_name": "OLDMAC",
            "mesh_mdns_name": "OfficeHDMI1-mesh",
            "link": "up",
            "generation": 2,
            "peers": [
                {"mdns_name": "OLDMAC", "role": "proxy", "self": True},
                {"mdns_name": "OfficeHDMI2", "role": "client", "url": "http://172.16.0.131:7272"},
            ],
        },
        live_url="http://10.255.0.1:7272",
        color=False,
    )
    assert "remote listener" in remote
    assert "(answering listener)" in remote
    assert "this computer owns the session" not in remote
    assert "Mesh: proxy is OLDMAC" in remote


def test_discover_flags_standalone_same_switch() -> None:
    from tesmart.mdns import discover_switch_conflicts, format_discover_text

    proxy = DiscoveredListener(
        instance_name="OfficeHDMI1",
        port=7272,
        addresses=("172.16.0.131",),
        properties={
            "mesh": "1",
            "switch": "tcp:172.16.0.222:5000",
            "role": "proxy",
            "mesh_name": "OfficeHDMI1-mesh",
        },
    )
    rogue = DiscoveredListener(
        instance_name="OldListener",
        port=8080,
        addresses=("172.16.0.50",),
        properties={
            "switch": "tcp:172.16.0.222:5000",
            "panel": "1",
        },
    )
    other = DiscoveredListener(
        instance_name="Den",
        port=8080,
        addresses=("172.16.0.10",),
        properties={"switch": "tcp:10.0.0.1:5000", "panel": "1"},
    )
    grouped = group_discover_listeners([proxy, rogue, other])
    conflicts = discover_switch_conflicts(grouped)
    assert "OldListener" in conflicts
    assert conflicts["OldListener"]["mesh_name"] == "OfficeHDMI1-mesh"
    assert "Den" not in conflicts

    text = format_discover_text([proxy, rogue, other], color=False)
    assert "WARNING: same switch as mesh OfficeHDMI1-mesh" in text
    assert "OldListener" in text


def test_mdns_update_properties_sets_role() -> None:
    """Role changes must reach zeroconf (ServiceInfo.properties is read-only)."""
    pytest.importorskip("zeroconf")
    import socket

    from zeroconf import ServiceInfo

    from tesmart.mdns import MdnsAdvertisement

    info = ServiceInfo(
        "_wbevan-tesmart._tcp.local.",
        "RoleTest._wbevan-tesmart._tcp.local.",
        addresses=[socket.inet_aton("127.0.0.1")],
        port=7272,
        properties={"role": "client", "mesh": "1"},
    )
    updated: list[object] = []

    class _FakeZc:
        def update_service(self, svc: object) -> None:
            updated.append(svc)

    adv = MdnsAdvertisement(zeroconf=_FakeZc(), info=info, instance_name="RoleTest")
    adv.update_properties({"role": "proxy", "mesh": "1", "switch": "tcp:1.2.3.4:5000"})
    assert info.decoded_properties["role"] == "proxy"
    assert info.decoded_properties["switch"] == "tcp:1.2.3.4:5000"
    assert updated == [info]


def test_mesh_env_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / "env"
    monkeypatch.setenv("TESMART_ENV_FILE", str(env))
    assert mesh_enabled(str(env)) is False
    write_mesh_enabled(True, str(env))
    assert mesh_enabled(str(env)) is True
    assert config.read_env_file(env)["TESMART_HTTP_MESH"] == "1"


def test_handle_join_and_leave(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / "env"
    monkeypatch.setenv("TESMART_ENV_FILE", str(env))
    config.write_env_file(
        env,
        {"TESMART_HOST": "10.0.0.8", "TESMART_PORT": "5000", "TESMART_INPUTS": "8"},
    )
    controller = MeshController(
        env_file=str(env),
        switch_id="tcp:10.0.0.8:5000",
        started_at=1000,
        browse_fn=lambda: [],
    )
    controller.set_identity(mdns_name="Proxy", base_url="http://10.0.0.1:8080")
    controller._become_proxy(reason="test")
    assert controller.state.mesh_mdns_name == "Proxy-mesh"
    reply = controller.handle_join(
        {
            "switch_id": "tcp:10.0.0.8:5000",
            "started_at": 2000,
            "mdns_name": "Client",
            "base_url": "http://10.0.0.2:8080",
            "node_id": "abc123",
        }
    )
    assert reply["accepted"] is True
    assert reply["mesh_mdns_name"] == "Proxy-mesh"
    assert reply["generation"] >= 1
    peers = controller.snapshot()["peers"]
    assert any(p.get("node_id") == "abc123" for p in peers)
    left = controller.handle_leave({"node_id": "abc123"})
    assert left["removed"] is True


def test_mesh_config_pull_preserves_local_mdns_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tesmart.mesh import apply_mesh_shared_config

    env = tmp_path / "env"
    monkeypatch.setenv("TESMART_ENV_FILE", str(env))
    config.write_env_file(
        env,
        {
            "TESMART_HOST": "10.0.0.8",
            "TESMART_PORT": "5000",
            "TESMART_MDNS_NAME": "OfficeHDMI2-2",
            "TESMART_HTTP": "0.0.0.0:7273",
            "TESMART_HTTP_MESH": "1",
        },
    )
    bundle = {
        "format": "tesmartctl-config",
        "version": 1,
        "env": {
            "TESMART_HOST": "10.0.0.8",
            "TESMART_PORT": "5000",
            "TESMART_MDNS_NAME": "OLDMAC",
            "TESMART_HTTP": "0.0.0.0:7271",
            "TESMART_HTTP_MESH": "1",
            "TESMART_HTTP_MESH_MDNS_NAME": "OfficeHDMI2-mesh",
            "TESMART_NAME_1": "Mac",
        },
    }
    apply_mesh_shared_config(bundle, str(env))
    got = config.read_env_file(env)
    assert got["TESMART_MDNS_NAME"] == "OfficeHDMI2-2"
    assert got["TESMART_HTTP"] == "0.0.0.0:7273"
    assert got["TESMART_HTTP_MESH_MDNS_NAME"] == "OfficeHDMI2-mesh"
    assert got["TESMART_NAME_1"] == "Mac"


def test_join_learns_mesh_mdns_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / "env"
    monkeypatch.setenv("TESMART_ENV_FILE", str(env))
    config.write_env_file(env, {"TESMART_HOST": "10.0.0.8", "TESMART_PORT": "5000"})

    def fake_http(base: str, path: str, *, method: str = "GET", body: dict | None = None) -> dict:
        if path == "/mesh/join":
            return {
                "accepted": True,
                "mesh_mdns_name": "OfficeHDMI1-mesh",
                "generation": 3,
                "mesh": {"mesh_mdns_name": "OfficeHDMI1-mesh"},
            }
        if path == "/api/config":
            return {"bundle": {"version": 1, "env": {}, "kit": None, "messages": []}}
        raise AssertionError(path)

    client = MeshController(
        env_file=str(env),
        switch_id="tcp:10.0.0.8:5000",
        started_at=2000,
        browse_fn=lambda: [],
        request_json=fake_http,
    )
    client.set_identity(mdns_name="OfficeHDMI2", base_url="http://10.0.0.2:8080")
    client._join("http://10.0.0.1:8080")
    assert client.state.role == "client"
    assert client.state.mesh_mdns_name == "OfficeHDMI1-mesh"
    assert config.read_env_file(env)["TESMART_HTTP_MESH_MDNS_NAME"] == "OfficeHDMI1-mesh"


def test_join_rejects_wrong_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / "env"
    monkeypatch.setenv("TESMART_ENV_FILE", str(env))
    controller = MeshController(
        env_file=str(env),
        switch_id="tcp:10.0.0.8:5000",
        started_at=1000,
        browse_fn=lambda: [],
    )
    controller._become_proxy(reason="test")
    reply = controller.handle_join(
        {"switch_id": "tcp:10.0.0.9:5000", "node_id": "x", "started_at": 1}
    )
    assert reply["accepted"] is False


def test_find_live_proxy_url_prefers_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / "env"
    monkeypatch.setenv("TESMART_ENV_FILE", str(env))
    alias = DiscoveredListener(
        instance_name="OfficeHDMI2-mesh",
        port=7271,
        addresses=("172.16.0.109",),
        properties={
            "mesh": "1",
            "kind": "mesh",
            "switch": "tcp:10.0.0.8:5000",
            "role": "proxy",
            "mesh_name": "OfficeHDMI2-mesh",
            "started": "1000",
        },
    )
    other = DiscoveredListener(
        instance_name="OfficeHDMI2-2",
        port=7273,
        addresses=("172.16.0.131",),
        properties={
            "mesh": "1",
            "switch": "tcp:10.0.0.8:5000",
            "role": "proxy",
            "mesh_name": "OfficeHDMI2-mesh",
            "started": "5000",
        },
    )

    def fake_http(base: str, path: str, *, method: str = "GET", body: dict | None = None) -> dict:
        if path == "/mesh":
            return {
                "role": "proxy",
                "switch_id": "tcp:10.0.0.8:5000",
                "node_id": "oldmac",
            }
        raise AssertionError(path)

    node = MeshController(
        env_file=str(env),
        switch_id="tcp:10.0.0.8:5000",
        started_at=9000,
        mesh_mdns_name="OfficeHDMI2-mesh",
        browse_fn=lambda: [alias, other],
        request_json=fake_http,
    )
    node.set_identity(mdns_name="NewNode", base_url="http://10.0.0.3:8080")
    assert node.find_live_proxy_url() == "http://172.16.0.109:7271"


def test_find_mesh_alias_owner() -> None:
    alias = DiscoveredListener(
        instance_name="OfficeHDMI2-mesh",
        port=7271,
        addresses=("172.16.0.109",),
        properties={
            "mesh": "1",
            "kind": "mesh",
            "switch": "tcp:172.16.0.222:5000",
            "role": "proxy",
            "mesh_name": "OfficeHDMI2-mesh",
            "started": "1000",
        },
    )
    peer = DiscoveredListener(
        instance_name="OfficeHDMI2-2",
        port=7273,
        addresses=("172.16.0.131",),
        properties={
            "mesh": "1",
            "switch": "tcp:172.16.0.222:5000",
            "role": "proxy",
            "mesh_name": "OfficeHDMI2-mesh",
        },
    )
    owner = find_mesh_alias_owner(
        [alias, peer],
        mesh_name="OfficeHDMI2-mesh",
        switch_id="tcp:172.16.0.222:5000",
    )
    assert owner is not None
    assert owner["url"] == "http://172.16.0.109:7271"
    assert owner.get("alias") is True


def test_try_join_alias_owner_demotes_on_live_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / "env"
    monkeypatch.setenv("TESMART_ENV_FILE", str(env))
    config.write_env_file(env, {"TESMART_HOST": "10.0.0.8", "TESMART_PORT": "5000"})

    alias = DiscoveredListener(
        instance_name="OfficeHDMI2-mesh",
        port=8080,
        addresses=("10.0.0.1",),
        properties={
            "mesh": "1",
            "kind": "mesh",
            "switch": "tcp:10.0.0.8:5000",
            "role": "proxy",
            "mesh_name": "OfficeHDMI2-mesh",
            "started": "1000",
        },
    )
    calls: list[str] = []

    def fake_http(base: str, path: str, *, method: str = "GET", body: dict | None = None) -> dict:
        calls.append(f"{method}:{path}")
        if path == "/mesh":
            return {
                "role": "proxy",
                "switch_id": "tcp:10.0.0.8:5000",
                "node_id": "other",
            }
        if path == "/mesh/join":
            return {
                "accepted": True,
                "mesh_mdns_name": "OfficeHDMI2-mesh",
                "generation": 2,
                "mesh": {"mesh_mdns_name": "OfficeHDMI2-mesh"},
            }
        if path == "/api/config":
            return {"bundle": {"version": 1, "env": {}, "kit": None, "messages": []}}
        raise AssertionError(path)

    younger = MeshController(
        env_file=str(env),
        switch_id="tcp:10.0.0.8:5000",
        started_at=5000,
        mesh_mdns_name="OfficeHDMI2-mesh",
        browse_fn=lambda: [alias],
        request_json=fake_http,
    )
    younger.set_identity(mdns_name="OfficeHDMI2-2", base_url="http://10.0.0.2:8080")
    younger._become_proxy(reason="join-failed")
    assert younger.state.role == "proxy"
    assert younger.handle_alias_claim_failed() is True
    assert younger.state.role == "client"
    assert younger.state.proxy_url == "http://10.0.0.1:8080"
    assert any(c.startswith("POST:/mesh/join") for c in calls)


def test_try_join_alias_owner_ignores_stale_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / "env"
    monkeypatch.setenv("TESMART_ENV_FILE", str(env))
    alias = DiscoveredListener(
        instance_name="OfficeHDMI2-mesh",
        port=8080,
        addresses=("10.0.0.1",),
        properties={
            "mesh": "1",
            "kind": "mesh",
            "switch": "tcp:10.0.0.8:5000",
            "role": "proxy",
            "mesh_name": "OfficeHDMI2-mesh",
        },
    )

    def dead_http(base: str, path: str, *, method: str = "GET", body: dict | None = None) -> dict:
        raise MeshError("unreachable")

    node = MeshController(
        env_file=str(env),
        switch_id="tcp:10.0.0.8:5000",
        started_at=1000,
        mesh_mdns_name="OfficeHDMI2-mesh",
        browse_fn=lambda: [alias],
        request_json=dead_http,
    )
    node.set_identity(mdns_name="Survivor", base_url="http://10.0.0.2:8080")
    node._become_proxy(reason="elected")
    assert node.try_join_alias_owner() is False
    assert node.state.role == "proxy"


def test_instance_label_from_service_name() -> None:
    from tesmart.mdns import SERVICE_TYPE, _instance_label_from_service_name

    assert (
        _instance_label_from_service_name(f"OfficeHDMI2-2.{SERVICE_TYPE}")
        == "OfficeHDMI2-2"
    )


def test_advertise_avoids_mesh_alias_as_instance_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Instance registration must not steal the shared …-mesh failover label."""
    pytest.importorskip("zeroconf")
    import socket

    import zeroconf as zc_mod

    from tesmart import mdns as mdns_mod

    registered: list[tuple[str, bool]] = []

    class _FakeZc:
        def register_service(self, info: object, *args: object, **kwargs: object) -> None:
            allow = bool(kwargs.get("allow_name_change"))
            registered.append((getattr(info, "name", ""), allow))

        def close(self) -> None:
            return None

    monkeypatch.setattr(zc_mod, "Zeroconf", lambda *a, **k: _FakeZc())
    monkeypatch.setattr(
        mdns_mod,
        "_ipv4_addresses",
        lambda preferred=None: [socket.inet_aton("172.16.0.131")],
    )

    adv = mdns_mod.advertise(
        port=7272,
        bind_host="0.0.0.0",
        instance_name="OfficeHDMI2-mesh",
        version="0.0.7.19",
        panel=True,
        input_count=8,
        mesh=True,
        switch_id="tcp:172.16.0.222:5000",
        mesh_name="OfficeHDMI2-mesh",
    )
    assert adv.instance_name.casefold() != "officehdmi2-mesh"
    assert registered and registered[0][1] is True
    assert "OfficeHDMI2-mesh." not in registered[0][0]


def test_set_alias_fails_fast_on_nonunique() -> None:
    pytest.importorskip("zeroconf")
    import socket

    from zeroconf import NonUniqueNameException, ServiceInfo

    from tesmart.mdns import MdnsAdvertisement

    info = ServiceInfo(
        "_wbevan-tesmart._tcp.local.",
        "RoleTest._wbevan-tesmart._tcp.local.",
        addresses=[socket.inet_aton("127.0.0.1")],
        port=7272,
        properties={"role": "proxy"},
    )

    class _FakeZc:
        def register_service(self, svc: object) -> None:
            raise NonUniqueNameException()

        def unregister_service(self, svc: object) -> None:
            return None

    adv = MdnsAdvertisement(
        zeroconf=_FakeZc(),
        info=info,
        instance_name="RoleTest",
    )
    adv._addresses = [socket.inet_aton("127.0.0.1")]
    adv._port = 7272
    assert adv.set_alias("OfficeHDMI2-mesh", {"kind": "mesh", "role": "proxy"}) is False
    assert adv.alias_info is None
