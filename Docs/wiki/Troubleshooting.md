# Troubleshooting

## Kit / panel won’t load

- Confirm `tesmartctl listen --http --panel` is running
- Open `http://127.0.0.1:8765/kit` (or your `--host` / `--port`)
- If another process holds port 8765, stop it or pick a free `--port`
- After a UI upgrade, hard-refresh or open `/kit?fresh=1` / `/panel?fresh=1` so static JS reloads

## Kit shows offline after the switch was powered off

- Offline means `/api/state` could not reach the HDMI switch (502), not that the listener died
- SSE to the listener can stay up while the switch is down; the kit polls about every **2s** until the switch answers again
- If reconnect still feels stuck, hard-refresh the kit so you have current JS

## Kit looks wrong / prefs stuck (macOS app)

```bash
tesmartctl kit --fresh
```

That skips saved window / appearance prefs for one launch.

## `status` / `input` times out

- Ping / confirm switch LAN IP (default `192.168.1.10:5000`)
- Only one TCP client should own the session long-term — prefer the listener
- Try RS232 with `--serial` if LAN is unreliable
- Increase `--timeout` if the switch is slow to reply

## Banner does nothing / appears late

- Banners need `listen --http` (and `--panel` if you want the UI)
- Sticky banners stay until `tesmartctl banner --dismiss`
- Banners are **local UI only** — they do not change the switch’s hardware display
- With a current kit/panel, banners arrive over SSE immediately; if you still wait several seconds, hard-refresh (`?fresh=1`) so the EventSource client is loaded

## Hubitat shows offline / wrong input

- Base URL must reach the listener from the Hub (use LAN IP, not `127.0.0.1`, on the Hub)
- Firewall: allow Hub → listener port
- Re-run Manager setup / refresh children
- Enable callbacks if you change inputs from kit/CLI and need Hubitat to follow

## Images / README kit screenshot outdated on GitHub

Local `Docs/images/*.png` updates need a commit and push before GitHub (and this wiki’s raw image URLs) show the new look.

## More

- [Setup](Setup)
- [Docs/listener.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/listener.md)
- [Docs/hubitat.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/hubitat.md)
