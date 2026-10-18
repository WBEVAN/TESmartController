"""macOS-only Dock branding for the kit window.

The kit itself (``tesmart.kit_window``) is cross-platform. On Darwin, a bare
``python3.12`` process cannot replace the Dock document icon — Apple keys that
off the hosting ``.app`` bundle. This module builds a tiny launcher app under
``~/.config/tesmartctl/`` so Dock / Cmd-Tab show TESmart. Linux and Windows
never import this path.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_BUNDLE_ID = "com.tesmartctl.kit"
_APP_NAME = "TESmart Kit.app"


class KitMacosError(RuntimeError):
    """macOS Dock launcher failed."""


def _packaged_icns() -> Path | None:
    path = Path(__file__).resolve().parent / "static" / "kit" / "AppIcon.icns"
    return path if path.is_file() else None


def _packaged_png_icon() -> Path | None:
    path = Path(__file__).resolve().parent / "static" / "kit" / "png" / "app-icon.png"
    return path if path.is_file() else None


def app_root() -> Path:
    return Path.home() / ".config" / "tesmartctl" / _APP_NAME


def ensure_kit_app() -> Path:
    """Create/update the Dock-facing ``.app`` wrapper (idempotent)."""
    if sys.platform != "darwin":
        raise KitMacosError("TESmart Kit.app is only used on macOS")

    root = app_root()
    contents = root / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)

    icns_src = _packaged_icns()
    if icns_src is not None:
        dest = resources / "AppIcon.icns"
        if not dest.is_file() or dest.stat().st_mtime < icns_src.stat().st_mtime:
            dest.write_bytes(icns_src.read_bytes())

    (contents / "Info.plist").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleDisplayName</key><string>TESmart</string>
  <key>CFBundleExecutable</key><string>tesmart-kit</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIdentifier</key><string>{_BUNDLE_ID}</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>TESmart</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.0.7.9</string>
  <key>CFBundleVersion</key><string>0.0.7.9</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
</dict>
</plist>
""",
        encoding="utf-8",
    )

    launcher = macos / "tesmart-kit"
    launcher.write_text(
        "#!/bin/bash\n"
        "export PYTHONUNBUFFERED=1\n"
        f'exec "{sys.executable}" -m tesmart.kit_window "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    return root


def quit_kit_app() -> None:
    """Ask the Dock kit window to quit (best-effort; used on Ctrl-C)."""
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(  # noqa: S603 - fixed osascript
            ["osascript", "-e", f'tell application id "{_BUNDLE_ID}" to quit'],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def open_kit_app(
    url: str,
    *,
    width: int,
    height: int,
    fresh: bool = False,
    wait: bool = False,
) -> None:
    """Launch the kit through the Dock-facing ``.app`` (``open -a``)."""
    app = ensure_kit_app()
    cmd = ["open", "-n"]
    if wait:
        cmd.append("-W")
    cmd.extend(
        ["-a", str(app), "--args", "--url", url, "--width", str(width), "--height", str(height)]
    )
    if fresh:
        cmd.append("--fresh")
    try:
        # Popen so Ctrl-C can quit the kit window instead of dumping a traceback
        # while ``open -W`` is blocked waiting for the app to exit.
        proc = subprocess.Popen(  # noqa: S603 - fixed open(1) + our .app
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        raise KitMacosError(f"could not open TESmart Kit.app: {exc}") from exc
    try:
        _stdout, stderr = proc.communicate()
    except KeyboardInterrupt:
        quit_kit_app()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=1)
        raise
    if proc.returncode != 0:
        detail = (stderr or "").strip() or f"exit {proc.returncode}"
        raise KitMacosError(f"could not open TESmart Kit.app: {detail}")


def apply_process_icon() -> None:
    """Best-effort in-process icon when already running inside the ``.app``."""
    if sys.platform != "darwin":
        return
    icon = _packaged_png_icon() or _packaged_icns()
    if icon is None:
        return
    try:
        import AppKit  # type: ignore[import-untyped]
    except ImportError:
        return

    image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(icon))
    if image is None:
        return
    app = AppKit.NSApplication.sharedApplication()
    app.setApplicationIconImage_(image)
    try:
        app.dockTile().display()
    except Exception:
        pass
