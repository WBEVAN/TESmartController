"""Open the floating HDMI kit without normal browser chrome.

Browsers refuse to hide the address bar / title bar for an ordinary
``window.open`` popup (Safari especially; Chrome ``--app`` still keeps the
OS title bar on macOS). True frameless requires a native wrapper:

* ``pywebview`` (``pip install 'tesmartctl[kit]'``) — frameless window (preferred)
* Chromium ``--app=URL`` — no tab strip / address bar, but still has OS chrome

The listener launches these on the machine running ``tesmartctl`` when the
panel user clicks **Pop out**, or via ``tesmartctl kit``. Webview always runs
in a child process so Cocoa/AppKit can own the main thread.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .kit_prefs import KitPrefs

# Compact deck size (the kit remeasures after load and snaps to the panel).
DEFAULT_WIDTH = 380
DEFAULT_HEIGHT = 356
MIN_WIDTH = 280
MIN_HEIGHT = 160


class KitWindowError(RuntimeError):
    """No suitable chrome-less host was found or the launch failed."""


def _candidates() -> list[list[str]]:
    """Return argv prefixes that accept ``--app=URL`` (Chromium family)."""
    found: list[list[str]] = []
    if sys.platform == "darwin":
        for app in (
            "Google Chrome",
            "Google Chrome Canary",
            "Chromium",
            "Microsoft Edge",
            "Brave Browser",
            "Arc",
        ):
            path = Path(f"/Applications/{app}.app/Contents/MacOS/{app}")
            if path.is_file():
                found.append([str(path)])
        for name in ("chromium", "google-chrome", "microsoft-edge"):
            which = shutil.which(name)
            if which:
                found.append([which])
    elif sys.platform.startswith("linux"):
        for name in (
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
            "microsoft-edge",
            "brave-browser",
        ):
            which = shutil.which(name)
            if which:
                found.append([which])
    elif sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        program = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        program86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        for path in (
            Path(program) / "Google/Chrome/Application/chrome.exe",
            Path(program86) / "Google/Chrome/Application/chrome.exe",
            Path(local) / "Google/Chrome/Application/chrome.exe",
            Path(program) / "Microsoft/Edge/Application/msedge.exe",
            Path(program86) / "Microsoft/Edge/Application/msedge.exe",
        ):
            if path.is_file():
                found.append([str(path)])
    return found


def open_chromium_app(url: str, *, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT) -> str:
    """Launch Chromium ``--app=`` mode. Returns the browser path used."""
    last_error: Exception | None = None
    for argv in _candidates():
        cmd = [
            *argv,
            f"--app={url}",
            f"--window-size={width},{height}",
            "--new-window",
        ]
        try:
            subprocess.Popen(  # noqa: S603 - fixed browser binaries, URL is ours
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return argv[0]
        except OSError as exc:
            last_error = exc
            continue
    detail = f" ({last_error})" if last_error else ""
    raise KitWindowError(
        "no Chrome/Edge/Chromium found for a chrome-less --app window" + detail
    )


def _webview_available() -> bool:
    try:
        import webview  # noqa: F401
    except ImportError:
        return False
    return True


def _pointer_screen(webview_mod):  # type: ignore[no-untyped-def]
    """Screen under the mouse (helps portrait / multi-monitor placement)."""
    screens = list(getattr(webview_mod, "screens", []) or [])
    if not screens:
        return None
    if len(screens) == 1:
        return screens[0]
    px = py = None
    try:
        if sys.platform == "darwin":
            from Quartz import CGEventCreate, CGEventGetLocation  # type: ignore[import-untyped]

            loc = CGEventGetLocation(CGEventCreate(None))
            px, py = float(loc.x), float(loc.y)
        elif sys.platform == "win32":
            import ctypes

            class _POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            pt = _POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                px, py = float(pt.x), float(pt.y)
    except Exception:
        px = py = None
    if px is None or py is None:
        return None
    for screen in screens:
        left = float(screen.physical_x)
        top = float(screen.physical_y)
        right = left + float(screen.physical_width)
        bottom = top + float(screen.physical_height)
        if left <= px < right and top <= py < bottom:
            return screen
    return None


def _screen_containing(x: float, y: float, screens: list) -> object | None:
    for screen in screens:
        left = float(screen.physical_x)
        top = float(screen.physical_y)
        right = left + float(screen.physical_width)
        bottom = top + float(screen.physical_height)
        if left <= x < right and top <= y < bottom:
            return screen
    return screens[0] if screens else None


def _clamp_on_screen(
    x: int, y: int, width: int, height: int, screens: list
) -> tuple[int, int, int, int]:
    """Keep size/position on the display that currently owns the window.

    Portrait and mixed-DPI layouts otherwise let Cocoa shove a resized
    frameless window onto the primary landscape screen.
    """
    screen = _screen_containing(x + width / 2, y + height / 2, screens)
    if screen is None:
        screen = _screen_containing(x, y, screens)
    if screen is None:
        return x, y, max(280, width), max(160, height)

    left = int(screen.physical_x)
    top = int(screen.physical_y)
    sw = int(screen.physical_width)
    sh = int(screen.physical_height)
    # Leave a little margin so edges stay draggable on rotated displays.
    max_w = max(280, sw - 8)
    max_h = max(160, sh - 8)
    width = max(280, min(int(width), max_w, 3200))
    height = max(160, min(int(height), max_h, 1600))
    x = min(max(int(x), left), left + sw - width)
    y = min(max(int(y), top), top + sh - height)
    return x, y, width, height


def _clear_webview_http_cache() -> None:
    """Drop WKWebView disk cache so ``--fresh`` really re-fetches assets."""
    if sys.platform != "darwin":
        return
    try:
        import WebKit  # type: ignore[import-untyped]
        from Foundation import NSDate  # type: ignore[import-untyped]
    except ImportError:
        return

    def _do() -> None:
        store = WebKit.WKWebsiteDataStore.defaultDataStore()
        types = WebKit.WKWebsiteDataStore.allWebsiteDataTypes()
        since = NSDate.dateWithTimeIntervalSince1970_(0)

        def _done(_):
            return None

        store.removeDataOfTypes_modifiedSince_completionHandler_(types, since, _done)

    try:
        _cocoa_on_main(_do)
    except Exception:
        pass


def _use_supported_webview_transparency() -> None:
    """Redirect pywebview's deprecated transparent-background call.

    pywebview still does ``setValue_forKey_(True, 'drawsTransparentBackground')``.
    WebKit logs ``-[WKWebView _setDrawsTransparentBackground:] is deprecated``
    for that private setter; it only turns ``drawsBackground`` off. Use the
    supported pair instead, so the log line never happens:

    * ``drawsBackground = NO`` — the web view does not paint its own backing
    * ``underPageBackgroundColor = clear`` (macOS 13.3+) — public color behind
      the page
    """
    if sys.platform != "darwin":
        return
    try:
        import AppKit  # type: ignore[import-untyped]
        import WebKit  # type: ignore[import-untyped]
    except ImportError:
        return

    view_cls = WebKit.WKWebView
    if getattr(view_cls, "_tesmart_transparency", False):
        return
    orig = view_cls.instanceMethodForSelector_(b"setValue:forKey:")

    def setValue_forKey_(self, value, key):  # noqa: N802 - ObjC selector
        if key == "drawsTransparentBackground":
            orig(self, not bool(value), "drawsBackground")
            if value and self.respondsToSelector_(b"setUnderPageBackgroundColor:"):
                self.setUnderPageBackgroundColor_(AppKit.NSColor.clearColor())
            return None
        return orig(self, value, key)

    view_cls.setValue_forKey_ = setValue_forKey_
    view_cls._tesmart_transparency = True


# Compact / the boot deck. The live page replaces this with the computed
# chassis radius (Mini 18, Compact 22, Full 30).
_WIN_CLIP_RADIUS_CSS = 22.0
_win_clip_delegates: list = []
_win_clip_hooked: set[int] = set()


def clip_corner_px(css_radius: float, dpi: int) -> int:
    """Device pixels for one chassis corner at this window DPI.

    CSS ``border-radius`` is in CSS pixels. The Win32 region is in physical
    pixels, and pywebview sizes the form the same way (``width * dpi / 96``).
    """
    scale = max(int(dpi), 1) / 96.0
    return max(1, int(round(float(css_radius) * scale)))


def _win_set_radius(radius: float) -> None:
    """Remember the deck corner radius. Ignored off Windows."""
    global _WIN_CLIP_RADIUS_CSS
    if sys.platform != "win32":
        return
    try:
        value = float(radius)
    except (TypeError, ValueError):
        return
    if value < 1 or value > 80:
        return
    _WIN_CLIP_RADIUS_CSS = value


def _win_on_ui(form, fn) -> None:  # type: ignore[no-untyped-def]
    """Run ``fn`` on the WinForms UI thread. js_api calls are not on it."""
    try:
        if form.InvokeRequired:
            from System import Action  # type: ignore[import-untyped]

            action = Action(fn)
            # Keep the delegate alive; BeginInvoke returns before it runs.
            _win_clip_delegates.append(action)
            form.BeginInvoke(action)
            return
    except Exception:
        pass
    fn()


_win_gdi_dlls = None


def _win_gdi():  # type: ignore[no-untyped-def]
    """Private user32/gdi32 with pointer-sized handles.

    pywebview's shared ``ctypes.windll`` must not get ``argtypes`` from here.
    ``CreateRoundRectRgn`` returns an ``HRGN``; the ctypes default ``c_int``
    restype would truncate that pointer on 64-bit Windows.
    """
    global _win_gdi_dlls
    if _win_gdi_dlls is not None:
        return _win_gdi_dlls
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetDpiForWindow.argtypes = [ctypes.c_void_p]
    user32.GetDpiForWindow.restype = ctypes.c_uint
    user32.SetWindowRgn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL]
    user32.SetWindowRgn.restype = ctypes.c_int
    gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
    gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteObject.restype = wintypes.BOOL
    _win_gdi_dlls = (user32, gdi32)
    return _win_gdi_dlls


def _win_clip_form(form) -> None:  # type: ignore[no-untyped-def]
    """Clip this borderless form to the chassis radius. UI thread only."""
    import ctypes
    from ctypes import wintypes

    try:
        hwnd = int(form.Handle.ToInt64())
    except Exception:
        return
    if not hwnd:
        return
    try:
        user32, gdi32 = _win_gdi()
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width < 2 or height < 2:
            return
        try:
            dpi = int(user32.GetDpiForWindow(hwnd)) or 96
        except Exception:
            dpi = 96
        radius = min(clip_corner_px(_WIN_CLIP_RADIUS_CSS, dpi), width // 2, height // 2)
        # CreateRoundRectRgn omits the right and bottom edges, and its ellipse
        # arguments are the corner diameter.
        region = gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, radius * 2, radius * 2)
        if not region:
            return
        # The system takes ownership of the region when the call succeeds.
        if not user32.SetWindowRgn(hwnd, region, True):
            gdi32.DeleteObject(region)
    except Exception:
        return


def _win_ensure_clip(window) -> None:  # type: ignore[no-untyped-def]
    """Apply the rounded clip, and keep it applied across resizes."""
    if sys.platform != "win32" or window is None:
        return
    form = getattr(window, "native", None)
    if form is None:
        return

    def arm() -> None:
        key = id(form)
        if key not in _win_clip_hooked:
            _win_clip_hooked.add(key)

            def on_resize(_sender, _args) -> None:
                _win_clip_form(form)

            _win_clip_delegates.append(on_resize)
            try:
                form.Resize += on_resize
            except Exception:
                pass
        _win_clip_form(form)

    _win_on_ui(form, arm)


def _cocoa_on_main(fn):  # type: ignore[no-untyped-def]
    """Run ``fn`` on the AppKit main thread (js_api callbacks are not main).

    Calling NSWindow APIs off-main raises SIGTRAP:
    ``Must only be used from the main thread``.
    """
    if sys.platform != "darwin":
        return fn()
    try:
        import AppKit  # type: ignore[import-untyped]
        from Foundation import NSObject  # type: ignore[import-untyped]
    except ImportError:
        return fn()

    if AppKit.NSThread.isMainThread():
        return fn()

    box: dict = {}

    class _Runner(NSObject):  # type: ignore[misc, valid-type]
        def run_(self, _arg):  # noqa: N802 - ObjC selector
            try:
                box["value"] = fn()
            except Exception as exc:  # pragma: no cover - surfaced to caller
                box["error"] = exc

    runner = _Runner.alloc().init()
    runner.performSelectorOnMainThread_withObject_waitUntilDone_("run:", None, True)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _tesmart_nswindows():  # type: ignore[no-untyped-def]
    import AppKit  # type: ignore[import-untyped]

    found = []
    for nsw in AppKit.NSApp.windows():
        try:
            title = str(nsw.title() or "")
            if title.startswith("TESmart") and nsw.isVisible():
                found.append(nsw)
        except Exception:
            continue
    return found


def _cocoa_drag_by(dx: float, dy: float) -> bool:
    """Translate the TESmart window by pointer delta (Y down). Main-thread only."""

    def _do() -> bool:
        import AppKit  # type: ignore[import-untyped]

        for nsw in _tesmart_nswindows():
            origin = nsw.frame().origin
            nsw.setFrameOrigin_(AppKit.NSMakePoint(origin.x + dx, origin.y - dy))
            return True
        return False

    try:
        return bool(_cocoa_on_main(_do))
    except Exception:
        return False


def _cocoa_top_left() -> tuple[float, float] | None:
    """Window left edge and top edge in AppKit screen coordinates (Y up)."""

    def _do():  # type: ignore[no-untyped-def]
        for nsw in _tesmart_nswindows():
            frame = nsw.frame()
            return (
                float(frame.origin.x),
                float(frame.origin.y) + float(frame.size.height),
            )
        return None

    try:
        return _cocoa_on_main(_do)
    except Exception:
        return None


def _cocoa_restore_top_left(x: float, top: float) -> bool:
    """Put the window back at a saved top-left if that spot is still on a display.

    A monitor that was unplugged or rearranged leaves the saved point off-screen;
    in that case the window stays where pywebview opened it.
    """

    def _do() -> bool:
        import AppKit  # type: ignore[import-untyped]

        probe = AppKit.NSMakePoint(x + 40.0, top - 20.0)
        if not any(
            AppKit.NSPointInRect(probe, screen.frame()) for screen in AppKit.NSScreen.screens()
        ):
            return False
        for nsw in _tesmart_nswindows():
            height = float(nsw.frame().size.height)
            nsw.setFrameOrigin_(AppKit.NSMakePoint(x, top - height))
            return True
        return False

    try:
        return bool(_cocoa_on_main(_do))
    except Exception:
        return False


def _cocoa_set_min_size(width: int, height: int) -> bool:
    """Smallest size the user may drag the TESmart window to. Main-thread only."""

    def _do() -> bool:
        import AppKit  # type: ignore[import-untyped]

        for nsw in _tesmart_nswindows():
            nsw.setMinSize_(AppKit.NSMakeSize(float(width), float(height)))
            return True
        return False

    try:
        return bool(_cocoa_on_main(_do))
    except Exception:
        return False


def _cocoa_resize_keeping_top_left(width: int, height: int) -> bool:
    """Resize the TESmart NSWindow without moving its top-left corner."""

    def _do() -> bool:
        import AppKit  # type: ignore[import-untyped]

        for nsw in _tesmart_nswindows():
            frame = nsw.frame()
            top = float(frame.origin.y) + float(frame.size.height)
            nsw.setFrame_display_(
                AppKit.NSMakeRect(
                    float(frame.origin.x),
                    top - float(height),
                    float(width),
                    float(height),
                ),
                True,
            )
            # Transparent window: recompute the shadow from the rounded deck.
            nsw.invalidateShadow()
            return True
        return False

    try:
        return bool(_cocoa_on_main(_do))
    except Exception:
        return False


def _with_query(url: str, **params: str) -> str:
    """Return ``url`` with ``params`` added to its query string."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _kit_static_dir() -> Path:
    return Path(__file__).resolve().parent / "static" / "kit"


