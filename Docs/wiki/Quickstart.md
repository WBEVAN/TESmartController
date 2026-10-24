# Quick start

On the computer that owns the HDMI switch, install the package, save the listener once, then start it.

```bash
python3 -m pip install --upgrade tesmartctl
tesmartctl install
tesmartctl quickstart
```

`install` asks whether this computer is the server, then the switch address, the HTTP listen address, the listen port, the browser UI, and mDNS. Nothing is written until you confirm. A saved env file is used as the defaults.

`quickstart` reads that file and leaves the listener running. `/kit` is the floating UI application kit. `/panel` is the browser-only control panel. Both are on by default.

Leave that process running. The kit, the panel, Hubitat, Stream Deck, and `tesmartctl --via auto` on other computers all talk through it.

If the listen address is missing, turned off, or unusable, `quickstart` says it does not have the required information to start and asks whether to configure. Answering yes runs `install`, then starts the listener when that configuration includes HTTP.

The same start, written out, is `tesmartctl listen --http HOST:PORT` with `--panel` when the browser UI is on. Run `install` again when the address or the mDNS name should change.

Walk-through: [Docs/install.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/install.md). Listener routes: [Listener and HTTP](Listener-and-HTTP).
