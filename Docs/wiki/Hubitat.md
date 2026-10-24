# Hubitat

Drive the TESmart switch from Hubitat Elevation: one Manager app, one controller device, and a child device per input. Optional push callbacks keep Hubitat in sync when something else changes the active input. (Browser kit/panel use Server-Sent Events instead; see [Listener & HTTP](Listener-and-HTTP).)

## What you need

1. A server started with `tesmartctl install`, then left running with `tesmartctl quickstart` (**0.0.7.10+**, on a host and port Hubitat can reach)
2. The Hubitat package from this repo (`hubitat/`) — currently **1.6.0** (shown on the Manager screen)
3. Network path from the Hub to the machine running the listener

## Install the package

1. On Hubitat: **Apps** → **Hubitat Package Manager** (or manual import)
2. Install / update from the package manifest in `hubitat/packageManifest.json`
3. Drivers: TESmart Controller, TESmart Input
4. App: TESmart Manager

Manual Groovy import paths (if not using HPM):

- `hubitat/TESmart-Manager.groovy`
- `hubitat/TESmart-Controller.groovy`
- `hubitat/TESmart-Input.groovy`

## Configure

1. Open **TESmart Manager**
2. Set the HTTP base URL of the listener, e.g. `http://192.168.1.20:8765`
3. Create / refresh the controller and child input devices
4. Map input names to match your local `tesmartctl name` labels if you use them

Selecting an input child (or the controller command) issues HTTP to the listener, which talks to the switch.

## Callbacks (optional)

So Hubitat reflects changes made from the kit, CLI, or Stream Deck:

1. Point the listener’s Hubitat callback URL at the Manager / controller endpoint (see repo [Docs/hubitat.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/hubitat.md))
2. Keep `tesmartctl quickstart` running whenever you want live sync

The listener also posts `enabled_changed` when an input is enabled or disabled from the panel. The current Hubitat drivers leave the devices as they are when that event arrives.

## Banner & peek follow (controller device)

On **TESmart Controller** (package 1.6.0+):

| Command | Effect |
| --- | --- |
| `deviceNotification` | **Notification** capability — Rule Machine Notify / Notifications app → kit LCD |
| `showBanner` | Timed LCD message on kit/panel (`POST /banner`) |
| `showStickyBanner` | Sticky until `dismissBanner` |
| `bannerAction` | Dropdown: dismiss / reshow / clear (clear = message **and** history) |
| `setPeekFollow` | `off` / `once` / `always` (same as kit PEEK) |

Preferences **Notifications stay until dismissed** and **Notification seconds** control how `deviceNotification` behaves. Attributes: `bannerText`, `bannerShowing`, `bannerMode`, `bannerRemaining`, `peekFollow`. Push `event: banner` updates those without polling.

## Typical automations

- Rule: motion / mode → select input N
- Dashboard tiles for each input child
- Rule: mode / contact → `showBanner` / `showStickyBanner` on the controller
- Rule: set peek follow to `once` before an input change from another tile

## Deeper detail

Repo doc: [Docs/hubitat.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/hubitat.md)

## Next

- [Control panel](Control-Panel)
- [Listener & HTTP](Listener-and-HTTP)
- [Troubleshooting](Troubleshooting)
