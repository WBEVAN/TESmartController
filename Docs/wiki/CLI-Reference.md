# CLI reference

Short everyday list. Full flags and behavior: [README](https://github.com/WBEVAN/TESmartController#readme).

## Connection & config

```bash
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
```

## Peek, cycle, rotate

```bash
tesmartctl peek off|once|always|query   # exact subcommands per README
tesmartctl cycle …
tesmartctl rotate …
```

## Listener, kit, panel

```bash
tesmartctl listen --http --panel
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
