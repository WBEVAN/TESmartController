# Floating kit

The **kit** is the compact HDMI-switcher UI: input tiles, LCD banner, peek mode, cycle, and (in the app) a power switch.

![Floating kit](https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/kit.png)

## Open it

With the listener running (`tesmartctl listen --http --panel`):

```bash
# Browser (any platform)
open http://127.0.0.1:8765/kit

# macOS floating app (PyObjC + WKWebView)
tesmartctl kit
tesmartctl kit --fresh    # ignore saved window prefs once
```

App mode adds the red power switch (quits the kit) and persists appearance / window prefs on the Mac.

## What you can do

| Control | Action |
| --- | --- |
| Input tiles | Select that input |
| Cycle Inputs | Open the cycle list (silver chrome), then start cycling |
| PEEK (LCD) | Cycle Off → Once → Always |
| Banner area | Shows sticky / timed messages from `tesmartctl banner` or Hubitat (pushed over SSE) |
| Message list | Last ~10 banners; pick one to show again |
| Appearance | Body / LCD / highlight / glass themes (saved in the app) |

Defaults: **black body**, **green LCD**, olive highlight when using silver+green.

## Live updates

The kit opens `GET /api/events` (Server-Sent Events) so input changes, name edits, and banners appear as soon as the listener knows about them. A background `/api/state` poll is only a fallback (~30s while online). If the HDMI switch is powered off, the LED goes offline; when it comes back the kit probes about every **2 seconds** until connected again (SSE to the listener stays up even while the switch is down).

## Send a banner to the kit

Needs a running `listen --http` (add `--panel` for the UI):

```bash
tesmartctl banner "Door unlocked"           # timed (~8s)
tesmartctl banner Meeting in 5 --seconds 12
tesmartctl banner "Stay on camera" --sticky
tesmartctl banner --dismiss
tesmartctl banner --history
```

Banners are **not** written to the switch hardware LCD; they are local UI messages.

## Themes & prefs (app)

The macOS kit remembers:

- Window size / position
- Body, LCD, highlight, and glass choices
- Cycle list selection

Use `tesmartctl kit --fresh` if prefs get in the way.

## Quit

- App: red power switch, or **Ctrl+C** in the terminal that launched `tesmartctl kit`
- Browser tab: close the tab (listener keeps running)

## Deeper detail

Repo doc: [Docs/panel.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/panel.md) (kit + panel).

## Next

- [Hubitat](Hubitat)
- [Control panel](Control-Panel)
- [Troubleshooting](Troubleshooting)
