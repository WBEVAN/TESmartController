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
    url = _with_query(url, transparent="1")

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
        # Only the rounded deck is drawn; the square corners are see-through.
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
        url,
        width=width,
        height=height,
        **create_kwargs,
    )
    events = getattr(window, "events", None)
    if events is not None:
        if hasattr(events, "shown"):
            events.shown += memory.restore
            events.shown += lambda *_: _brand_macos(schedule_retries=True)
        if hasattr(events, "moved"):
            events.moved += lambda *_: memory.save_soon()
        if hasattr(events, "closing"):
            events.closing += memory.save_now
    webview.start(debug=False)


def open_webview_process(
    url: str,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fresh: bool = False,
    wait: bool = False,
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
                url, width=width, height=height, fresh=fresh, wait=wait
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
            if block and prefer == "webview" and sys.platform != "darwin":
                run_webview(url, width=width, height=height, fresh=fresh)
                return {"backend": "webview", "browser": "pywebview", "url": url}
            if block and prefer == "auto" and _webview_available() and sys.platform != "darwin":
                run_webview(url, width=width, height=height, fresh=fresh)
                return {"backend": "webview", "browser": "pywebview", "url": url}
            open_webview_process(
                url, width=width, height=height, fresh=fresh, wait=block
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
    args = parser.parse_args(argv)
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
