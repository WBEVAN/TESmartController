"""The macOS kit app's main executable stays a Mach-O inside the bundle."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS kit bundle")


def test_packaged_launcher_has_both_cpu_slices():
    from tesmart.kit_macos import _mach_slices, _packaged_stub

    slices = _mach_slices(_packaged_stub().read_bytes())
    assert set(slices) == {"arm64", "x86_64"}
    for blob in slices.values():
        assert blob[:4] in {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"}


def test_kit_app_uses_macho_launcher(tmp_path, monkeypatch):
    from tesmart import kit_macos

    monkeypatch.setattr(kit_macos, "app_root", lambda: tmp_path / "TESmart Kit.app")
    app = kit_macos.ensure_kit_app()
    exe = app / "Contents" / "MacOS" / "tesmart-kit"
    host = (app / "Contents" / "Resources" / "python-host").read_text(encoding="utf-8").splitlines()
    plist = (app / "Contents" / "Info.plist").read_text(encoding="utf-8")

    assert kit_macos._is_macho_file(exe)
    assert not exe.read_bytes().startswith(b"#!")
    assert Path(host[0]).is_file()
    assert Path(host[1]).is_file()
    assert "com.tesmartctl.kit" in plist
    assert "NSLocalNetworkUsageDescription" in plist
    assert subprocess.run(["codesign", "--verify", str(app)], check=False).returncode == 0

    stamped = exe.stat().st_mtime_ns
    again = kit_macos.ensure_kit_app()
    assert again == app
    assert exe.stat().st_mtime_ns == stamped

    proc = subprocess.run(
        [str(exe)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode != 0
    assert "--url" in (proc.stderr + proc.stdout)
