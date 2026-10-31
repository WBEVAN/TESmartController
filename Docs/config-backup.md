# Config backup (export / import)

Move a listener’s local configuration from one computer to another without re-entering names, enablement, cycle settings, peek defaults, LCD message history, callbacks, or kit look/feel prefs.

## What is included

The portable file is JSON with `"format": "tesmartctl-config"`. It wraps:

| Section | Contents |
|---|---|
| `env` | The full env file map: switch host/port/serial/inputs, input names, disabled inputs, rotate/cycle, peek seconds, banner message history, callbacks and timeout, last-sent buzzer/LED/auto-detect, HTTP listen / panel / mDNS, client via keys |
| `kit` (optional) | Floating kit prefs from `kit.json` (themes, brightness, auto-dim, hide disabled, show names, window position, and so on) |

Not included: the live active input on the switch, the switch’s own LAN settings, or whether a banner is currently showing.

## CLI

```bash
# On the source listener host
tesmartctl config export office.json

# On the destination host (replaces the env file; writes kit prefs when present)
tesmartctl config import office.json

# Or seed install, then confirm / adjust
tesmartctl install --import office.json
tesmartctl quickstart
```

`config export` with no path writes JSON to stdout. `--no-kit` omits kit prefs. `config import --merge` keeps existing env keys the dump does not set. After a plain import on a running listener, restart `quickstart` (or use the panel Import, which reloads message history in memory).

## Panel

With `listen --http --panel` (or quickstart with the panel on), the **Backup** card exports a downloadable JSON file and imports one through a confirmation dialog (no browser alert). Import replaces this listener’s env file and reloads banner history for open kits and panels.

## Same machine vs new host

Importing onto a new computer often needs a different switch IP or HTTP bind. Use `install --import` (or run `install` after `config import`) so you can confirm those settings. Callback URLs that point at the old Hubitat or other hosts may still need editing after the move.
