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
from pathlib import Path

# Fits the kit deck (720px panel + padding) without a tall empty band.
DEFAULT_WIDTH = 756
DEFAULT_HEIGHT = 700


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


def run_webview(url: str, *, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT) -> None:
    """Block on a frameless pywebview window (must run on the process main thread)."""
    try:
        import webview  # type: ignore[import-untyped]
    except ImportError as exc:
        raise KitWindowError(
            "pywebview is not installed; pip install 'tesmartctl[kit]'"
        ) from exc

    class _Api:
        def close(self) -> None:
            for window in list(webview.windows):
                try:
                    window.destroy()
                except Exception:
                    pass
            # Frameless Cocoa windows can linger as an empty shell after destroy.
            os._exit(0)

        def pin(self, on: bool) -> None:
            for window in webview.windows:
                window.on_top = bool(on)

        def resize_to(self, width: int, height: int) -> None:
            width = max(320, min(int(width), 1600))
            height = max(320, min(int(height), 1600))
            for window in webview.windows:
                window.resize(width, height)

        def move_by(self, dx: int, dy: int) -> None:
            for window in webview.windows:
                x, y = window.x, window.y
                window.move(int(x) + int(dx), int(y) + int(dy))

    webview.create_window(
        "TESmart",
        url,
        width=width,
        height=height,
        frameless=True,
        # Native drag; buttons/inputs stay clickable. Custom JS move_by fights this.
        easy_drag=True,
        on_top=False,
        background_color="#10151b",
        js_api=_Api(),
    )
    webview.start(debug=False)


def open_webview_process(
    url: str, *, width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT
) -> str:
    """Spawn a child process that owns the frameless webview event loop."""
    if not _webview_available():
        raise KitWindowError(
            "pywebview is not installed; pip install 'tesmartctl[kit]' "
            "or use Chrome/Edge --app mode"
        )
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
    try:
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
) -> dict[str, str]:
    """Open the kit URL without normal browser chrome.

    ``prefer`` is ``auto`` (frameless webview, then Chromium ``--app``),
    ``webview``, or ``chromium``.

    ``block`` is only meaningful for an in-process webview (CLI). The API always
    spawns a child process so the HTTP listener stays responsive.
    """
    prefer = (prefer or "auto").lower()
    if prefer not in {"auto", "chromium", "webview", "chrome", "edge"}:
        raise KitWindowError(f"unknown kit window backend {prefer!r}")

    if prefer in {"webview", "auto"}:
        try:
            if block and prefer == "webview":
                run_webview(url, width=width, height=height)
                return {"backend": "webview", "browser": "pywebview", "url": url}
            if block and prefer == "auto" and _webview_available():
                run_webview(url, width=width, height=height)
                return {"backend": "webview", "browser": "pywebview", "url": url}
            open_webview_process(url, width=width, height=height)
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
    args = parser.parse_args(argv)
    try:
        run_webview(args.url, width=args.width, height=args.height)
    except KitWindowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
