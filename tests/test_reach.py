"""Retry a listener connection when the default route says no route to host."""

import errno
import urllib.error
import urllib.request

import pytest

from tesmart import reach
from tesmart.reach import host_unreachable, open_url


class _Resp:
    status = 200

    def read(self) -> bytes:
        return b"ok"


class _Conn:
    def __init__(self, host, port, timeout=None, source_address=None):
        self.host = host
        self.port = port
        self.source_address = source_address

    def request(self, method, path, body=None, headers=None):
        self.method = method
        self.path = path

    def getresponse(self) -> _Resp:
        return _Resp()

    def close(self) -> None:
        return None


def test_host_unreachable_recognizes_the_os_error():
    err = urllib.error.URLError(OSError(errno.EHOSTUNREACH, "No route to host"))
    assert host_unreachable(err)
    assert host_unreachable(OSError(10065, "No route to host"))
    assert not host_unreachable(OSError(errno.ECONNREFUSED, "Connection refused"))


def test_open_url_retries_from_a_lan_address(monkeypatch):
    reach._HINTED = False

    def boom(*_args, **_kwargs):
        raise urllib.error.URLError(OSError(errno.EHOSTUNREACH, "No route to host"))

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(reach.http.client, "HTTPConnection", _Conn)
    monkeypatch.setattr("tesmart.mdns.local_lan_ipv4s", lambda: ["10.1.1.5"])

    request = urllib.request.Request("http://192.0.2.20:8080/kit?app=1")
    with open_url(request, timeout=1) as response:
        assert response.status == 200
        assert response.read() == b"ok"


def test_open_url_hints_when_every_lan_address_fails(monkeypatch, capsys):
    reach._HINTED = False

    def boom(*_args, **_kwargs):
        raise urllib.error.URLError(OSError(errno.EHOSTUNREACH, "No route to host"))

    class _Fail(_Conn):
        def request(self, method, path, body=None, headers=None):
            raise OSError(errno.EHOSTUNREACH, "No route to host")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(reach.http.client, "HTTPConnection", _Fail)
    monkeypatch.setattr("tesmart.mdns.local_lan_ipv4s", lambda: ["10.1.1.5"])

    request = urllib.request.Request("http://192.0.2.20:8080/kit")
    with pytest.raises(urllib.error.URLError):
        open_url(request, timeout=1)
    err = capsys.readouterr().err
    assert "192.0.2.20:8080" in err
    assert "Local Network" in err or "10.1.1.5" in err
