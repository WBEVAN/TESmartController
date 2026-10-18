# HDMI Switcher visual kit

Open index.html in a browser. It works offline and simulates 4, 8, or 16 inputs, input names, selection, Peek Off / Once / Always, duration, timed return, external interruption and disconnect. Right-click an input to edit a demo name. This is a reusable asset package and UI simulation, not an installed desktop application.

## Assets
- svg/: editable enclosure, LCD bezel, eight button states, eleven icons (including power-off), four controller LEDs. Buttons intentionally have no baked-in numbers: render numbers and names as live text.
- png/: transparent raster equivalents at 1× and 2×.
- styles.css: responsive panel styling and state classes.
- tokens.json: colors and dimensions.
- demo.js: illustrative local state machine; replace with API integration.

## Actual state versus local state
Read active_input, active_name, input_count, names, endpoint from /status?network=0. Names are local controller metadata. Render only the configured count (1–16); never infer connected sources. A green LED means controller communication succeeded, not video signal lock. Offline means unknown current input; optionally show last confirmed input explicitly labeled stale.
Do not show refresh rate, resolution, signal presence, HDCP, or audio state: these are not available. The header power icon closes the frameless kit window only; it is not a switch power command. Settings and grip are artwork only. Previous/next follow the configured cycle, not necessarily numeric order (the demo uses numeric order).

## API integration map
GET /status?network=0 — initial state and refresh.
GET /input/N — select input and read back; use returned active_input, not requested N.
GET /previous and /next — configured cycle navigation.
GET /peek/once?seconds=5 — arm next selection only.
GET /peek/always?seconds=5 — arm repeated peeks.
GET /peek/off — ordinary selection.
GET /peek/N?seconds=5 — explicit one-shot peek without consuming an armed once.
GET /names or POST /names — retrieve/edit local names.

Peek requests finish after the return. Show “Peek requested” while awaiting confirmation; any countdown is an estimate until backend lifecycle events exist. Use response previous, peeked, restored, active_input, interrupted, cancelled, error to settle the UI. If interrupted, preserve the external input and show “External selection retained”. On failure show unknown state and refresh. Do not assume that expiry proves restoration. Do not invent a cancel/keep command; neither is included. Backend peek-follow mode belongs to the shared listener; do not claim authoritative mode from local UI state when other clients can change it. Serialize commands, disable input controls while an operation is pending, and refresh after completion. Never send a redundant input selection while peeking.

Serve the integrated panel from the listener's origin or use a native bridge; this kit does not bypass CORS. Native wrappers (SwiftUI/AppKit, Qt, Tauri, Electron) must implement transparent background, window dragging, resizing, and always-on-top. Keep drag regions off buttons and LCD text. Scale SVGs freely; for native 2× use the @2x PNGs. Honor reduced-motion, keyboard focus, accessible input labels, and non-color status text.

Source reference: https://github.com/WBEVAN/TESmartController/blob/main/Docs/listener.md (reviewed 2026-10-17).

Original kit artwork may be used, modified, and distributed with TESmartController. No external fonts or image dependencies.

