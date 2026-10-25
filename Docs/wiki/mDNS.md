# mDNS discovery

Find the HTTP listener on the LAN without typing its IP — then fall back to a typed URL when multicast is blocked.

## Quick start

On the server, `tesmartctl install` saves the listen address and mDNS name. `tesmartctl quickstart` starts that listener.

```bash
pip install 'tesmartctl[mdns]'

# server
tesmartctl install
tesmartctl quickstart

# the same listener, written out
tesmartctl listen --http 0.0.0.0:8080 --panel --mdns-name OfficeHDMI

# another computer: tesmartctl install, choose Client
# the wizard scans and lists the listeners on the network
# that choice is saved as TESMART_CLIENT_VIA and TESMART_CLIENT_MDNS_NAME
tesmartctl --via auto kit

# the same client, written out
tesmartctl discover
tesmartctl --via auto --mdns-name OfficeHDMI input 3
tesmartctl --via auto enable 4
tesmartctl --via auto disable Office
tesmartctl --via auto kit --fresh
tesmartctl --via auto banner "Door unlocked"
```

Service type: `_wbevan-tesmart._tcp` (TXT includes `domain=wbevan.com`).

## When discovery fails

Guest Wi‑Fi, AP isolation, some mesh/VLANs, and many corporate networks block mDNS. If `discover` is empty but the listener is up:

```bash
tesmartctl --via http://192.168.1.20:8080 status
tesmartctl kit 'http://192.168.1.20:8080/kit?app=1'
curl http://192.168.1.20:8080/version
```

Hubitat always uses a typed base URL in Manager — it does not browse mDNS.

## OS browse tools (optional)

Prefer `tesmartctl discover` on every platform. For manual checks:

| OS | Tool |
| --- | --- |
| macOS | `dns-sd` (ships with the OS) |
| Linux | `avahi-browse` (Avahi utils package) |
| Windows | no stock CLI — use `tesmartctl discover`, or Bonjour Print Services’ `dns-sd.exe` |

## Full guide

Limitations, platform CLI notes, `--save` / `TESMART_VIA`, and the full fallback list:

[Docs/mdns.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/mdns.md)
