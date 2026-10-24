# Guided setup (`tesmartctl install`)

`tesmartctl install` asks the questions that are easy to get wrong the first time: where the HDMI switch is, whether this computer is the server or a client, and how other programs should connect. It writes the same env file as `tesmartctl config` (`~/.config/tesmartctl/env`, or `--env-file`), and only after you confirm.

```bash
tesmartctl install
```

Press Enter to keep the value in brackets. Each section has a heading and a blank line before the next question. On a terminal that supports ANSI color (macOS, Linux, and Windows 10 consoles) headings, commands, and success lines are colored. Set `NO_COLOR` to leave the text plain. `startup.txt` is always plain text. The wizard exits when the configuration is saved, or when you decline the last question. Declining leaves the env file unchanged.

## What it reads first

If that env file already has connection settings, the wizard says it will use that configuration as the defaults so you can reconfigure it. Each later question shows where its starting value came from: the saved file, an environment variable, a command-line flag, or the built-in default (`192.168.1.10`, port `5000`, 8 inputs).

If the file is missing, the questions start from those built-in defaults and the wizard says so.

Flags still win for the value they set. `tesmartctl --host 192.168.1.50 install` starts the switch address at `192.168.1.50`.

## Server

Use this on the computer that will own the connection to the switch.

1. Input count (8 or 16 on current models).
2. TCP (the usual LAN connection) or serial RS232. Serial checks for `pyserial` (`pip install "tesmartctl[serial]"`). The wizard can install that with the same Python that is running `tesmartctl`, on Windows, macOS, and Linux.
3. Optional connection test. A successful read of the active input is required before the next questions, unless you choose to continue anyway.
4. Zeroconf / mDNS. If you say yes and `zeroconf` is missing, the wizard offers to install `tesmartctl[mdns]`, then asks for the instance name. The current saved name, or this computer's hostname, is the default.
5. HTTP, so curl, Hubitat, Stream Deck, Companion, the kit, and the panel can connect. The browser UI is on by default. `/kit` is the floating UI application kit (`tesmartctl kit`). `/panel` is the browser-only control panel. The pages need no extra package. The frameless kit window checks for `pywebview` (`tesmartctl[kit]`); Chrome or Edge still open the kit if that package is not installed.
6. Where the listener accepts connections: this computer only (`127.0.0.1`), every interface (`0.0.0.0`), or one address from the list of IPv4 interfaces. mDNS with localhost is called out, because other computers cannot connect to `127.0.0.1`.

The wizard then prints `tesmartctl quickstart` and the same start written out as `tesmartctl listen ...`, plus the client checks (curl, and `tesmartctl --via auto` when zeroconf is on). When this is the only server on the network, those client commands may omit `--mdns-name`. It can write that text to `startup.txt` in the current directory for future reference. Confirming writes the env file and the command exits.

The env file stores the HTTP listener as `TESMART_HTTP` and the listen port on its own as `TESMART_HTTP_PORT`. A bare `TESMART_HTTP` value means this computer only (`127.0.0.1`). `0.0.0.0:8080` accepts every interface. `172.16.0.141:7272` is one address on this computer. `TESMART_HTTP_PANEL=1` serves `/kit` (the floating UI application kit) and `/panel` (the browser-only control panel). `TESMART_HTTP_PANEL=0` leaves those pages off. `TESMART_HTTP_MDNS=1` advertises the listener, and the instance name stays in `TESMART_MDNS_NAME`. `TESMART_HTTP=off` means the HTTP listener was turned off.

```bash
tesmartctl quickstart
```

`quickstart` opens the switch from `TESMART_HOST` and `TESMART_PORT` (or `TESMART_SERIAL`) and serves HTTP at the saved address with the saved panel and mDNS options. Run `install` again when that address should change. If the listener address is missing, turned off, or unusable, `quickstart` says it does not have the required information to start and asks whether to configure. Answering yes runs `install`. After that configuration is saved with an HTTP listener, `quickstart` starts it.

## Client

Use this on a computer that talks to a listener already running somewhere else. It does not open the HDMI switch itself.

1. Find the listener with mDNS (`tesmartctl --via auto`) or type its `http://host:port` URL.
2. mDNS checks for `zeroconf` the same way the server does, then asks for the instance name the server is advertising.
3. Optional check: browse the LAN, or GET `/version` on the URL you typed.
4. Optional local kit window (`pywebview`, with Chrome or Edge as the fallback).

The same `startup.txt` offer lists the client commands. Confirming saves `TESMART_VIA` and `TESMART_MDNS_NAME`.

## Documentation

The wizard prints these links at the start and again when it finishes:

- [Setup](https://github.com/WBEVAN/TESmartController/wiki/Setup)
- [Listener and HTTP](https://github.com/WBEVAN/TESmartController/wiki/Listener-and-HTTP)
- [mDNS](https://github.com/WBEVAN/TESmartController/wiki/mDNS)
- [Control panel](https://github.com/WBEVAN/TESmartController/wiki/Control-Panel)
- [CLI reference](https://github.com/WBEVAN/TESmartController/wiki/CLI-Reference)
- [README](https://github.com/WBEVAN/TESmartController#readme)
- [Project site](https://wbevan.github.io/TESmartController/)

The panel's **Connect info** button shows the same kind of samples for the listener you are looking at.
