# Listener & HTTP

The listener is the long-lived process that owns the switch TCP (or serial) session and serves HTTP for UIs and integrations.

## Start the server

Configure the computer that talks to the HDMI switch, then leave the listener running:

```bash
tesmartctl install
tesmartctl quickstart
```

`install` saves the listen address (`TESMART_HTTP`), the listen port (`TESMART_HTTP_PORT`), and the browser UI (`TESMART_HTTP_PANEL`). `quickstart` starts the listener from those values. `/kit` is the floating UI application kit. `/panel` is the browser-only control panel.

The same start, written out, is `tesmartctl listen --http HOST:PORT` with `--panel` when the browser UI is on. Walk-through: [Docs/install.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/install.md).

Line-protocol mode (no HTTP) is also available for raw clients; see [Docs/listener.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/listener.md).

## Useful URLs

Base: `http://127.0.0.1:8765` (or your bind host/port)

| Path | Role |
| --- | --- |
| `GET /status` | Connection + active input |
| `GET` / `POST /input` | Query or select input |
| `POST /banner` | Show / dismiss / clear banners |
| `GET /events` | Server-Sent Events (input, names, enable/disable, banner push) |
| `GET /api/events` | Same stream for kit / panel (`--panel`) |
| `GET /kit` | Floating kit UI |
| `GET /panel` | Full panel UI |
| Static under `/static/…` | Kit and panel assets |

Exact query/body shapes live in the repo listener doc — prefer that as the source of truth when wiring Stream Deck, Companion, or custom scripts.

## Push vs poll

| Client | How it stays in sync |
| --- | --- |
| Kit / panel | `GET /api/events` (SSE) + fallback `/api/state` poll |
| Hubitat | Outbound `POST` to registered `/callbacks` URLs (+ its own poll) |
| Stream Deck / Companion / curl | Usually one-shot HTTP; optional `curl -N /events` to watch |

SSE event names: `hello`, `input_changed`, `names_changed`, `enabled_changed`, `banner`, `peek_follow`. Same JSON shape as callback bodies. `enabled_changed` is sent when an input is enabled or disabled from the panel.

## Who uses it

- [Floating kit](Floating-Kit) and [control panel](Control-Panel)
- [Hubitat](Hubitat) controller / Manager
- `tesmartctl banner …` (POSTs to the listener; kit/panel see it over SSE)
- One-shot CLI still can open its own short TCP session to the switch when no listener is required

## mDNS

With `tesmartctl[mdns]`, a non-loopback `--http` bind can advertise `_wbevan-tesmart._tcp`. Clients use `tesmartctl discover` or `--via auto`. Hubitat still needs a typed base URL. Full limits and fallbacks: [mDNS](mDNS) / [Docs/mdns.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/mdns.md).

## Deeper detail

[Docs/listener.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/listener.md) (includes the SSE section)
