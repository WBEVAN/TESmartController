# Setup

Get `tesmartctl` talking to the switch, then keep a listener running for the kit, panel, and Hubitat.

## Install

```bash
python3 -m pip install --upgrade tesmartctl
# or from a clone:
python3 -m pip install -e .
```

Entry point: `tesmartctl`. Requires Python 3.9+.

## Reach the switch

Default LAN target is **192.168.1.10:5000**. Override per command or save defaults:

```bash
tesmartctl --host 192.168.1.50 status
tesmartctl config set --host 192.168.1.50 --port 5000
tesmartctl config path    # where defaults live
```

RS232 instead of LAN:

```bash
tesmartctl --serial /dev/tty.usbserial-xxx status
```

16-port models: add `--inputs 16` (or save it with `config set`).

## Smoke test

```bash
tesmartctl status
tesmartctl input 2
tesmartctl input              # query current
```

If `status` times out: confirm IP/port, that nothing else owns the TCP session, and that the switch is on the same LAN.

## Run the listener (required for UI + Hubitat)

One process owns the switch connection and exposes HTTP:

```bash
tesmartctl listen --http --panel
# optional: --host 0.0.0.0 --port 8765
```

| Flag | Purpose |
| --- | --- |
| `--http` | HTTP API + `/status`, `/input`, `/banner`, `/events`, … |
| `--panel` | Serve `/kit` and `/panel` (and `/api/events` for live UI updates) |
| `--host` / `--port` | Bind address (default `127.0.0.1:8765`) |

Leave this running. CLI one-shots, Stream Deck HTTP, the floating kit, and Hubitat all talk through it (or open their own short-lived TCP sessions for one-shots).

Optional LAN discovery (`pip install 'tesmartctl[mdns]'`): bind `0.0.0.0`, then from another machine use `tesmartctl --via auto …`. Limits and typed-URL fallback: [mDNS](mDNS).

## Local input names

Names are stored only on the Mac/PC (env file), not on the switch:

```bash
tesmartctl name set 2 Office
tesmartctl name list
tesmartctl input Office
```

## Next

- [Floating kit](Floating-Kit) — desktop UI over `/kit`
- [Hubitat](Hubitat) — smart-home drivers
- [Control panel](Control-Panel) — full `/panel`
- [Listener & HTTP](Listener-and-HTTP) — URL cheat sheet
- [mDNS](mDNS) — discover the listener without typing an IP
