# Control panel

The **panel** at `/panel` is the browser-only control panel: status, input grid, network settings, peek, cycle, names, and an API explorer. The [floating kit](Floating-Kit) at `/kit` is the floating UI application kit.

## Open it

On the server:

```bash
tesmartctl install
tesmartctl quickstart
```

Then open `http://127.0.0.1:<port>/panel`. `<port>` is `TESMART_HTTP_PORT` from that install (the wizard default is `8080`).

## What’s on the page

- Live connection / active input
- Input buttons (and local names when set). Each name row has an **Enabled** checkbox. Unchecked inputs are ignored for selection, peek, and next/previous. At least one input stays enabled.
- Peek mode, cycle, buzzer / LED helpers where exposed
- Banner controls (same store as the kit)
- Network fields (LAN changes need a switch power cycle)
- Links into raw HTTP routes for scripting

## Kit vs panel

| | Kit `/kit` | Panel `/panel` |
| --- | --- | --- |
| What it is | Floating UI application kit | Browser-only control panel |
| Size | Compact switcher | Full control surface |
| Themes | Body / LCD / glass | Standard Bootstrap panel |
| How it opens | `tesmartctl kit`, or a browser | A browser (or an embedded page) |
| Best for | Desk / always visible | Setup, debugging, full config |

Both share the same listener state (active input, banners, peek). They subscribe to `GET /api/events` (Server-Sent Events) so input, name, and banner changes show up immediately. A slower `/api/state` poll remains as a fallback (~30s online; ~2s while the switch is unreachable so reconnect feels quick). Hubitat uses separate outbound HTTP callbacks, not this browser stream.

## Embed buttons elsewhere

See [Docs/embed.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/embed.md) and `examples/buttons/` for dropping input buttons on your own page.

## Deeper detail

Repo doc: [Docs/panel.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/panel.md)

## Next

- [Listener & HTTP](Listener-and-HTTP)
- [CLI reference](CLI-Reference)
