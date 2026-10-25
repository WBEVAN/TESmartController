# CLI reference

Short everyday list. On the server, run `tesmartctl install` first, then `tesmartctl quickstart`. Full flags and behavior: [README](https://github.com/WBEVAN/TESmartController#readme).

## Connection & config

```bash
tesmartctl install          # server, or a client on another computer
tesmartctl quickstart       # server: start the listener saved by install
tesmartctl --via auto kit   # client: floating kit, after a client install
tesmartctl status
tesmartctl config set --host 192.168.1.50 --port 5000
tesmartctl config path
tesmartctl --serial /dev/tty.usbserial-xxx status
tesmartctl --inputs 16 status
```

## Inputs & names

```bash
tesmartctl input 2
tesmartctl input Office
tesmartctl input
tesmartctl name set 2 Office
tesmartctl name list
tesmartctl name unset 1
tesmartctl disable 4
tesmartctl disable Office
tesmartctl enable 4
tesmartctl --via auto enable 4
tesmartctl --via auto disable Office
```

`disable` and `enable` take an input number or a local name. A disabled input is ignored for selection, peek, and next/previous. It can stay in a saved cycle. At least one input must stay enabled. Inputs are enabled unless you disable them. Without `--via` the change is stored on this computer. `--via auto` or `--via http://host:port` stores it on that listener (`GET /enable/N` and `GET /disable/N`).

## Peek, cycle, rotate

```bash
tesmartctl peek off|once|always|query   # exact subcommands per README
tesmartctl cycle …
tesmartctl rotate …
```

## Listener, kit, panel

```bash
tesmartctl quickstart
tesmartctl quickstart --simulate   # no HDMI switch; input, peek, and cycle stay local
tesmartctl listen --http --panel
tesmartctl listen --simulate --http 8080 --panel
tesmartctl kit
tesmartctl kit --fresh
tesmartctl panel
```

## Banners (needs `listen --http`)

```bash
tesmartctl banner "Door unlocked"
tesmartctl banner Meeting in 5 --seconds 12
tesmartctl banner "Stay on camera" --sticky
tesmartctl banner
tesmartctl banner --history
tesmartctl banner --dismiss
tesmartctl banner --clear
```

Global flags go **before** the subcommand: `tesmartctl --host 192.168.1.50 status`.
