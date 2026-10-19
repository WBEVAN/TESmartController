# mDNS / DNS-SD discovery

`tesmartctl listen --http` can advertise itself on the LAN with **multicast DNS** (Bonjour / Avahi) so other machines find **host and port** without typing them. Clients use the same records via `tesmartctl discover` and `--via auto`.

This discovers the **HTTP listener** (the computer running `tesmartctl`), not the TESmart switch’s factory TCP port (`192.168.1.10:5000`). The switch hardware does not speak mDNS.

## Install

```bash
pip install 'tesmartctl[mdns]'   # or: pip install zeroconf
```

Without `zeroconf`, the listener still serves HTTP; it just does not advertise. `--via auto` / `discover` will fail until the extra is installed.

## Advertise (server)

Bind a LAN-reachable address and leave mDNS on (the default for non-loopback binds):

```bash
tesmartctl listen --http 0.0.0.0:8080 --panel
tesmartctl listen --http 0.0.0.0:8080 --panel --mdns-name OfficeHDMI
tesmartctl listen --http 0.0.0.0:8080 --no-mdns    # HTTP only, no advertisement
tesmartctl listen --http 0.0.0.0:8080 --mdns        # require zeroconf or exit
```

| Flag | Effect |
| --- | --- |
| (default) | Advertise when the bind is not loopback and `zeroconf` is present |
| `--mdns` | Require advertisement (error if `zeroconf` is missing) |
| `--no-mdns` | Never advertise |
| `--mdns-name NAME` | DNS-SD instance name (default: this computer’s hostname) |

A loopback-only bind (`--http 8080` → `127.0.0.1`) does **not** advertise by default: other hosts could not use the published addresses anyway.

### What is published

| Field | Value |
| --- | --- |
| Service type | `_wbevan-tesmart._tcp.local.` (namespaced under **wbevan.com**; DNS-SD service names are ≤15 characters) |
| Instance | `--mdns-name`, or the machine hostname |
| Port | The HTTP listen port |
| TXT | `path=/`, `version=…`, `panel=0\|1`, `inputs=8\|16`, `proto=http`, `domain=wbevan.com` |

On startup the listener prints a one-line summary and points at `tesmartctl discover`.

## Discover (client) — preferred on every OS

`tesmartctl discover` and `--via auto` use the same `zeroconf` stack on **macOS, Linux, and Windows**. Prefer these over OS-specific browse tools:

```bash
tesmartctl discover
tesmartctl discover --mdns-name OfficeHDMI
tesmartctl discover --mdns-name OfficeHDMI --save   # write TESMART_VIA (+ name) to the env file
tesmartctl --json discover
```

Talk through a discovered (or remembered) listener (same commands on macOS, Linux, and Windows):

```bash
tesmartctl --via auto status
tesmartctl --via auto --mdns-name OfficeHDMI input 3
tesmartctl --via auto next
tesmartctl --via auto peek 2
tesmartctl --via auto banner "Door unlocked"
tesmartctl --via auto kit --fresh
```

Supported `--via` commands: `status`, `input`, `rotate`, `next`, `previous`, `peek`, `banner`, `kit`.

Direct switch commands (`net`, `raw`, `monitor`, `buzzer`, `led`, `autodetect`, `cycle run`, …) still use `--host` / the env file / RS232. They do not go through mDNS.

### How `--via auto` picks a listener

1. `--mdns-name` on the command line, else `TESMART_MDNS_NAME` in the env file / environment  
2. Else the last saved `TESMART_VIA` URL if it still matches a live service  
3. Else the only service on the LAN  
4. If several remain, error — pass `--mdns-name`

A successful `--via auto` (and `discover --save`) stores `TESMART_VIA` so the next call can prefer that endpoint when the browse list is ambiguous.

## Optional OS browse tools

These are only for manual debugging. They are **not** required once `tesmartctl[mdns]` is installed — prefer `tesmartctl discover` above.

### macOS — `dns-sd`

`dns-sd` ships with **macOS** (Apple’s Bonjour / mDNSResponder CLI). It is not a portable Unix utility.

```bash
dns-sd -B _wbevan-tesmart._tcp local.                 # browse (Ctrl-C to stop)
dns-sd -L "OfficeHDMI" _wbevan-tesmart._tcp local.  # resolve host, port, TXT
dns-sd -G v4 Waynes-Mac-Mini.local.                   # hostname → IPv4
```

`dns-sd -L` waits until that **exact** instance name appears; if it hangs, run `-B` first and use the name you see (not a docs example).

### Linux — `avahi-browse`

