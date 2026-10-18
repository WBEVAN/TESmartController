# Control panel

The **panel** is the full-size Bootstrap UI at `/panel`: status, input grid, network settings, peek, cycle, names, and an API explorer. Use it when you want everything on one page; use the [floating kit](Floating-Kit) for a compact always-on window.

## Open it

```bash
tesmartctl listen --http --panel
open http://127.0.0.1:8765/panel
```

Or from the CLI helpers (if installed as documented in the README):

```bash
tesmartctl panel
```

## What’s on the page

- Live connection / active input
- Input buttons (and local names when set)
- Peek mode, cycle, buzzer / LED helpers where exposed
- Banner controls (same store as the kit)
- Network fields (LAN changes need a switch power cycle)
- Links into raw HTTP routes for scripting

## Kit vs panel

| | Kit `/kit` | Panel `/panel` |
| --- | --- | --- |
| Size | Compact switcher | Full control surface |
| Themes | Body / LCD / glass | Standard Bootstrap panel |
| macOS app | `tesmartctl kit` | Browser (or embed) |
| Best for | Desk / always visible | Setup, debugging, full config |

Both share the same listener state (active input, banners, peek). They subscribe to `GET /api/events` (Server-Sent Events) so input, name, and banner changes show up immediately. A slower `/api/state` poll remains as a fallback (~30s online; ~2s while the switch is unreachable so reconnect feels quick). Hubitat uses separate outbound HTTP callbacks, not this browser stream.

## Embed buttons elsewhere

See [Docs/embed.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/embed.md) and `examples/buttons/` for dropping input buttons on your own page.

## Deeper detail

Repo doc: [Docs/panel.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/panel.md)

## Next

- [Listener & HTTP](Listener-and-HTTP)
- [CLI reference](CLI-Reference)
