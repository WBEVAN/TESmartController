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
| **TESmart Manager** (app) | Guided setup, connection test, optional Hubitat-side port names |
| Parent device (`TESmart Controller`) | `MediaInputSource`, refresh polling, next / previous / peek |
| Child switches (`TESmart Input`) | One dashboard tile per port; turning one **on** selects that input (radio group) |

## Prerequisites

On a host Hubitat can reach:

```bash
tesmartctl listen --http 0.0.0.0:8080
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
3. **Configure server host & port** — enter the machine running `tesmartctl`, run the connection test.
4. **Edit port names** — **Refresh Names**, edit the fields, then **Done**. Hubitat writes them with `POST /names` to the tesmartctl server (needs a recent tesmartctl that includes that route). You can still use `tesmartctl name set N Label` on the host.
5. Set the device label, leave **Create child switch per input** on if you want dashboard tiles.
6. **Done** — creates/updates the **TESmart Controller** device, saves names to the server, and pushes connection settings.

After the next device refresh, child devices show labels like `Office` or `Input 3`.

## Commands and attributes

### Parent (`TESmart Controller`)

| Command | Effect |
| --- | --- |
| `refresh` | `GET /status?network=0` (rebuilds named lists + child labels) |
| Preferences → **Switch to input** / **Peek at input** | Named list (`2 — Office`) rebuilt each time you open the device page — use these on the device UI |
| `setInputSource` / `peek` (Commands) | Same values for Rule Machine; Hubitat often freezes these command ENUMs as bare numbers — prefer Preferences on the device page |
| `nextInput` / `previousInput` | `GET /next` / `GET /previous` |
| `createChildInputs` / `deleteChildInputs` | manage child devices |

| Attribute | Meaning |
| --- | --- |
| `mediaInputSource` | Active label (name if set, else port number) |
| `supportedInputs` | JSON list of labels |
| `activeInput` | Active port number |
| `activeName` | Local / override name when known |
| `inputCount` | 8 or 16 |
| `healthStatus` | `online` / `offline` |

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

## Server routes used

| Hubitat action | HTTP |
| --- | --- |
| Refresh / poll | `GET /status?network=0` |
| Select input | `GET /input/N` |
| Next / previous | `GET /next`, `GET /previous` |
| Peek | `GET /peek/N` |

Full route list: [`listener.md`](listener.md).

## Package files

| Path | Role |
| --- | --- |
| [`hubitat/TESmart-Manager.groovy`](../hubitat/TESmart-Manager.groovy) | Setup app (`primary`) |
| [`hubitat/TESmart-Controller.groovy`](../hubitat/TESmart-Controller.groovy) | Parent driver |
| [`hubitat/TESmart-Input.groovy`](../hubitat/TESmart-Input.groovy) | Child driver |
| [`hubitat/packageManifest.json`](../hubitat/packageManifest.json) | HPM package manifest ([devs docs](https://hubitatpackagemanager.hubitatcommunity.com/devs1.html)) |
| [`hubitat/repository.json`](../hubitat/repository.json) | HPM repository listing; also installable as the same package (includes `packageName`) |