On most distributions, install Avahi utilities (package name varies: `avahi-utils`, `avahi-tools`, …):

```bash
avahi-browse -r _wbevan-tesmart._tcp
# or terminate after one pass:
avahi-browse -rt _wbevan-tesmart._tcp
```

### Windows

Windows has no built-in equivalent to `dns-sd` / `avahi-browse` in a stock install. Options:

- Use **`tesmartctl discover`** (recommended)
- Or install Apple’s **Bonjour Print Services for Windows**, which provides a `dns-sd.exe` with similar `-B` / `-L` flags

Then open the HTTP API with whatever host/port you resolved:

```bash
curl http://Waynes-Mac-Mini.local.:8080/version
curl http://192.168.1.20:8080/input
```

## Limitations

| Limitation | What it means |
| --- | --- |
| Multicast required | Guest Wi‑Fi, client isolation, some mesh / VLAN / IoT SSIDs, and corporate firewalls often **block** mDNS. Browse stays empty even though HTTP works if you type the IP. |
| Same L2 / multicast domain | Discovery is link-local. It will not cross routers unless your network deliberately forwards mDNS (uncommon). |
| Optional dependency | Core `tesmartctl` stays stdlib-only. Advertising and `--via auto` need `pip install 'tesmartctl[mdns]'`. |
| Loopback bind | `--http 8080` on `127.0.0.1` is local-only and does not auto-advertise. Use `0.0.0.0:PORT` (or a LAN IP) for other devices. |
| Browsers | Page JavaScript cannot browse mDNS. Open a typed URL, a `.local` hostname you already know, or launch with `tesmartctl --via auto kit`. |
| Hubitat | The Hubitat package expects a configured base URL. It does not browse mDNS; enter the listener’s LAN IP (or a stable hostname) in Manager. |
| Multiple listeners | Two switches / two hosts both advertising need distinct `--mdns-name` values; clients must pass `--mdns-name` (or save one). |
| Not the switch | Finding the listener does not change the switch’s own IP. Configure that with `tesmartctl --host …` / `config set host` on the **listener host**. |
| Stale cache | `TESMART_VIA` can point at an old IP after DHCP moves the host. Re-run `discover --save` or pass `--via http://…` once. |

## Fallback when discovery fails

Use these in order when `discover` finds nothing or `--via auto` errors:

### 1. Typed HTTP base URL (always works if the port is open)

```bash
tesmartctl --via http://192.168.1.20:8080 status
tesmartctl --via http://192.168.1.20:8080 input 3
tesmartctl --via http://192.168.1.20:8080 banner "Hello"
tesmartctl kit 'http://192.168.1.20:8080/kit?app=1' --fresh
curl http://192.168.1.20:8080/version
```

On the same machine as the listener:

```bash
tesmartctl --via http://127.0.0.1:8080 status
# or omit --via and use banner/kit defaults:
tesmartctl banner --listen 8080 "Hello"
tesmartctl kit --listen 8080
```

### 2. Save a stable default

```bash
tesmartctl discover --mdns-name OfficeHDMI --save
# or set by hand in ~/.config/tesmartctl/env:
# TESMART_VIA=http://192.168.1.20:8080
# TESMART_MDNS_NAME=OfficeHDMI
```

After `--save`, later `--via auto` still browses, but prefers the remembered name/URL when several services exist.

### 3. Hostname instead of IP

If your LAN resolves `.local` or you have DHCP DNS:

```bash
tesmartctl --via http://Waynes-Mac-Mini.local.:8080 status
```

### 4. Talk to the switch directly (no listener)

When you only need a one-shot and the listener is down:

```bash
tesmartctl --host 192.168.1.10 status
tesmartctl --host 192.168.1.10 input 3
tesmartctl config set host 192.168.1.10
```

That path never uses mDNS. Prefer the listener for kit, panel, Hubitat, banners, and shared access so only one TCP session owns the switch.

### 5. Confirm the listener is reachable

```bash
# On the listener host:
tesmartctl listen --http 0.0.0.0:8080 --panel --mdns-name OfficeHDMI

# From any client:
curl -sS --connect-timeout 2 http://192.168.1.20:8080/version
ping -c 1 192.168.1.20
```

If `curl` works but `discover` does not, the problem is **multicast / mDNS**, not the HTTP API — stay on `--via http://…` or Hubitat’s typed base URL.

## Related

- [`listener.md`](listener.md) — HTTP routes, callbacks, SSE  
- [`hubitat.md`](hubitat.md) — typed listener host/port in Manager  
- [`panel.md`](panel.md) — kit / panel over the listener  
