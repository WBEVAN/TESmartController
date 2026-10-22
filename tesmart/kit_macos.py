"""macOS-only Dock branding and menu bar for the kit window.

The kit itself (``tesmart.kit_window``) is cross-platform. On Darwin, a bare
``python3.12`` process cannot replace the Dock document icon — Apple keys that
off the hosting ``.app`` bundle. This module builds a tiny launcher app under
``~/.config/tesmartctl/`` so Dock / Cmd-Tab show TESmart. The app's main
executable is a Mach-O stub that stays the process and loads the installed
Python, so Local Network permission belongs to ``com.tesmartctl.kit``. It also
rewires the About menu so the panel shows TESmart Kit (not ``python3``) and
adds an **Inputs** menu (listener input list + checkmark on the active one).
Linux and Windows never import this path.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import sysconfig
import threading
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from . import version as pkg_version
from .reach import open_url

_BUNDLE_ID = "com.tesmartctl.kit"
_APP_NAME = "TESmart Kit.app"
_DISPLAY_NAME = "TESmart Kit"
_COPYRIGHT = "Independent community project — not affiliated with TESmart."
_CREDITS_INTRO = "Control TESmart HDMI / KVM switches over LAN or RS232.\n"
_GITHUB_URL = "https://github.com/WBEVAN/TESmartController"
_GITHUB_LABEL = "tesmartctl on GitHub"

# Keep Cocoa targets alive for the process lifetime.
_ABOUT_TARGET = None
_BRANDING_TIMER_TARGET = None
_INPUTS_TARGET = None
_INPUTS_TIMER = None
_INPUTS_ATTACH_TIMER = None
_INPUTS_TIMERS_ARMED = False
_MENU_HOOKED = False
_LISTENER_BASE = ""
_INPUTS_SIG: tuple | None = None
_INPUTS_FETCHING = False
_MAIN_LOCK = threading.Lock()
_PEEK_SECONDS = 5
_PEEK_MODES = (("off", "Peek Off", 1001), ("once", "Peek Once", 1002), ("always", "Peek Always", 1003))
_PEEK_BY_TAG = {tag: mode for mode, _title, tag in _PEEK_MODES}
_INPUT_COUNT = 8
_CACHED_INPUTS: list[dict] | None = None
_CACHED_ACTIVE: int | None = None
_CACHED_MODE: str | None = None
_CONTROLLER_ONLINE = False
# mDNS instance the kit URL is bound to. Empty means any listener on this base.
_EXPECTED_MDNS = ""


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


_FAT_MAGIC = 0xCAFEBABE
_FAT_CIGAM = 0xBEBAFECA
_MH_MAGIC_64 = 0xFEEDFACF
_CPU_X86_64 = 0x01000007
_CPU_ARM64 = 0x0100000C


def _cpu_name(cputype: int) -> str | None:
    base = cputype & 0x00FFFFFF
    if cputype == _CPU_X86_64 or base == 0x00000007:
        return "x86_64"
    if cputype == _CPU_ARM64 or base == 0x0000000C:
        return "arm64"
    return None


def _mach_slices(data: bytes) -> dict[str, bytes]:
    """Map ``arm64`` / ``x86_64`` to the Mach-O slice bytes in ``data``."""
    if len(data) < 8:
        return {}
    magic = int.from_bytes(data[:4], "big")
    if magic in {_FAT_MAGIC, _FAT_CIGAM}:
        endian = "big" if magic == _FAT_MAGIC else "little"
        count = int.from_bytes(data[4:8], endian)
        found: dict[str, bytes] = {}
        for index in range(count):
            start = 8 + index * 20
            if start + 20 > len(data):
                break
            cputype = int.from_bytes(data[start : start + 4], endian)
            offset = int.from_bytes(data[start + 8 : start + 12], endian)
            size = int.from_bytes(data[start + 12 : start + 16], endian)
            name = _cpu_name(cputype)
            if name and offset + size <= len(data):
                found[name] = data[offset : offset + size]
        return found
    if int.from_bytes(data[:4], "little") == _MH_MAGIC_64:
        name = _cpu_name(int.from_bytes(data[4:8], "little"))
        return {name: data} if name else {}
    if int.from_bytes(data[:4], "big") == _MH_MAGIC_64:
        name = _cpu_name(int.from_bytes(data[4:8], "big"))
        return {name: data} if name else {}
    return {}


def _mach_archs(path: Path) -> list[str]:
    try:
        header = path.read_bytes()[: 8 + 20 * 8]
    except OSError:
        return []
    if len(header) >= 4 and int.from_bytes(header[:4], "big") in {_FAT_MAGIC, _FAT_CIGAM}:
        return list(_mach_slices(path.read_bytes()))
    return list(_mach_slices(header))


def _is_macho_file(path: Path) -> bool:
    return bool(_mach_archs(path))


def _packaged_stub() -> Path:
    path = Path(__file__).resolve().parent / "static" / "kit" / "macos" / "tesmart-kit"
    if not path.is_file():
        raise KitMacosError("TESmart Kit is missing its macOS launcher")
    return path


def _interpreter() -> Path:
    """Mach-O Python that is running tesmartctl, past a pyenv shim."""
    candidates = [Path(sys.executable)]
    base = getattr(sys, "_base_executable", None)
    if base:
        candidates.append(Path(base))
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file() and _is_macho_file(resolved):
            return resolved
    raise KitMacosError(f"could not find a Mach-O Python at {sys.executable}")


def _resolve_install_name(name: str, exe: Path, rpaths: list[Path]) -> Path | None:
    if name.startswith(("@executable_path/", "@loader_path/")):
        relative = name.split("/", 1)[1]
        return (exe.parent / relative).resolve()
    if name.startswith("@rpath/"):
        relative = name[len("@rpath/") :]
        for root in rpaths:
            candidate = (root / relative).resolve()
            if candidate.is_file():
                return candidate
        return None
    candidate = Path(name)
    if candidate.is_file():
        return candidate.resolve()
    return None


def _otool_rpaths(exe: Path) -> list[Path]:
    proc = subprocess.run(
        ["otool", "-l", str(exe)],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []
    rpaths: list[Path] = []
    lines = proc.stdout.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "cmd LC_RPATH":
            continue
        for follow in lines[index + 1 : index + 6]:
            stripped = follow.strip()
            if stripped.startswith("path "):
                path = stripped.split(" (offset", 1)[0].removeprefix("path ").strip()
                if path.startswith("@executable_path/"):
                    path = str(exe.parent / path.split("/", 1)[1])
                elif path.startswith("@loader_path/"):
                    path = str(exe.parent / path.split("/", 1)[1])
                rpaths.append(Path(path))
                break
    return rpaths


def _libpython_from_otool(exe: Path) -> Path | None:
    proc = subprocess.run(
        ["otool", "-L", str(exe)],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    rpaths = _otool_rpaths(exe)
    for line in proc.stdout.splitlines()[1:]:
        name = line.strip().split(" (compatibility", 1)[0].strip()
        if "libpython" not in name and not name.endswith("/Python"):
            continue
        resolved = _resolve_install_name(name, exe, rpaths)
        if resolved is not None and resolved.is_file():
            return resolved
    return None


def _libpython_candidates(exe: Path) -> list[Path]:
    candidates: list[Path] = []
    libdir = sysconfig.get_config_var("LIBDIR")
    ldlibrary = sysconfig.get_config_var("LDLIBRARY")
    if libdir and ldlibrary:
        candidates.append(Path(libdir) / str(ldlibrary))
    prefix = Path(sysconfig.get_config_var("prefix") or exe.parent.parent)
    framework = sysconfig.get_config_var("PYTHONFRAMEWORK")
    if framework:
        candidates.append(prefix / str(framework))
        candidates.append(prefix / "Python")
    version = f"libpython{sys.version_info.major}.{sys.version_info.minor}.dylib"
    candidates.append(exe.parent.parent / "lib" / version)
    return candidates


def _find_libpython(exe: Path) -> Path:
    found = _libpython_from_otool(exe)
    if found is not None:
        return found
    for candidate in _libpython_candidates(exe):
        if candidate.is_file() and _is_macho_file(candidate):
            return candidate.resolve()
    raise KitMacosError(f"could not find libpython for {exe}")


def _stub_for(executable: Path) -> bytes:
    """Launcher slice that matches ``executable`` so dlopen can load libpython."""
    packaged = _packaged_stub()
    slices = _mach_slices(packaged.read_bytes())
    if not slices:
        raise KitMacosError("TESmart Kit launcher is not a Mach-O executable")
    py_archs = _mach_archs(executable)
    if not py_archs:
        raise KitMacosError(f"could not read the CPU architecture of {executable}")
    if len(py_archs) == 1:
        arch = py_archs[0]
        if arch not in slices:
            raise KitMacosError(f"TESmart Kit has no {arch} launcher for {executable}")
        return slices[arch]
    if not set(py_archs) & set(slices):
        raise KitMacosError(f"TESmart Kit has no launcher for {', '.join(py_archs)}")
    return packaged.read_bytes()


def _plist_text() -> str:
    ver = pkg_version.package_version()
    return f"""<?xml version="1.0" encoding="UTF-8"?>
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
  <key>NSLocalNetworkUsageDescription</key><string>TESmart Kit connects to the tesmartctl listener on your local network.</string>
  <key>NSBonjourServices</key>
  <array>
    <string>_wbevan-tesmart._tcp</string>
  </array>
