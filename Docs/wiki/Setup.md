# Setup

Get `tesmartctl` talking to the switch, then keep a listener running for the kit, panel, and Hubitat.

## Install

```bash
python3 -m pip install --upgrade tesmartctl
# or from a clone:
python3 -m pip install -e .
```

Entry point: `tesmartctl`. Requires Python 3.9+.

## Guided setup

`tesmartctl install` asks whether this computer is the server or a client, then the switch or listener details. A saved env file is used as the defaults and the wizard says so; otherwise it starts from the built-in defaults. Nothing is written until you confirm. A server install stores the HTTP listen address (`TESMART_HTTP`) with the panel and mDNS choices. It can also save `startup.txt` in the current directory for future reference, with `tesmartctl quickstart` and the client checks.

```bash
tesmartctl install
tesmartctl quickstart
```

Full walk-through: [Docs/install.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/install.md).

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

## Run the server

On the computer that talks to the HDMI switch, configure once, then start:

```bash
tesmartctl install
tesmartctl quickstart
```

`install` saves the switch address, the HTTP listen address (`TESMART_HTTP`), the listen port (`TESMART_HTTP_PORT`), and the browser UI choice (`TESMART_HTTP_PANEL`). `quickstart` starts that listener and leaves it running. `/kit` is the floating UI application kit. `/panel` is the browser-only control panel. Both are on by default.

Leave `quickstart` running. CLI one-shots, Stream Deck HTTP, the kit, the panel, and Hubitat all talk through it (or open their own short-lived TCP sessions for one-shots). `quickstart` is the same process as `tesmartctl listen` with the options `install` saved. The wizard also prints that longer command. Walk-through: [Docs/install.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/install.md).

Optional LAN discovery (`pip install 'tesmartctl[mdns]'`): during `install`, choose a listen address other computers can open, then from another machine use `tesmartctl --via auto …`. Limits and typed-URL fallback: [mDNS](mDNS).

## Local input names

Names are stored only on the Mac/PC (env file), not on the switch:

```bash
tesmartctl name set 2 Office
tesmartctl name list
tesmartctl input Office
```

## Next

- [Floating kit](Floating-Kit) — floating UI application kit at `/kit`
- [Hubitat](Hubitat) — smart-home drivers
- [Control panel](Control-Panel) — browser-only control panel at `/panel`
- [Listener & HTTP](Listener-and-HTTP) — URL cheat sheet
- [mDNS](mDNS) — discover the listener without typing an IP