def _boot_page_url(live_url: str, *, transparent: bool = True, discover: bool = False) -> str:
    """``file://`` boot deck that paints without a network, then hands off to ``live_url``."""
    from urllib.parse import urlencode

    boot = _kit_static_dir() / "boot.html"
    if not boot.is_file():
        raise KitWindowError(f"kit boot shell missing: {boot}")
    params: dict[str, str] = {"app": "1"}
    if live_url and not _is_discover_placeholder(live_url):
        params["target"] = live_url
    if transparent:
        params["transparent"] = "1"
    if discover:
        params["discover"] = "1"
    return f"{boot.as_uri()}?{urlencode(params)}"


def _is_discover_placeholder(url: str) -> bool:
    """True for the stand-in URL used when ``--via auto`` has not found a listener yet."""
    from urllib.parse import urlsplit

    parts = urlsplit(url or "")
    return parts.hostname in {"127.0.0.1", "localhost"} and parts.port == 9


def _origin(url: str) -> str:
    """``scheme://host:port`` for an http(s) URL, or empty."""
    from urllib.parse import urlsplit

    parts = urlsplit(url or "")
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}".lower()


def _kit_page_url(base: str, *, template: str = "", discover: bool = False) -> str:
    """Build the live ``/kit`` URL for a listener base, keeping launch flags."""
    from urllib.parse import parse_qsl, urlencode, urlsplit

    root = (base or "").rstrip("/")
    if root.endswith("/kit"):
        root = root[: -len("/kit")]
    keep = dict(parse_qsl(urlsplit(template or "").query, keep_blank_values=True))
    query = {"app": "1", "transparent": "1"}
    if discover or "discover" in keep:
        query["discover"] = "1"
    for key in ("ports", "inputs", "fresh", "mdns"):
        if keep.get(key):
            query[key] = keep[key]
    return f"{root}/kit?{urlencode(query)}"