## Update: numeric Peek and compact view
The LCD reserves a fixed countdown cell in all states: -- while idle and 05, 04, 03, 02, 01 during Peek. Tabular digits and fixed screen height prevent layout movement. It is labeled EST. because the real API does not stream countdown events. Restored state must still come from the backend response.
A first launch starts in Compact; afterwards the last chosen size, Always on top, window position, and an "always" peek-follow are restored (`kit-prefs.js`: pywebview bridge → `~/.config/tesmartctl/kit.json`, otherwise localStorage). `?fresh=1` or `tesmartctl kit --fresh` ignores saved settings for that launch. The header shows the current size name; click it to cycle Compact → Mini → Full → Compact. Compact is a 380px-wide deck with all input keys and a smaller LCD. Mini shows only the LCD with Up/Down input buttons to its right (Setup door and the input grid are hidden). Full is the wide deck; the next click returns to Compact. Compact input buttons retain the monitor artwork and live numbers. The compact LCD is 94px tall with a reserved numeric countdown column. Views retain accessible names and keyboard focus. Appearance lives in Setup (`themes.css`): body **black** (default) / **silver**, LCD **green** (default) / **blue** / **red** / **amber** (dark + orange), plus an optional custom **Highlight** colour and **Default** to restore the LCD match. The active input keeps idle key artwork with an inset ring and numeral in the highlight colour. Choices are remembered with the other kit prefs. Dock icon branding is macOS-only (`tesmart/kit_macos.py` → `TESmart Kit.app`); other platforms launch `python -m tesmart.kit_window` directly. The pywebview window is transparent (`transparent=True`; the URL gains `transparent=1`, which the listener stamps as `body.is-transparent`), so the page background and the outer CSS drop shadow are dropped and only the rounded deck is drawn. pywebview asks WebKit for that with the deprecated private `_setDrawsTransparentBackground:`; the kit redirects that call to `drawsBackground = NO` plus the public `underPageBackgroundColor = clear` so the deprecation is not logged. Chromium `--app` and browser popups keep the dark stage because they cannot be see-through. In app/popup mode the device's panel is forced to `height:100%`/`width:100%` of the window, not just its content size, so dragging an edge past the content's natural size stretches the device border to meet it instead of leaving a gap outside the device. Window resize floors follow the view ladder (native `setMinSize` via `set_min_size`; browser popups are pushed back with `resizeTo`): Full ≥ Compact snap, Compact ≥ Mini snap, Mini ≥ Mini snap. In the frameless app window the deck may grow wider than the snap sizes: keys stay a fixed track width and switch to one row when width allows. Resize snaps clamp to the display that currently owns the window so a portrait secondary monitor does not fling the frame back to the primary landscape screen.

## Cycle controls and action icons
Start/Stop cycle advances through a chosen comma-separated list (empty means every configured input). Previous/Next use that same list. Interval defaults to 15 seconds. The local demo pauses cycle advancement during Peek and stops cycling on disconnect or when the page is hidden. Peek modes also apply to cycle changes in this demo. Icons accompany navigation, Peek modes, Compact/Expand, and Cycle controls; labels and keyboard accessibility remain.
For integration, GET /api/state and POST /api/cycle {"inputs":[1,3,5],"mode":"automatic","seconds":15} read/save configuration with --panel enabled. Subscribe to GET /api/events (Server-Sent Events) for live input_changed / names_changed / banner pushes; keep a slow /api/state poll as fallback (faster while offline). The cycle endpoint saves configuration; it does not start a server timer. Match the project's browser panel by scheduling POST /api/next from the visible client, stopping when hidden, and pausing during Peek. Manual mode uses Previous/Next only. A separate CLI cycle run is its own process; this button must not claim to start/stop that process. Do not overlap requests: wait for one cycle/Peek request to finish before scheduling the next.

## Arbitrary LCD content
The LCD is a live HTML region, not baked-in text. The demo editor accepts any text. Custom content remains in place while input/Peek simulation continues. Screen geometry stays fixed; overflow scrolls inside the screen.

```js
hdmiLCD.setText('CUSTOM TITLE\nAny input name\nAny status or message');
hdmiLCD.setText(['Line one', 'Line two'], {showCountdown: false});
hdmiLCD.setHTML('<strong>Custom layout</strong><div>Any trusted markup</div>', {showCountdown: false});
hdmiLCD.reset(); // restore the default switcher display
```

setText renders text safely, including literal markup characters. setHTML is for developer-controlled trusted HTML only; sanitize any external or user-provided HTML before passing it in. Do not insert scripts or executable markup. Use text for untrusted content. Custom layouts may use text, icons, or HTML; adapt their styling to the green LCD palette. showCountdown defaults to true and reserves its fixed column; false gives custom content the full LCD width. Custom content and mode are presentation state only and are not saved across page reloads.
