"""macOS-only Dock branding for the kit window.

The kit itself (``tesmart.kit_window``) is cross-platform. On Darwin, a bare
``python3.12`` process cannot replace the Dock document icon — Apple keys that
off the hosting ``.app`` bundle. This module builds a tiny launcher app under
``~/.config/tesmartctl/`` so Dock / Cmd-Tab show TESmart. It also rewires the
About menu so the panel shows TESmart Kit (not ``python3``). Linux and Windows
never import this path.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from . import version as pkg_version

_BUNDLE_ID = "com.tesmartctl.kit"
_APP_NAME = "TESmart Kit.app"
_DISPLAY_NAME = "TESmart Kit"
_COPYRIGHT = "Independent community project — not affiliated with TESmart."
_CREDITS_INTRO = "Control TESmart HDMI / KVM switches over LAN or RS232.\ntesmartctl\n"
_GITHUB_URL = "https://github.com/WBEVAN/TESmartController"
_GITHUB_LABEL = "github.com/WBEVAN/TESmartController"

# Keep Cocoa targets alive for the process lifetime.
_ABOUT_TARGET = None
_BRANDING_TIMER_TARGET = None


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

    ver = pkg_version.package_version()
    (contents / "Info.plist").write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleDisplayName</key><string>{_DISPLAY_NAME}</string>
  <key>CFBundleExecutable</key><string>tesmart-kit</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIdentifier</key><string>{_BUNDLE_ID}</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleName</key><string>{_DISPLAY_NAME}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>{ver}</string>
  <key>CFBundleVersion</key><string>{ver}</string>
  <key>CFBundleGetInfoString</key><string>{_DISPLAY_NAME} {ver}</string>
  <key>NSHumanReadableCopyright</key><string>{_COPYRIGHT}</string>
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


def _load_app_icon():
    icon = _packaged_png_icon() or _packaged_icns()
    if icon is None:
        return None
    try:
        import AppKit  # type: ignore[import-untyped]
    except ImportError:
        return None
    return AppKit.NSImage.alloc().initWithContentsOfFile_(str(icon))


def _credits_attributed():
    """Credits block with a clickable GitHub link for the About panel."""
    plain = f"{_CREDITS_INTRO}{_GITHUB_LABEL}"
    try:
        from Foundation import (  # type: ignore[import-untyped]
            NSMutableAttributedString,
            NSURL,
        )
    except ImportError:
        return plain

    credits = NSMutableAttributedString.alloc().initWithString_(plain)
    link_start = len(_CREDITS_INTRO)
    link_len = len(_GITHUB_LABEL)
    if link_len <= 0 or link_start + link_len > len(plain):
        return credits

    url = NSURL.URLWithString_(_GITHUB_URL)
    # NSLinkAttributeName — clickable in NSAboutPanel credits.
    try:
        from AppKit import NSLinkAttributeName  # type: ignore[import-untyped]
    except ImportError:
        NSLinkAttributeName = "NSLink"  # type: ignore[misc, assignment]

    credits.addAttribute_value_range_(
        NSLinkAttributeName,
        url,
        (link_start, link_len),
    )
    return credits


def _about_options(image) -> dict:
    ver = pkg_version.package_version()
    options = {
        "ApplicationName": _DISPLAY_NAME,
        "ApplicationVersion": ver,
        "Version": ver,
        "Copyright": f"© {_COPYRIGHT}",
        "Credits": _credits_attributed(),
    }
    if image is not None:
        options["ApplicationIcon"] = image
    return options


_ABOUT_OPTIONS: dict | None = None
_BRANDING_TIMER = None


def _show_branded_about(_sender=None) -> None:
    try:
        import AppKit  # type: ignore[import-untyped]
    except ImportError:
        return
    opts = _ABOUT_OPTIONS
    if not opts:
        return
    AppKit.NSApp.orderFrontStandardAboutPanelWithOptions_(opts)


def _install_about_override(image) -> None:
    """Replace the system About action so it never falls back to ``python3``."""
    global _ABOUT_TARGET, _ABOUT_OPTIONS
    try:
        import objc  # type: ignore[import-untyped]
        import AppKit  # type: ignore[import-untyped]
        from Foundation import NSObject  # type: ignore[import-untyped]
    except ImportError:
        return

    options = _about_options(image)
    _ABOUT_OPTIONS = options

    # ObjC-runtime override (plain Python assignment is ignored by menu IMPs).
    def orderFrontStandardAboutPanel_(self, sender=None):  # noqa: ANN001, N802
        self.orderFrontStandardAboutPanelWithOptions_(options)

    orderFrontStandardAboutPanel_ = objc.selector(
        orderFrontStandardAboutPanel_,
        signature=b"v@:@",
    )
    AppKit.NSApplication.orderFrontStandardAboutPanel_ = orderFrontStandardAboutPanel_

    class _AboutTarget(NSObject):
        def showTesmartAbout_(self, sender) -> None:  # noqa: ARG002, N802
            _show_branded_about(sender)

    target = _AboutTarget.alloc().init()
    _ABOUT_TARGET = target

    app = AppKit.NSApplication.sharedApplication()
    menu = app.mainMenu()
    if menu is None or menu.numberOfItems() < 1:
        return
    # Walk every top-level menu; pywebview may not put About in index 0.
    for menu_index in range(menu.numberOfItems()):
        top = menu.itemAtIndex_(menu_index)
        submenu = top.submenu() if top is not None else None
        if submenu is None:
            continue
        for index in range(submenu.numberOfItems()):
            item = submenu.itemAtIndex_(index)
            if item is None:
                continue
            title = str(item.title() or "")
            if "About" in title:
                item.setTitle_(f"About {_DISPLAY_NAME}")
                item.setTarget_(target)
                item.setAction_("showTesmartAbout:")
                return


def _schedule_branding_retries() -> None:
    """Menus appear after webview starts; re-apply a few times on the main thread."""
    global _BRANDING_TIMER, _BRANDING_TIMER_TARGET
    if _BRANDING_TIMER_TARGET is not None:
        return
    try:
        from Foundation import NSObject, NSTimer  # type: ignore[import-untyped]
    except ImportError:
        return

    class _Retry(NSObject):
        def tick_(self, timer) -> None:  # noqa: ARG002, N802
            apply_branding(schedule_retries=False)

    retry = _Retry.alloc().init()
    _BRANDING_TIMER_TARGET = retry
    _BRANDING_TIMER = retry
    for delay in (0.25, 0.75, 1.5, 3.0):
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            delay, retry, "tick:", None, False
        )


def apply_branding(*, schedule_retries: bool = False) -> None:
    """Dock icon + About panel branding while the kit window is running."""
    if sys.platform != "darwin":
        return
    try:
        import AppKit  # type: ignore[import-untyped]
    except ImportError:
        return

    image = _load_app_icon()
    app = AppKit.NSApplication.sharedApplication()
    if image is not None:
        app.setApplicationIconImage_(image)
        try:
            app.dockTile().display()
        except Exception:
            pass
    try:
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    except Exception:
        pass
    try:
        from Foundation import NSProcessInfo  # type: ignore[import-untyped]

        NSProcessInfo.processInfo().setProcessName_(_DISPLAY_NAME)
    except Exception:
        pass
    _install_about_override(image)
    if schedule_retries:
        _schedule_branding_retries()


def apply_process_icon() -> None:
    """Backward-compatible alias for :func:`apply_branding`."""
    apply_branding(schedule_retries=True)