def _discover_listener_base(name: str | None = None) -> str | None:
    """Browse mDNS for a listener base URL. ``None`` when nothing matches.

    ``name`` is the instance from this launch (``--mdns-name``). It wins over
    the saved name. A set name never falls through to a different listener.
    """
    try:
        from . import config
        from .mdns import MdnsError, resolve_listener
    except ImportError:
        return None
    chosen = (name or "").strip() or (config.read_mdns_name() or "").strip() or None
    try:
        service = resolve_listener(
            name=chosen,
            prefer_url=config.read_via_url(),
            timeout=1.2,
        )
    except MdnsError:
        return None
    return service.base_url.rstrip("/")


def _remember_via(base: str) -> None:
    """Store a listener we actually attached to, so the next launch prefers it."""
    new = (base or "").rstrip("/")
    if not new:
        return
    try:
        from . import config

        current = (config.read_via_url() or "").rstrip("/")
        if new != current:
            config.write_via_url(new)
    except Exception:
        return


def _expected_mdns_name(url: str) -> str:
    """mDNS instance this kit URL is bound to, or empty when it has none."""
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(url or "").query)
    return ((query.get("mdns") or [""])[0] or "").strip()


def _reported_mdns_name(url: str, *, timeout: float) -> str | None:
    """Advertised instance name from ``GET /version``, or None when not reported."""
    import json
    from urllib.request import Request

    from .reach import open_url

    origin = _origin(url)
    if not origin:
        return None
    try:
        req = Request(f"{origin}/version", headers={"Accept": "application/json"})
        with open_url(req, timeout=timeout) as res:
            payload = json.loads(res.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    reported = payload.get("mdns_name")
    if not isinstance(reported, str):
        return None
    text = reported.strip()
    return text or None


def probe_failure_detail(url: str, message: str) -> str:
    """Attach the host the kit dialed so a bare OS error names its target."""
    from urllib.parse import urlsplit

    host = urlsplit(url or "").netloc
    text = (message or "Connection failed").strip() or "Connection failed"
    if host and host not in text:
        return f"{text} · {host}"
    return text


def _probe_live(url: str, *, timeout: float = 1.5) -> tuple[bool, str]:
    """Return ``(ok, detail)`` for whether the live kit URL answers.

    When the URL carries ``mdns=NAME``, an answer from a listener advertising
    a different instance name is a failed connection. The same host and port
    coming back under that other name must not be treated as ready.
    """
    from urllib.error import HTTPError, URLError
    from urllib.request import Request

    from .debug import log
    from .reach import open_url

    log(f"probe {url}")
    try:
        req = Request(url, headers={"Accept": "text/html,application/json,*/*"})
        with open_url(req, timeout=timeout) as res:
            status = getattr(res, "status", None) or res.getcode()
            if 200 <= int(status) < 400:
                expected = _expected_mdns_name(url)
                if expected:
                    from .mdns import mdns_identity_conflict

                    reported = _reported_mdns_name(url, timeout=timeout)
                    if mdns_identity_conflict(expected, reported):
                        detail = probe_failure_detail(url, f"Wrong mDNS name ({reported})")
                        log(f"probe fail {detail}")
                        return False, detail
                log("probe ok")
                return True, "Listener ready"
            detail = probe_failure_detail(url, f"HTTP {status}")
            log(f"probe fail {detail}")
            return False, detail
    except HTTPError as exc:
        # 404 usually means listen without --panel.
        if exc.code == 404:
            detail = probe_failure_detail(url, "Listener up · /kit not enabled (need --panel)")
        else:
            detail = probe_failure_detail(url, f"HTTP {exc.code}")
        log(f"probe fail {detail}")
        return False, detail
    except URLError as exc:
        reason = exc.reason
        if isinstance(reason, OSError) and getattr(reason, "errno", None):
            text = reason.strerror or str(reason)
            errno = getattr(reason, "errno", None)
            detail = probe_failure_detail(url, text)
            log(f"probe fail errno={errno} {detail}")
            return False, detail
        detail = probe_failure_detail(url, str(reason or exc))
        log(f"probe fail {detail}")
        return False, detail
    except TimeoutError:
        detail = probe_failure_detail(url, "Timed out")
        log(f"probe fail {detail}")
        return False, detail
    except Exception as exc:  # noqa: BLE001 - surface any probe failure on the LCD
        detail = probe_failure_detail(url, str(exc) or exc.__class__.__name__)
        log(f"probe fail {detail}")
        return False, detail


class _WindowMemory:
    """Save and restore where the kit window sits (via :class:`KitPrefs`)."""

    SAVE_DELAY = 0.5

    def __init__(self, webview_mod, prefs: KitPrefs) -> None:  # type: ignore[no-untyped-def]
        self._webview = webview_mod
        self._prefs = prefs
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _current(self) -> tuple[float, float] | None:
        if sys.platform == "darwin":
            return _cocoa_top_left()
        for window in self._webview.windows:
            try:
                return float(window.x), float(window.y)
            except Exception:
                return None
        return None

    def save_now(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        pos = self._current()
        if pos is not None:
            self._prefs.update({"position": {"x": pos[0], "y": pos[1]}})

    def save_soon(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.SAVE_DELAY, self.save_now)
            self._timer.daemon = True
            self._timer.start()

    def restore(self) -> None:
        pos = self._prefs.get("position")
        if not pos:
            return
        if sys.platform == "darwin":
            if not _cocoa_restore_top_left(pos["x"], pos["y"]):
                # Unplugged / rearranged display — drop the bad coords for next time.
                self._prefs.update({"position": None})
            return
        screens = list(getattr(self._webview, "screens", []) or [])
        x, y = int(pos["x"]), int(pos["y"])
        if screens:
            x, y, _, _ = _clamp_on_screen(x, y, DEFAULT_WIDTH, DEFAULT_HEIGHT, screens)
            if (x, y) != (int(pos["x"]), int(pos["y"])):
                self._prefs.update({"position": {"x": float(x), "y": float(y)}})
        for window in self._webview.windows:
            try:
                window.move(x, y)
            except Exception:
                pass


def run_webview(
    url: str,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fresh: bool = False,
) -> None:
    """Block on a frameless pywebview window (must run on the process main thread).

    Position, Always on top, view size, and peek-follow are remembered in
    :mod:`tesmart.kit_prefs`; ``fresh`` ignores them for this launch.
    """
    try:
        import webview  # type: ignore[import-untyped]
    except ImportError as exc:
        raise KitWindowError(
            "pywebview is not installed; pip install 'tesmartctl[kit]'"
        ) from exc

    _use_supported_webview_transparency()
    if fresh:
        # Bypass disk cache for this launch; the URL also carries fresh=1 so
        # the server stamps a new ?v= token on every /static asset.
        _clear_webview_http_cache()
        url = _with_query(url, fresh="1")
    def _brand_macos(*, schedule_retries: bool = False) -> None:
        if sys.platform != "darwin":
            return
        try:
            from . import kit_macos

            _cocoa_on_main(lambda: kit_macos.apply_branding(schedule_retries=schedule_retries))
        except Exception:
            pass

    # Early Dock icon; schedule About rewires until the app menu exists.
    _brand_macos(schedule_retries=True)
    prefs = KitPrefs(fresh=fresh)
    memory = _WindowMemory(webview, prefs)
    live_url = _with_query(url, transparent="1")
    from urllib.parse import parse_qs, urlsplit

    launch_query = parse_qs(urlsplit(url).query)
    discover = "discover" in launch_query
    # ``--mdns-name`` for this launch. Empty means "use the saved name".
    wanted_name = ((launch_query.get("mdns") or [""])[0] or "").strip()
    live = {"url": "" if _is_discover_placeholder(live_url) else live_url}
    if sys.platform == "darwin" and live["url"]:
        try:
            from . import kit_macos

            kit_macos.set_listener_url(live["url"])
        except Exception:
            pass
    # Fast path: listener already answers → open live /kit. Otherwise paint the
    # packaged boot deck (file://) so the window is never a blank Chromium page.
    ready, _ = (False, "")
    if live["url"]:
        ready, _ = _probe_live(live["url"], timeout=0.4)
    entry_url = live["url"] if ready and live["url"] else _boot_page_url(
        live["url"], transparent=True, discover=discover
    )
    adopt_lock = threading.Lock()

    def _adopt_moved_listener(*, navigate: bool) -> str:
        """Attach to the listener advertising now when the saved one is down.

        With ``navigate``, reload the window only when the host or port
        changed. The same endpoint is left to the page poll, which already
        recovers it, unless that process advertises a different mDNS name
        than the one on this kit URL. That probe fails and the kit stays
        offline until the named listener answers. Returns the kit page URL,
        or empty when nothing new was adopted.
        """
        if not discover or not adopt_lock.acquire(blocking=False):
            return ""
        try:
            current = live["url"]
            reachable = (
                bool(current)
                and not _is_discover_placeholder(current)
                and _probe_live(current, timeout=0.8)[0]
            )
            if reachable:
                return "" if navigate else current
            base = _discover_listener_base(wanted_name)
            if not base:
                return ""
            page = _kit_page_url(base, template=current, discover=True)
            if not _probe_live(page, timeout=1.2)[0]:
                return ""
            moved = _origin(page) != _origin(current)
            if navigate and not moved:
                return ""
            live["url"] = page
            _remember_via(base)
            if sys.platform == "darwin":
                try:
                    from . import kit_macos

                    _cocoa_on_main(lambda: kit_macos.set_listener_url(page))
                except Exception:
                    pass
            if navigate and moved:
                for window in list(webview.windows):
                    try:
                        window.load_url(page)
                        break
                    except Exception:
                        continue
            return page
        finally:
            adopt_lock.release()

    def _watch_moved_listener() -> None:
        # Let the boot shell paint before the first browse.
        time.sleep(2.5)
        while True:
            try:
                _adopt_moved_listener(navigate=True)
            except Exception:
                pass
            time.sleep(3.0)

    class _Api:
        def get_prefs(self) -> dict:
            """Saved kit settings for the page (position stays native-side)."""
            data = prefs.snapshot()
            data.pop("position", None)
            data["fresh"] = prefs.fresh
            return data

        def set_prefs(self, values: dict) -> dict:
            allowed = {"view", "peek_follow", "peek_seconds", "body", "lcd"}
            prefs.update({k: v for k, v in dict(values or {}).items() if k in allowed})
            return self.get_prefs()

        def probe_listener(self) -> dict:
            """Boot shell: is the live kit URL reachable? Not persisted.

            ``--via auto`` sets ``discover=1`` for the whole window, including
            after the first connection. A failed probe browses mDNS and adopts
            the same instance when it appears on a new host or port.
            """
            current = live["url"]
            if current:
                ok, detail = _probe_live(current, timeout=1.5)
                if ok:
                    return {"ok": True, "detail": "Listener ready", "url": current}
            else:
                ok, detail = False, "Looking for listener…"
            if not discover:
                return {"ok": False, "detail": detail, "url": current}
            found = _adopt_moved_listener(navigate=False)
            if not found:
                return {"ok": False, "detail": detail or "Looking for listener…", "url": current}
            return {"ok": True, "detail": "Listener found", "url": found}

        def rediscover_listener(self) -> dict:
            """Live kit: while offline, browse again and follow a moved listener.

            Same host and port is left alone when it is still this client's
            mDNS name — the page poll already retries it. A different name
            on that address fails the probe, so the named listener is sought
            again and the kit stays offline until it appears.
            """
            if not discover:
                return {"ok": False, "moved": False, "url": live["url"]}
            found = _adopt_moved_listener(navigate=True)
            return {"ok": bool(found), "moved": bool(found), "url": live["url"]}

        def load_live(self) -> bool:
            """Swap the boot shell for the live listener /kit page."""
            target = live["url"]
            if not target:
                return False
            for window in webview.windows:
                try:
                    window.load_url(target)
                    return True
                except Exception:
                    continue
            return False

        def close(self) -> None:
            memory.save_now()
            for window in list(webview.windows):
                try:
                    window.destroy()
                except Exception:
                    pass
            # Frameless Cocoa windows can linger as an empty shell after destroy.
            os._exit(0)

        def set_on_top(self, on: bool) -> None:
            for window in webview.windows:
                window.on_top = bool(on)
            prefs.update({"on_top": bool(on)})

        def set_window_clip(self, radius: float) -> None:
            """Clip the Windows window to the deck's corner radius (CSS px)."""
            _win_set_radius(radius)
            for item in webview.windows:
                _win_ensure_clip(item)

        def drag_by(self, dx: float, dy: float) -> None:
            """Move by pointer delta (browser screen coords: Y grows downward).

            Bypasses pywebview cocoa ``move()`` screen-origin double-count
            (https://github.com/r0x0r/pywebview/issues/1820). AppKit calls are
            marshalled onto the main thread — js_api runs off-main and would
            otherwise SIGTRAP (``Must only be used from the main thread``).
            """
            dx_f = float(dx)
            dy_f = float(dy)
            if not dx_f and not dy_f:
                return
            memory.save_soon()
            if _cocoa_drag_by(dx_f, dy_f):
                return
            for window in webview.windows:
                try:
                    window.move(int(window.x + dx_f), int(window.y + dy_f))
                except Exception:
                    pass

        def resize_to(self, width: int, height: int) -> None:
            screens = list(getattr(webview, "screens", []) or [])
            width = max(280, min(int(width), 3200))
            height = max(160, min(int(height), 1600))
            # Clamp to the display under the window when we know it.
            for window in webview.windows:
                try:
                    ox = int(window.x)
                    oy = int(window.y)
                    _, _, width, height = _clamp_on_screen(ox, oy, width, height, screens)
                except Exception:
                    pass
                break
            if _cocoa_resize_keeping_top_left(width, height):
                return
            for window in webview.windows:
                try:
                    window.resize(width, height)
                except Exception:
                    pass

        def set_min_size(self, width: int, height: int) -> None:
            """Apply the kit's resize floor (Full≥Compact, Compact≥Mini, Mini≥Mini)."""
            width = max(MIN_WIDTH, min(int(width), 3200))
            height = max(MIN_HEIGHT, min(int(height), 1600))
            if _cocoa_set_min_size(width, height):
                return
            for window in webview.windows:
                try:
                    window.min_size = (width, height)
                except Exception:
                    pass

        def screen_size(self) -> dict[str, int]:
            """CSS-ish bounds for the screen that currently owns the window."""
            screens = list(getattr(webview, "screens", []) or [])
            for window in webview.windows:
                try:
                    ox = int(window.x)
                    oy = int(window.y)
                    ow = int(window.width)
                    oh = int(window.height)
                except Exception:
                    continue
                screen = _screen_containing(ox + ow / 2, oy + oh / 2, screens)
                if screen is None:
                    continue
                return {
                    "width": int(screen.physical_width),
                    "height": int(screen.physical_height),
                    "x": int(screen.physical_x),
                    "y": int(screen.physical_y),
                }
            if screens:
                s0 = screens[0]
                return {
                    "width": int(s0.physical_width),
                    "height": int(s0.physical_height),
                    "x": int(s0.physical_x),
                    "y": int(s0.physical_y),
                }
            return {"width": 1200, "height": 800, "x": 0, "y": 0}

    target_screen = _pointer_screen(webview)
    create_kwargs: dict = {
        "frameless": True,
        # Kit JS owns dragging via api.drag_by (cocoa native drag-region jumps
        # on secondary monitors — pywebview#1820). Keep easy_drag off.
        "easy_drag": False,
        "on_top": bool(prefs.get("on_top", False)),
        # The page is transparent so only the rounded chassis is painted.
        # macOS composites that shape. Windows still owns a rectangular HWND,
        # so the window is clipped to the chassis radius (see _win_ensure_clip).
        "transparent": True,
        "background_color": "#10151b",
        "js_api": _Api(),
        # Floor only; the kit raises it to the current view's size after load.
        "min_size": (MIN_WIDTH, MIN_HEIGHT),
    }
    if target_screen is not None:
        create_kwargs["screen"] = target_screen
        # Fit the requested size onto the pointer's display (portrait-safe).
        width = max(280, min(int(width), int(target_screen.physical_width) - 8))
        height = max(160, min(int(height), int(target_screen.physical_height) - 8))

    window = webview.create_window(
        "TESmart Kit",
        entry_url,
        width=width,
        height=height,
        **create_kwargs,
    )
    events = getattr(window, "events", None)
    if events is not None:
        if hasattr(events, "shown"):
            events.shown += memory.restore
            events.shown += lambda *_: _brand_macos(schedule_retries=True)
            events.shown += lambda *_: _win_ensure_clip(window)
        if hasattr(events, "resized"):
            events.resized += lambda *_: _win_ensure_clip(window)
        if hasattr(events, "moved"):
            events.moved += lambda *_: memory.save_soon()
            events.moved += lambda *_: _win_ensure_clip(window)
        if hasattr(events, "closing"):
            events.closing += memory.save_now
    if discover:
        threading.Thread(
            target=_watch_moved_listener,
            name="tesmart-kit-discover",
            daemon=True,
        ).start()
    webview.start(debug=False)


def open_webview_process(
    url: str,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fresh: bool = False,
    wait: bool = False,
    debug: bool = False,
    netdebug: bool = False,
) -> str:
    """Spawn a child process that owns the frameless webview event loop."""
    if not _webview_available():
        raise KitWindowError(
            "pywebview is not installed; pip install 'tesmartctl[kit]' "
            "or use Chrome/Edge --app mode"
        )
    # Darwin-only Dock branding (see kit_macos). Other OSes use this same
    # python -m entrypoint unchanged.
    if sys.platform == "darwin":
        from . import kit_macos

        try:
            kit_macos.open_kit_app(
                url,
                width=width,
                height=height,
                fresh=fresh,
                wait=wait,
                debug=debug,
                netdebug=netdebug,
            )
        except kit_macos.KitMacosError as exc:
            raise KitWindowError(str(exc)) from exc
        return "pywebview"
    cmd = [
        sys.executable,
        "-m",
        "tesmart.kit_window",
        "--url",
        url,
        "--width",
        str(width),
        "--height",
        str(height),
    ]
    if fresh:
        cmd.append("--fresh")
    if debug:
        cmd.append("--debug")
    if netdebug:
        cmd.append("--netdebug")
    try:
        if wait:
            proc = subprocess.Popen(cmd)  # noqa: S603 - our own module entrypoint
            try:
                proc.wait()
            except KeyboardInterrupt:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1)
                raise
        else:
            subprocess.Popen(  # noqa: S603 - our own module entrypoint
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except OSError as exc:
        raise KitWindowError(f"could not start pywebview process: {exc}") from exc
    return "pywebview"


def open_kit_window(
    url: str,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    prefer: str = "auto",
    block: bool = False,
    fresh: bool = False,
    debug: bool = False,
    netdebug: bool = False,
) -> dict[str, str]:
    """Open the kit URL without normal browser chrome.

    ``prefer`` is ``auto`` (frameless webview, then Chromium ``--app``),
    ``webview``, or ``chromium``.

    ``block`` waits until the kit window closes (CLI).

    ``fresh`` ignores remembered kit settings for this launch.
    """
    prefer = (prefer or "auto").lower()
    if prefer not in {"auto", "chromium", "webview", "chrome", "edge"}:
        raise KitWindowError(f"unknown kit window backend {prefer!r}")

    if prefer in {"webview", "auto"}:
        try:
            # macOS normally launches a Dock .app, whose output never reaches
            # this terminal. --debug stays in-process so probe errors print here.
            in_process = block and _webview_available() and (
                sys.platform != "darwin" or debug
            )
            if in_process and (prefer == "webview" or prefer == "auto"):
                if debug and sys.platform == "darwin":
                    from .debug import log

                    log("macOS kit stays in this terminal so connection errors print here")
                run_webview(url, width=width, height=height, fresh=fresh)
                return {"backend": "webview", "browser": "pywebview", "url": url}
            open_webview_process(
                url,
                width=width,
                height=height,
                fresh=fresh,
                wait=block,
                debug=debug,
                netdebug=netdebug,
            )
            return {"backend": "webview", "browser": "pywebview", "url": url}
        except KitWindowError:
            if prefer != "auto":
                raise

    if prefer in {"chromium", "chrome", "edge", "auto"}:
        browser = open_chromium_app(url, width=width, height=height)
        return {"backend": "chromium-app", "browser": browser, "url": url}

    raise KitWindowError("could not open a chrome-less kit window")


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Frameless TESmart kit window")
    parser.add_argument("--url", required=True)
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--fresh", action="store_true", help="ignore remembered kit settings")
    parser.add_argument("--debug", action="store_true", help="print connection errors on stderr")
    parser.add_argument(
        "--netdebug",
        action="store_true",
        help="with --debug, also print local interfaces and saved network settings",
    )
    args = parser.parse_args(argv)
    if args.debug or args.netdebug:
        from .debug import log_network, set_enabled, set_netdebug

        set_enabled(True)
        if args.netdebug:
            set_netdebug(True)
            log_network()
    try:
        run_webview(args.url, width=args.width, height=args.height, fresh=args.fresh)
    except KitWindowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
