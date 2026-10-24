# Hubitat integration

Drive a TESmart 8-port or 16-port HDMI/KVM switch from [Hubitat Elevation](https://hubitat.com/) through the `tesmartctl` REST server.

Use **TESmart Manager** (the app) for setup. It walks through the server address, optional port names, and creates the controller device plus child input switches.

<p align="center">
  <img src="https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/readme-banner.png" alt="tesmartctl" width="640"/>
</p>

## How the switch is modelled

A TESmart unit is **not** a bank of independent relays. Exactly one input is active at a time. Hubitat’s closest built-in idea is **MediaInputSource**: one `mediaInputSource` value and `setInputSource`.

| Layer | What you get |
| --- | --- |
| **TESmart Manager** (app) | Guided setup, `/compat` version check, port names on the server, push-callback registration |
| Parent device (`TESmart Controller`) | `MediaInputSource` + `Notification`, refresh polling, next / previous / peek, banners, callback-driven updates |
| Child switches (`TESmart Input`) | One dashboard tile per port; turning one **on** selects that input (radio group) |

App and drivers use the Hubitat namespace **`wbevan_tesmart`**. If you previously installed under `tesmart`, uninstall the old app/devices and install fresh (or update via HPM and recreate the Manager device).

### Push notifications (callbacks)

With a recent `tesmartctl listen --http`, **TESmart Manager** can register this hub as a named callback (`name` like `Hubitat <location>`). When the switch input changes, or when local input names are updated on the server, the listener POSTs JSON (`event`: `input_changed` or `names_changed`) to Hubitat so the controller and child labels update without waiting for the next poll. Enabling or disabling an input from the panel also posts `event`: `enabled_changed` (`disabled`, `names`, `input_count`). The current Hubitat drivers leave the devices as they are when that event arrives. The same channel receives `event: banner` when an LCD message is shown or cleared (`POST /banner` / `POST /api/banner` / `tesmartctl banner`); the controller updates `bannerText` / `bannerShowing` / `bannerMode`. `GET /callbacks/health` on the server can probe the hub with `event: health_check` (acknowledged without changing devices). Turn this on under **Push notifications** in the Manager app (requires OAuth enabled for the app — HPM sets this when `oauth` is true in the package).

Browser kit/panel clients do **not** use this outbound POST path. They open `GET /api/events` (Server-Sent Events) for the same logical events. See [`listener.md`](listener.md#server-sent-events-kit--panel).

## Prerequisites

On a host Hubitat can reach, run a recent enough `tesmartctl` (this Hubitat package **1.6.0** checks `/compat` and requires **0.0.7.10+**). Configure that server first, and choose a listen address the hub can open:

```bash
tesmartctl install
tesmartctl quickstart
```

Confirm the server version:

```bash
curl http://192.168.1.20:8080/version
curl -X POST http://192.168.1.20:8080/compat \
  -H 'Content-Type: application/json' \
  -d '{"min_server":"0.0.7.10","client":"hubitat","client_version":"1.6.0"}'
```

Optional local names (shown in Hubitat as `2 — Office`):

```bash
tesmartctl name 2 Office
tesmartctl name 3 Den
```

Confirm:

```bash
curl http://192.168.1.20:8080/status?network=0
```

There is no authentication on the server — only do this on a trusted network. Details: [`listener.md`](listener.md).

Hubitat does **not** browse mDNS. Always enter the listener’s LAN IP (or a stable hostname) and port in Manager. For CLI discovery on other machines, see [`mdns.md`](mdns.md).

## Install

Packaging follows the [HPM developer documentation](https://hubitatpackagemanager.hubitatcommunity.com/devs1.html).

### Option A — Hubitat Package Manager (recommended)

1. Install [Hubitat Package Manager](https://hubitatpackagemanager.hubitatcommunity.com/) if needed.

2. **If you already see a package named `null` in Uninstall:** select it → uninstall it. That entry was an incomplete install and blocks a clean setup.

3. Install the package (either method works; both show **TESmart Controller**):

   | Method | URL |
   | --- | --- |
   | **Install → From a URL** | [`packageManifest.json`](https://raw.githubusercontent.com/WBEVAN/TESmartController/main/hubitat/packageManifest.json) |
   | **Install → From a URL** (same package) | [`repository.json`](https://raw.githubusercontent.com/WBEVAN/TESmartController/main/hubitat/repository.json) |
   | **Add custom repository**, refresh, then browse | [`repository.json`](https://raw.githubusercontent.com/WBEVAN/TESmartController/main/hubitat/repository.json) |

4. Leave **Configure the installed package after installation completes** enabled when offered. HPM opens **TESmart Manager** to finish setup.

### Option B — Manual import

1. **Drivers Code** → import (child first):
   - [`TESmart-Input.groovy`](https://raw.githubusercontent.com/WBEVAN/TESmartController/main/hubitat/TESmart-Input.groovy)
   - [`TESmart-Controller.groovy`](https://raw.githubusercontent.com/WBEVAN/TESmartController/main/hubitat/TESmart-Controller.groovy)
2. **Apps Code** → import:
   - [`TESmart-Manager.groovy`](https://raw.githubusercontent.com/WBEVAN/TESmartController/main/hubitat/TESmart-Manager.groovy)

## Setup walkthrough (TESmart Manager)

1. **Apps** → **Add User App** → **TESmart Manager** (skipped if HPM already opened it)
2. Read the intro (banner loads from GitHub).
3. **Configure server host & port** — enter the machine running `tesmartctl`, run the connection test. Manager calls `POST /compat` first (needs **tesmartctl 0.0.7.10+**); an old server fails with an upgrade message. The Manager main screen shows Hubitat package **1.6.0**.
4. **Edit port names** — **Refresh Names**, edit the fields, then **Done**. Hubitat writes them with `POST /names` to the tesmartctl server. You can still use `tesmartctl name set N Label` on the host; name changes also notify Hubitat when callbacks are enabled.
5. Set the device label, leave **Create child switch per input** on if you want dashboard tiles.
6. Leave **Push notifications** on so Manager registers a named callback with the server (OAuth access token).
7. **Done** — creates/updates the **TESmart Controller** device, saves names to the server, registers callbacks, and pushes connection settings.

After the next device **Refresh** (or a callback), children labeled `Input N` are recreated with server names (e.g. `Old Mac`, `New Mac`). Unnamed ports stay `Input N`.

## Commands and attributes

### Parent (`TESmart Controller`)

| Command | Effect |
| --- | --- |
| `refresh` | `GET /status?network=0` (rebuilds named lists + child labels) |
| Preferences → **Switch to input** | Device-page control: switch port using a live `1 - Old Mac` list (rebuilt every time you open Preferences) |
| Preferences → **Peek at input** / **Peek seconds** | Device-page peek; optional duration overrides the server default |
| `setInputSource` / `peek` (Commands) | For Rule Machine / automations (string like `2` or `2 - New Mac`). Not a live named dropdown — Hubitat cannot rebuild command ENUM lists |
| `nextInput` / `previousInput` | `GET /next` / `GET /previous` |
| `setPeekFollow` | `GET /peek/off` · `/peek/once` · `/peek/always` (optional seconds for once/always) |
| Preferences → **Peek follow mode** | Same as `setPeekFollow` from the device page |
| `deviceNotification` (Notification) | Same as timed/sticky banner — pick this device in Rule Machine **Notify** / Notifications app |
| `showBanner` / `showStickyBanner` | `POST /banner` timed or sticky LCD message (kit/panel) |
| `bannerAction` | Device-page dropdown + Run: **dismiss** (hide, keep history), **reshow** (last message again), **clear** (erase message **and** history) |
| `createChildInputs` / `deleteChildInputs` | manage child devices |

| Attribute | Meaning |
| --- | --- |
| `mediaInputSource` | Active label (name if set, else port number) |
| `supportedInputs` | JSON list of labels |
| `activeInput` | Active port number |
| `previousInput` | Prior port when known (from callbacks / switches) |
| `activeName` | Local / override name when known |
| `inputCount` | 8 or 16 |
| `peekFollow` | `off` / `once` / `always` (from `/status` or `setPeekFollow`) |
| `bannerText` | Last / current LCD message text |
| `bannerShowing` | `true` / `false` |
| `bannerMode` | `timed` / `sticky` / `none` |
| `bannerRemaining` | Seconds left when timed and showing (else `0`) |
| `healthStatus` | `online` / `offline` |
| `lastChangeSource` | `command` / `unsolicited` (from callbacks when present) |
| `lastRefresh` | Timestamp of last successful status apply |

### Children (`TESmart Input`)

| Link | Meaning |
| --- | --- |
| Device label | Name when set (`Office`), otherwise `Input 3` |
| `inputNumber` | Port number |
| `inputName` | Local / Hubitat name when set (omitted if unnamed) |
| `parentDevice` | Parent controller display name |
| DNI | `{parentDni}-input-{n}` |

**On** selects that port. **Off** on the active port is ignored so exactly one input stays selected.

## Dashboard

Add child devices as Switch tiles. The lit tile is the active input.

## Rule Machine

- Select a port: `setInputSource` with `3` or `3 — Office` (driver sends the port number)
- Condition: attribute `activeInput`
- Notify / Notifications app: choose the **TESmart Controller** as a notification device (LCD on kit/panel). Preferences control sticky vs timed seconds.
- Banner: `showBanner` / `showStickyBanner`; then `bannerAction` with dismiss / reshow / clear
- Peek follow: `setPeekFollow` with `once`, `always`, or `off`

## Server routes used

| Hubitat action | HTTP |
| --- | --- |
| Compatibility / install check | `POST /compat` (fallback `GET /version`) |
| Refresh / poll | `GET /status?network=0` (includes `version`, `names`) |
| Select input | `GET /input/N` |
| Next / previous | `GET /next`, `GET /previous` |
| Peek | `GET /peek/N` |
| Peek follow | `GET /peek/off`, `/peek/once`, `/peek/always` |
| Banner | `POST /banner` (`text` / `sticky` / `dismiss` / `show` / `clear`) |
| Load / save port names | `GET /names`, `POST /names` |
| Register / remove push URL | `POST /callbacks`, `POST /callbacks/remove` |
| Server → Hubitat push | `POST` to Manager `/tesmart-event` (`input_changed`, `names_changed`, `enabled_changed`, `banner`, `health_check`). The current drivers apply input, name, and banner events. `enabled_changed` is delivered and left unused. |

Full route list: [`listener.md`](listener.md).

## Package files

| Path | Role |
| --- | --- |
| [`hubitat/TESmart-Manager.groovy`](../hubitat/TESmart-Manager.groovy) | Setup app (`primary`) |
| [`hubitat/TESmart-Controller.groovy`](../hubitat/TESmart-Controller.groovy) | Parent driver |
| [`hubitat/TESmart-Input.groovy`](../hubitat/TESmart-Input.groovy) | Child driver |
| [`hubitat/packageManifest.json`](../hubitat/packageManifest.json) | HPM package manifest ([devs docs](https://hubitatpackagemanager.hubitatcommunity.com/devs1.html)) |
| [`hubitat/repository.json`](../hubitat/repository.json) | HPM repository listing; also installable as the same package (includes `packageName`) |