</dict>
</plist>
"""


def _same_text(path: Path, text: str) -> bool:
    try:
        return path.read_text(encoding="utf-8") == text
    except OSError:
        return False


def _codesign_ok(path: Path) -> bool:
    return (
        subprocess.run(  # noqa: S603 - fixed codesign verify
            ["codesign", "--verify", str(path)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def _sign(path: Path) -> None:
    proc = subprocess.run(  # noqa: S603 - fixed codesign of our app
        ["codesign", "--force", "--sign", "-", "--identifier", _BUNDLE_ID, str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise KitMacosError(f"could not sign {path.name}: {detail}")


def _refresh_icon(resources: Path) -> bool:
    """Copy the packaged icon when its bytes differ. True when the file changed."""
    source = _packaged_icns()
    if source is None:
        return False
    dest = resources / "AppIcon.icns"
    data = source.read_bytes()
    try:
        if dest.read_bytes() == data:
            return False
    except OSError:
        pass
    dest.write_bytes(data)
    return True


def ensure_kit_app() -> Path:
    """Create/update the Dock-facing ``.app`` (idempotent when nothing changed)."""
    if sys.platform != "darwin":
        raise KitMacosError("TESmart Kit.app is only used on macOS")

    root = app_root()
    contents = root / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)

    executable = _interpreter()
    libpython = _find_libpython(executable)
    stub = _stub_for(executable)
    plist = _plist_text()
    host = f"{libpython}\n{executable}\n{hashlib.sha256(stub).hexdigest()}\n"
    exe_path = macos / "tesmart-kit"
    plist_path = contents / "Info.plist"
    host_path = resources / "python-host"
    icon_changed = _refresh_icon(resources)
    current = (
        not icon_changed
        and _same_text(plist_path, plist)
        and _same_text(host_path, host)
        and _is_macho_file(exe_path)
        and _codesign_ok(root)
    )
    if current:
        return root

    exe_path.write_bytes(stub)
    exe_path.chmod(0o755)
    plist_path.write_text(plist, encoding="utf-8")
    host_path.write_text(host, encoding="utf-8")
    _sign(exe_path)
    _sign(root)
    if not _codesign_ok(root):
        raise KitMacosError("TESmart Kit.app signature did not verify")
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
    debug: bool = False,
    netdebug: bool = False,
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
    if debug:
        cmd.append("--debug")
    if netdebug:
        cmd.append("--netdebug")
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
    # Only ApplicationVersion — also setting Version makes macOS show "0.0.7.11 (0.0.7.11)".
    options = {
        "ApplicationName": _DISPLAY_NAME,
        "ApplicationVersion": ver,
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
    _install_inputs_menu()
    if schedule_retries:
        _schedule_branding_retries()


def apply_process_icon() -> None:
    """Backward-compatible alias for :func:`apply_branding`."""
    apply_branding(schedule_retries=True)


def set_listener_url(url: str) -> None:
    """Remember the kit's listener so the Inputs menu can list and select ports.

    ``url`` may be the full ``/kit?...`` page; only scheme and host are kept.
    ``ports`` / ``inputs`` on that URL sets how many rows to show before the
    listener answers.
    """
    global _LISTENER_BASE, _INPUTS_SIG, _INPUT_COUNT, _EXPECTED_MDNS
    from urllib.parse import parse_qs

    parts = urlsplit((url or "").strip())
    query = parse_qs(parts.query)
    expected = ((query.get("mdns") or [""])[0] or "").strip()
    name_changed = expected != _EXPECTED_MDNS
    _EXPECTED_MDNS = expected
    raw_ports = (query.get("ports") or query.get("inputs") or [None])[0]
    try:
        ports = int(raw_ports) if raw_ports is not None else 0
    except (TypeError, ValueError):
        ports = 0
    if 8 <= ports <= 32:
        _INPUT_COUNT = ports
    base = _base_from_kit_url(url)
    if base == _LISTENER_BASE:
        if name_changed:
            _refresh_inputs_async()
        _install_inputs_menu()
        return
    _LISTENER_BASE = base
    _INPUTS_SIG = None
    _install_inputs_menu()
    _refresh_inputs_async()


def _default_inputs() -> list[dict]:
    count = _INPUT_COUNT if 8 <= _INPUT_COUNT <= 32 else 8
    cached = _CACHED_INPUTS or []
    by_number = {int(item["number"]): item for item in cached if item.get("number") is not None}
    rows: list[dict] = []
    for number in range(1, count + 1):
        known = by_number.get(number)
        name = str(known.get("name") or "") if known else ""
        rows.append({"number": number, "name": name})
    return rows


def _base_from_kit_url(url: str) -> str:
    parts = urlsplit((url or "").strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def _call_on_main(fn) -> None:  # type: ignore[no-untyped-def]
    """Run ``fn`` on the AppKit main thread without blocking the caller."""
    try:
        import AppKit  # type: ignore[import-untyped]
    except ImportError:
        return
    if AppKit.NSThread.isMainThread():
        try:
            fn()
        except Exception:
            pass
        return
    try:
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(fn)
    except Exception:
        return


def _fetch_inputs() -> tuple[list[dict], int | None, str] | None:
    """Return ``(inputs, active, peek_mode)`` or ``None`` if the listener did not answer."""
    global _PEEK_SECONDS, _INPUT_COUNT, _CACHED_INPUTS, _CACHED_ACTIVE, _CACHED_MODE, _CONTROLLER_ONLINE
    base = _LISTENER_BASE
    if not base:
        return None
    request = urllib.request.Request(
        f"{base}/api/state?network=0",
        headers={"Accept": "application/json"},
    )
    try:
        with open_url(request, timeout=4.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        _CONTROLLER_ONLINE = False
        return None
    if not isinstance(payload, dict):
        return None
    from .mdns import mdns_identity_conflict

    # Same IP and port, different advertised name: this is not our controller.
    reported = payload.get("mdns_name")
    if mdns_identity_conflict(_EXPECTED_MDNS, reported if isinstance(reported, str) else None):
        _CONTROLLER_ONLINE = False
        return None

    inputs: list[dict] = []
    raw_inputs = payload.get("inputs")
    if isinstance(raw_inputs, list):
        for item in raw_inputs:
            if not isinstance(item, dict) or item.get("number") is None:
                continue
            try:
                number = int(item["number"])
            except (TypeError, ValueError):
                continue
            name = item.get("name") or ""
            inputs.append({"number": number, "name": str(name)})
    if not inputs:
        try:
            count = int(payload.get("input_count") or payload.get("inputCount") or 0)
        except (TypeError, ValueError):
            count = 0
        names = payload.get("names") if isinstance(payload.get("names"), dict) else {}
        for number in range(1, max(0, count) + 1):
            label = names.get(str(number), names.get(number, "")) if isinstance(names, dict) else ""
            inputs.append({"number": number, "name": str(label or "")})
    active_raw = payload.get("active_input", payload.get("activeInput"))
    try:
        active = int(active_raw) if active_raw is not None else None
    except (TypeError, ValueError):
        active = None
    peek = payload.get("peek") if isinstance(payload.get("peek"), dict) else {}
    mode = str(peek.get("follow") or "off").lower()
    if mode not in {"off", "once", "always"}:
        mode = "off"
    try:
        seconds = int(peek.get("seconds"))
    except (TypeError, ValueError):
        seconds = _PEEK_SECONDS
    if seconds >= 1:
        _PEEK_SECONDS = seconds
    if inputs:
        _INPUT_COUNT = len(inputs)
        _CACHED_INPUTS = inputs
    _CACHED_ACTIVE = active
    _CACHED_MODE = mode
    # active_input is null when the HDMI switch did not answer.
    _CONTROLLER_ONLINE = active is not None
    return inputs, active, mode


def _post_input(number: int) -> None:
    base = _LISTENER_BASE
    if not base:
        return
    body = json.dumps({"input": int(number)}).encode("utf-8")
    request = urllib.request.Request(
        f"{base}/api/input",
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with open_url(request, timeout=8.0) as response:
            response.read()
    except Exception:
        pass
    _refresh_inputs_async()


def _post_peek_mode(mode: str) -> None:
    base = _LISTENER_BASE
    if mode not in {"off", "once", "always"} or not base:
        return
    body: dict[str, object] = {"mode": mode}
    if mode != "off":
        body["seconds"] = int(_PEEK_SECONDS) if _PEEK_SECONDS >= 1 else 5
    request = urllib.request.Request(
        f"{base}/api/peek-follow",
        data=json.dumps(body).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with open_url(request, timeout=8.0) as response:
            response.read()
    except Exception:
        pass
    _refresh_inputs_async()


def _inputs_target():
    """Menu target + delegate. Created once so selectors stay registered."""
    global _INPUTS_TARGET
    if _INPUTS_TARGET is not None:
        return _INPUTS_TARGET
    try:
        from Foundation import NSObject  # type: ignore[import-untyped]
    except ImportError:
        return None

    class _InputsTarget(NSObject):
        def selectInput_(self, sender) -> None:  # noqa: N802
            if not _CONTROLLER_ONLINE:
                return
            try:
                number = int(sender.tag())
            except (TypeError, ValueError, AttributeError):
                return
            threading.Thread(target=_post_input, args=(number,), daemon=True).start()

        def selectPeekMode_(self, sender) -> None:  # noqa: N802
            if not _CONTROLLER_ONLINE:
                return
            try:
                mode = _PEEK_BY_TAG.get(int(sender.tag()))
            except (TypeError, ValueError, AttributeError):
                return
            if not mode:
                return
            threading.Thread(target=_post_peek_mode, args=(mode,), daemon=True).start()

        def menuWillOpen_(self, _menu) -> None:  # noqa: N802
            _refresh_inputs_async()

    _INPUTS_TARGET = _InputsTarget.alloc().init()
    return _INPUTS_TARGET


def _ensure_inputs_submenu():
    """Return the Inputs submenu, creating the top-level menu if the bar exists."""
    try:
        import AppKit  # type: ignore[import-untyped]
    except ImportError:
        return None
    app = AppKit.NSApplication.sharedApplication()
    menu = app.mainMenu()
    if menu is None:
        return None

    submenu = None
    for index in range(menu.numberOfItems()):
        item = menu.itemAtIndex_(index)
        if item is not None and str(item.title() or "") == "Inputs":
            submenu = item.submenu()
            break
    if submenu is None:
        submenu = AppKit.NSMenu.alloc().initWithTitle_("Inputs")
        top = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Inputs", None, "")
        top.setSubmenu_(submenu)
        insert_at = 1 if menu.numberOfItems() >= 1 else 0
        menu.insertItem_atIndex_(top, insert_at)
    try:
        submenu.setAutoenablesItems_(False)
    except Exception:
        pass
    target = _inputs_target()
    if target is not None:
        try:
            submenu.setDelegate_(target)
        except Exception:
            pass
    return submenu


def _menu_has_input_rows(submenu) -> bool:
    if submenu is None or submenu.numberOfItems() < 1:
        return False
    first = submenu.itemAtIndex_(0)
    if first is None:
        return False
    try:
        tag = int(first.tag())
    except (TypeError, ValueError):
        return False
    return 1 <= tag <= 32


def _fill_inputs_menu(
    inputs: list[dict],
    active: int | None,
    peek_mode: str = "off",
    *,
    include_peek: bool = True,
    inputs_enabled: bool = True,
) -> None:
    global _INPUTS_SIG
    submenu = _ensure_inputs_submenu()
    if submenu is None:
        return
    mode = peek_mode if peek_mode in {"off", "once", "always"} else "off"
    rows = inputs or _default_inputs()
    signature = (
        active,
        mode if include_peek else None,
        include_peek,
        inputs_enabled,
        tuple((int(item["number"]), str(item.get("name") or "")) for item in rows),
    )
    expected = len(rows) + 1 + len(_PEEK_MODES)
    if signature == _INPUTS_SIG and submenu.numberOfItems() == expected:
        return
    _INPUTS_SIG = signature

    target = _inputs_target()
    try:
        import AppKit  # type: ignore[import-untyped]
    except ImportError:
        return

    submenu.removeAllItems()
    for item in rows:
        number = int(item["number"])
        label = str(item.get("name") or "").strip() or f"INPUT {number}"
        title = f"{number:02d}  {label}"
        entry = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, "selectInput:", ""
        )
        if target is not None:
            entry.setTarget_(target)
        # Prefetched names stay visible while the switch is offline, but greyed out.
        entry.setEnabled_(bool(inputs_enabled))
        entry.setTag_(number)
        entry.setState_(1 if active is not None and number == active else 0)
        submenu.addItem_(entry)

    submenu.addItem_(AppKit.NSMenuItem.separatorItem())
    for key, title, tag in _PEEK_MODES:
        entry = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, "selectPeekMode:", ""
        )
        if target is not None:
            entry.setTarget_(target)
        entry.setEnabled_(bool(inputs_enabled))
        entry.setTag_(tag)
        # Checkmark is the menu-bar checkbox; only the active peek mode is on.
        entry.setState_(1 if key == mode else 0)
        submenu.addItem_(entry)


def _show_inputs_now() -> None:
    """Paint the Inputs menu. Rows stay disabled until the controller answers."""
    rows = list(_CACHED_INPUTS) if _CACHED_INPUTS else _default_inputs()
    _fill_inputs_menu(
        rows,
        _CACHED_ACTIVE,
        _CACHED_MODE or "off",
        include_peek=True,
        inputs_enabled=_CONTROLLER_ONLINE,
    )


def _refresh_inputs_async() -> None:
    """Fetch listener state off the main thread, then repaint the Inputs menu."""
    global _INPUTS_FETCHING

    def work() -> None:
        global _INPUTS_FETCHING
        try:
            result = _fetch_inputs()
            if result is None:
                rows = list(_CACHED_INPUTS) if _CACHED_INPUTS else _default_inputs()
                active = _CACHED_ACTIVE
                mode = _CACHED_MODE or "off"
                _call_on_main(
                    lambda: _fill_inputs_menu(
                        rows, active, mode, include_peek=True, inputs_enabled=False
                    )
                )
                return
            inputs, active, mode = result
            snapshot = list(inputs)
            enabled = active is not None
            _call_on_main(
                lambda: _fill_inputs_menu(
                    snapshot, active, mode, include_peek=True, inputs_enabled=enabled
                )
            )
        finally:
            with _MAIN_LOCK:
                _INPUTS_FETCHING = False

    with _MAIN_LOCK:
        if _INPUTS_FETCHING:
            return
        _INPUTS_FETCHING = True
    threading.Thread(target=work, daemon=True).start()


def _hook_main_menu() -> None:
    """Fill Inputs inside the menu bar pywebview installs.

    ``first_show`` builds App / Edit / View and calls ``setMainMenu:`` after
    the window's shown event. Anything inserted earlier is discarded, which
    is why Inputs used to appear only after a later timer. Painting here
    puts the rows on the bar before the kit contacts the listener.
    """
    global _MENU_HOOKED
    if _MENU_HOOKED:
        return
    try:
        import AppKit  # type: ignore[import-untyped]
        import objc  # type: ignore[import-untyped]
    except ImportError:
        return

    original = AppKit.NSApplication.instanceMethodForSelector_(b"setMainMenu:")

    def setMainMenu_(self, menu):  # noqa: ANN001, N802
        original(self, menu)
        try:
            _show_inputs_now()
            _arm_inputs_timers()
        except Exception:
            pass

    AppKit.NSApplication.setMainMenu_ = objc.selector(setMainMenu_, signature=b"v@:@")
    _MENU_HOOKED = True


def _arm_inputs_timers() -> None:
    """Keep Inputs on the bar and refresh it once the run loop is alive."""
    global _INPUTS_TIMER, _INPUTS_ATTACH_TIMER, _INPUTS_TIMERS_ARMED
    if _INPUTS_TIMERS_ARMED:
        return
    try:
        from Foundation import NSObject, NSTimer  # type: ignore[import-untyped]
    except ImportError:
        return

    class _AttachTimer(NSObject):
        def tick_(self, _timer) -> None:  # noqa: N802
            bar = _ensure_inputs_submenu()
            if bar is not None and not _menu_has_input_rows(bar):
                _show_inputs_now()

    class _InputsTimer(NSObject):
        def tick_(self, _timer) -> None:  # noqa: N802
            bar = _ensure_inputs_submenu()
            if bar is not None and not _menu_has_input_rows(bar):
                _show_inputs_now()
            _refresh_inputs_async()

    attach_target = _AttachTimer.alloc().init()
    _INPUTS_ATTACH_TIMER = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.15, attach_target, "tick:", None, True
    )
    _INPUTS_ATTACH_TIMER._tesmart_target = attach_target  # type: ignore[attr-defined]
    timer_target = _InputsTimer.alloc().init()
    _INPUTS_TIMER = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        2.0, timer_target, "tick:", None, True
    )
    _INPUTS_TIMER._tesmart_target = timer_target  # type: ignore[attr-defined]
    _INPUTS_TIMERS_ARMED = True


def _install_inputs_menu() -> None:
    """Show numbered input rows now; replace them when the controller answers."""
    _hook_main_menu()
    submenu = _ensure_inputs_submenu()
    if submenu is not None and not _menu_has_input_rows(submenu):
        _show_inputs_now()
    _refresh_inputs_async()
    try:
        import AppKit  # type: ignore[import-untyped]

        running = bool(AppKit.NSApp.isRunning())
    except Exception:
        running = False
    # Timers scheduled before NSApp.run() never fire. The menu hook arms them
    # at setMainMenu:, which is the last call before the run loop starts.
    if running:
        _arm_inputs_timers()
