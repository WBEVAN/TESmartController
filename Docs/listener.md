# Command listener

`tesmartctl listen` keeps one LAN or RS232 session open so other programs can read and change the active input without paying for a new TCP connection each time. It has two fronts:

* a one-line-in, one-line-out protocol on stdin or a raw TCP port (`--bind`)
* an HTTP server (`--http`) whose routes take the input number as a path segment or a query parameter

Both use the same device connection and the same one-second pacing described in [`protocol.md`](protocol.md). A reply can take about that long. Clients are serialized, so a second request waits until the first has finished talking to the switch.

## Starting the server

On the computer that talks to the HDMI switch:

```bash
tesmartctl install
tesmartctl quickstart
```

`install` saves the switch address, the HTTP listen address, the listen port, the browser UI choice, and mDNS. `quickstart` starts the listener from that file and leaves it running. `/kit` is the floating UI application kit. `/panel` is the browser-only control panel. When those saved values are missing, `quickstart` says so and offers to run `install`. Walk-through: [`install.md`](install.md).

`quickstart` is `tesmartctl listen` with the saved options. The flags below are that same start written out, plus the line-protocol modes `quickstart` does not use.

## Simulator

`--simulate` runs the listener without a TESmart switch. Selecting an input, peek, next, previous, and cycle update an in-memory active input. The HDMI switch is not contacted, so this is for a demo or for development when the hardware is not on the network. Names, enable and disable, banners, and the kit preferences stay local, the same way they do with a real switch. Status reports `simulated: yes` and the endpoint `sim://8` (or `sim://16` when `--inputs 16` is set).

```bash
tesmartctl listen --simulate --http 8080 --panel
tesmartctl quickstart --simulate
```

```bash
tesmartctl listen                    # commands on stdin
tesmartctl listen --bind 9753        # raw TCP, this computer only (127.0.0.1:9753)
tesmartctl listen --bind 0.0.0.0:9753 # raw TCP on every interface
tesmartctl listen --json             # one JSON object per line (line protocol only)
tesmartctl listen --http 8080        # HTTP, this computer only (http://127.0.0.1:8080/)
tesmartctl listen --http 0.0.0.0:8080 # HTTP on every interface (auto mDNS when zeroconf is installed)
tesmartctl listen --http 0.0.0.0:8080 --mdns-name "Office Switch"
tesmartctl listen --http 8080 --panel # /kit floating UI application kit, /panel browser-only control panel
tesmartctl listen --http 0.0.0.0:8080 --no-mdns  # LAN HTTP without advertising
```

Those HTTP options are what `install` writes: `TESMART_HTTP`, `TESMART_HTTP_PORT`, `TESMART_HTTP_PANEL` (`1` or `0`), and `TESMART_HTTP_MDNS`. The switch address stays in `TESMART_HOST` / `TESMART_PORT` or `TESMART_SERIAL`.

A bare port binds `127.0.0.1`, so only this computer can connect. `0.0.0.0` accepts every network interface. Other devices then use the address of the computer running `tesmartctl`, for example `http://192.168.1.20:8080/input/3`. There is no authentication. `--bind` and `--http` cannot be used together; start two processes to expose both. Ctrl-C prints `stopping.`, sends `event: shutdown` to SSE clients and registered callback URLs, then closes the socket, callback thread, and mDNS advertisement. Clients that receive that event show a shutdown status. A crash or a dropped connection does not send it, so those stay offline. When the HTTP listener starts, it POSTs `event: online` to those same callback URLs with the current input, names, and disabled list, so Hubitat can mark the controller online without waiting for the next change. Another Ctrl-C during that teardown is ignored so zeroconf's shutdown does not print a traceback. Connection flags (`--host`, `--serial`, `--inputs`, `--timeout`, `--env-file`) work the same way as the rest of `tesmartctl`; see the README.

There is no authentication. Leave the bind address on `127.0.0.1` unless every machine that can open the port is trusted.

## mDNS / DNS-SD

When the HTTP listener binds a non-loopback address and the optional `zeroconf` package is installed (`pip install 'tesmartctl[mdns]'`), it advertises `_wbevan-tesmart._tcp` on the LAN. Clients can then run `tesmartctl discover` or `tesmartctl --via auto …` instead of typing a host:port.

```bash
tesmartctl listen --http 0.0.0.0:8080 --panel --mdns-name OfficeHDMI
tesmartctl --via auto --mdns-name OfficeHDMI input 3
tesmartctl --via auto kit --fresh
```

Full detail — service type, **`tesmartctl discover`** (all OSes), optional macOS `dns-sd` / Linux `avahi-browse`, **limitations**, and **fallback** when discovery fails — is in [`mdns.md`](mdns.md).

## Line protocol

One command per line. The reply is one line, flushed immediately. The number in a successful reply is the input the switch reports, which can differ from the number that was requested.

| Line | Effect | Text reply |
| --- | --- | --- |
| `get` | read the active input | `2` |
| `port`, `status`, `?` | same as `get` | `2` |
| `set 3` | select input 3, then read it back | `3` |
| `next` or `rotate` | select the next input and wrap after the last | `3` |
| `rotate 1,2,4` | next input within that list | `4` |
| `input 3`, `switch 3`, or just `3` | same as `set 3` | `3` |
| `set Office` | select by a local name from `tesmartctl name` | `2` |
| `peek 3` | show input 3 for the saved seconds, then return; replies once back | `2` |
| `peek 3 10` | same, for 10 seconds | `2` |
| `peek once` | the next `set`, bare number, `next`, or `previous` peeks; later changes stick | `peek once` |
| `peek always` | every one of those peeks until `peek off` | `peek always` |
| `peek off` | those commands stick again | `peek off` |
| `help` or `h` | no switch traffic | the command list, including `peek once`, `peek always`, and `peek off` |
| `quit` or `exit` | close this session | none |
| blank line | ignored | none |

On stdin, `quit` ends the process. On TCP, `quit` closes that client and the server keeps accepting new ones.

Inputs are 1-based and must fall inside the configured count (default 8, `--inputs 16` for a 16×1). `set 9` is rejected before anything is sent:

```text
error: input must be between 1 and 8, got 9
```

An unknown line is `error: ...` and the listener stays up.

`--json` changes every reply into one JSON object:

```json
{"active_input": 2}
{"active_input": 3, "requested": 3}
{"error": "input must be between 1 and 8, got 9"}
{"help": "commands: get | set <n> | <n> | next | previous | rotate [1,2,4] | peek <n> [seconds] | peek once [seconds] | peek always [seconds] | peek off | quit"}
{"active_input": 3, "requested": 3, "previous": 2, "active_name": "Den", "requested_name": "Den", "previous_name": "Office"}
{"previous": 2, "peeked": 3, "seconds": 5, "restored": 2, "interrupted": false, "cancelled": false, "active_input": 2, "error": null}
```

`requested` is present only after a set. Compare it with `active_input` when the switch may have landed on a different input. A `peek` reply arrives after the switch is back; `interrupted` is true when something else changed the input during the peek, in which case nothing is restored and `active_input` is whatever was selected.

The line command `status` is an alias of `get`. It returns the active input only. The full readable status (input plus saved LAN settings) is `tesmartctl status` or HTTP `GET /status`.

### Clients

```bash
printf 'get\n3\nquit\n' | tesmartctl listen

echo get | nc -w 3 127.0.0.1 9753
echo 5 | nc -w 3 127.0.0.1 9753
```

`nc -w` must be longer than the command pacing. One second is often too short when a command is already in flight.

## HTTP

`--http [HOST:]PORT` serves the routes below. GET and POST are the same, so a browser bookmark, `curl`, or a home-automation "call URL" action can drive the switch.

JSON is the default. Add `format=text` to get plain text: a bare input number, or several lines for `/status`.

| URL | Effect | JSON response |
| --- | --- | --- |
| `/` | list routes | `{"routes": {...}}` |
| `/input` | read the active input | `{"active_input": 2}` |
| `/get` | same as `/input` | `{"active_input": 2}` |
| `/rotate` | next input, wrapping after the last | `{"previous": 2, "active_input": 3, "requested": 3}` |
| `/rotate?only=1,2,4` | next input within that list | same shape |
| `/input?rotate=1` | same as `/rotate` | same shape |
| `/peek/3` | show 3 for the saved seconds, then return; replies once back | `{"previous": 2, "peeked": 3, "seconds": 5, "restored": 2, "interrupted": false, "cancelled": false, "active_input": 2, "error": null}` |
| `/peek?input=Office&seconds=8` | same, by name, with an explicit duration | same shape |
| `/peek/once` | the next `/input/N`, `/next`, or `/previous` peeks; later changes stick | `{"peek_follow": "once"}` |
| `/peek/always` | every one of those peeks until `/peek/off` | `{"peek_follow": "always"}` |
| `/peek/off` | those routes stick again | `{"peek_follow": "off"}` |
| `/input?set=3` | switch to 3, then read it back | `{"active_input": 3, "requested": 3}` |
| `/input/3` | same as `/input?set=3` | `{"active_input": 3, "requested": 3}` |
| `/input/Office`, `/set?input=Office` | select by a local name | `{"active_input": 2, "requested": 2, "active_name": "Office", ...}` |
| `/set?input=3` | same as `/input?set=3` | `{"active_input": 3, "requested": 3}` |
| `/set/3` | same as `/input?set=3` | `{"active_input": 3, "requested": 3}` |
| `/status` | active input and saved LAN settings | see below (includes `"version"`) |
| `/status?network=0` | active input only | `"network": null` plus `"version"` |
| `/banner` | last LCD message + history for kit / panel / callbacks (also `tesmartctl banner`) | GET `banner` + `banner_history` (≤10, with `at` / `at_iso`); POST `{"text","seconds"}` or `{"sticky":true}`; POST `{"show":true}` / `{"dismiss":true}` / `{"clear":true}` |
| `/events` | Server-Sent Events for browser clients (same payloads as callbacks) | long-lived `text/event-stream`; named events `hello`, `input_changed`, `names_changed`, `enabled_changed`, `banner`, `peek_follow` |
| `/version` | package name, version, feature tags, and `mdns_name` (the instance this process is advertising, or `null`) | `{"name":"tesmartctl","version":"0.0.7.9","features":[…],"mdns_name":"OfficeHDMI"}` |
| `/compat` | GET or POST compatibility check | `{"min_server":"0.0.7.9","features":["callbacks"]}` → `{"compatible":true,…}` |
| `/enable/4`, `/enable?input=4`, `/enable/Office` | allow that input again | `{"input": 4, "enabled": true, "disabled": [], "inputs": [...]}` |
| `/disable/4`, `/disable?input=Office` | ignore that input for selection, peek, and next/previous | `{"input": 4, "enabled": false, "disabled": [4], "inputs": [...]}` |
| `/names` | GET local input names | `{"names": {"2": "Office"}}` |
| `POST /names` | set or clear local names (JSON body) | `{"names": {"2": "Office", "3": ""}}` — empty string clears |
| `/callbacks` | GET registered callbacks and POST timeout | `{"callbacks":[{"url":"…","name":"Hubitat"}], "urls":[…], "count": 1, "timeout": 10}` |
| `POST /callbacks` | register a URL (optional name; same URL updates the name) | `{"url": "http://…", "name": "Hubitat"}` |
| `POST /callbacks` or `/callbacks/timeout` | set how long each callback POST may wait | `{"timeout": 10}` (seconds; default 10; stored in env) |
| `GET` or `POST /callbacks/health` | probe every registered URL with a `health_check` event | `{"healthy": 1, "unhealthy": 0, "results":[…]}` |
| `DELETE /callbacks` or `POST /callbacks/remove` | remove by URL and/or name | body or `?url=` / `?name=` |
| `POST /callbacks/clear` | remove every URL | `{"callbacks": [], "count": 0, "timeout": 10}` |

### Version and compatibility

Clients (Hubitat, scripts, other apps) can ask what this process is running and whether it meets their needs:

```bash
curl http://127.0.0.1:8080/version

curl -X POST http://127.0.0.1:8080/compat \
  -H 'Content-Type: application/json' \
  -d '{"min_server":"0.0.7.9","client":"hubitat","client_version":"1.5.3","features":["callbacks","names_changed"]}'
```

```json
{
  "compatible": true,
  "version": "0.0.7.9",
  "version_ok": true,
  "features_ok": true,
  "missing_features": [],
  "features": ["status", "names", "callbacks", "compat", "…"],
  "min_server": "0.0.7.9",
  "message": "compatible with tesmartctl 0.0.7.9"
}
```

`compatible` is false when the server is older than `min_server` or a requested feature tag is missing. Unknown HTTP routes return **404** with `"error": "not supported"` (and the path/method) instead of a bare “unknown route”.

### Server-Sent Events (kit / panel)

Browsers cannot register as outbound callback URLs. With `--panel`, the kit and control panel open `GET /api/events` (plain listener also exposes `GET /events`) and receive push frames as soon as the active input, local names, enablement, LCD banner, or peek-follow mode changes. Event bodies match the callback JSON (`event`, `active_input`, `banner`, …); `banner` frames also include `banner_history` when available. Named SSE events: `hello` (on connect), `input_changed`, `names_changed`, `enabled_changed`, `banner`, `peek_follow`, `shutdown` (Ctrl-C, before the socket closes), `online` (listener startup; also POSTed to registered callback URLs with the current input, names, and disabled list). A `: keepalive` comment is sent about every 20 seconds. Feature tag: `sse`.

Setting the mode (`/peek/once`, `/peek/always`, `/peek/off`, or the panel control) sends `mode` and `peek_follow` (`off`, `once`, or `always`), plus `seconds` when a duration was stored. That frame has no `followed` and no `previous`.

When an armed follow peek actually moves the switch, a second `peek_follow` goes out as soon as the return input is known, before a blocking peek waits for the switch to come back. `mode` and `peek_follow` are the mode after the claim (`off` for Once, `always` when Always stays armed). `followed` is what this peek consumed (`once` or `always`). `previous` is the input that will be restored. `active_input` is the input showing for the peek. `input_changed` is unchanged and still describes the move on its own.

```json
{
  "event": "peek_follow",
  "mode": "off",
  "peek_follow": "off",
  "followed": "once",
  "previous": 1,
  "active_input": 4,
  "seconds": 8
}
```

```bash
# watch frames (Ctrl-C to stop)
curl -N http://127.0.0.1:8080/events
```

SSE talks to the **listener**, not the HDMI switch. While the switch is unreachable, kit/panel still keep the EventSource open and poll `/api/state` about every **2 seconds** so they reconnect quickly when the box comes back. When online with SSE healthy, that poll slows to about **30 seconds** as a safety net. That poll only reaches the host and port the page was opened on. `tesmartctl --via auto kit` keeps browsing after the first connection. While that listener is stopped it reloads the kit if the same mDNS name is advertising on a different host or port, without restarting the window. If the client is bound to an mDNS name and the process that answers on that same host and port advertises a different name (`mdns_name` on `/version`, `/api/state`, and the SSE `hello` frame), the reconnect fails and the kit stays offline until the named listener is back. A typed `--via http://…` URL is not re-browsed. Hubitat continues to use outbound `POST /callbacks` URLs; it does not use `/events`.

### Change callbacks

While `tesmartctl listen --http` is running it watches the switch for input reports (front panel, remote, auto-detect, or another client) and also notices changes made through this HTTP API — including local name updates via `POST /names` or `POST /api/names`. Each registered URL receives a JSON `POST`. The wait per URL defaults to **10 seconds** and is configurable only via the API (not the panel UI):

```bash
curl -X POST http://127.0.0.1:8080/callbacks \
  -H 'Content-Type: application/json' \
  -d '{"url":"http://192.168.1.20/apps/api/123/tesmart-event?access_token=…","name":"Hubitat home"}'

curl -X POST http://127.0.0.1:8080/callbacks/timeout \
  -H 'Content-Type: application/json' \
  -d '{"timeout":10}'

curl http://127.0.0.1:8080/callbacks/health
```

Input change:

```json
{
  "event": "input_changed",
  "previous": 2,
  "active_input": 3,
  "previous_name": "New Mac",
  "active_name": "Work",
  "input_count": 8,
  "source": "unsolicited",
  "endpoint": "tcp://172.16.0.222:5000",
  "names": {"1": "Old Mac", "2": "New Mac", "3": "Work"}
}
```

Name change (panel, Hubitat Manager, or `POST /names`):

```json
{
  "event": "names_changed",
  "changed": {"2": {"from": "Office", "to": "Den"}},
  "names": {"1": "Old Mac", "2": "Den"},
  "active_input": 3,
  "active_name": "Work",
  "input_count": 8,
  "source": "command",
  "endpoint": "tcp://172.16.0.222:5000"
}
```

Enable or disable from `GET /enable/N`, `GET /disable/N`, the panel (`POST /api/enabled`), or `tesmartctl --via auto enable 4`. A command without `--via` still writes only the env file on this computer and does not send this event to a listener that is already running.

```json
{
  "event": "enabled_changed",
  "disabled": [4, 5],
  "names": {"1": "Old Mac", "2": "Den"},
  "input_count": 8,
  "source": "command"
}
```

LCD banner (`POST /banner`, `POST /api/banner`, or `tesmartctl banner "…"`; Hubitat updates banner attributes on this event):

```json
{
  "event": "banner",
  "banner": {
    "id": 3,
    "text": "Door unlocked",
    "at": 1710000000.123,
    "mode": "timed",
    "seconds": 8,
    "until": 1710000008.123,
    "remaining": 8.0,
    "showing": true
  },
  "source": "command",
  "endpoint": "tcp://172.16.0.222:5000",
  "input_count": 8,
  "at": "2026-03-17T12:00:00Z"
}
```

The last message is kept after it leaves the LCD (`showing: false`). `mode` is `timed` or `sticky`. `POST {"dismiss": true}` hides it for every client that follows the listener (kit, panel, Hubitat); `POST {"show": true}` puts it back; `POST {"clear": true}` erases it and history (`banner: null`, `banner_history: []`). History (up to 10, with `at` / `at_iso`) is stored in the listener env file, so after a restart kits still receive `banner` / `banner_history` and can show the message icons — the LCD does not auto-pop the last message until the client shows it or a new one is sent. `/api/state` includes the same fields.

`source` is `unsolicited` for switch-originated input reports and `command` when this listener changed the input, names, or banner. The same URL registered twice stays a single entry (a new `name` updates the label). Entries are stored as JSON objects in the env file (`TESMART_CALLBACKS`); older plain-URL lists still load. The POST timeout (`TESMART_CALLBACK_TIMEOUT`) also lives in the env file.

`GET` / `POST /callbacks/health` POSTs `{"event":"health_check","ok":true}` to every registered URL and returns per-client `ok` / `status` / `ms` (or `error`). Clients should answer with HTTP 2xx and ignore the body for state changes. `GET /callbacks` also includes a `last` object per entry when this process has delivered (or failed to deliver) an event or health probe since it started.

With `--panel`, manage callbacks from the **API** dialog (`GET` / `POST` / `DELETE /api/callbacks`, plus `/api/callbacks/health`); set timeout the same way (`{"timeout": 10}`), not from a panel control.

`network=0` also accepts `off`, `false`, and `no`. Skipping the LAN queries avoids four extra paced round trips.

`/peek/once` and `/peek/always` do not move the switch. They arm this listener so the next plain input change — `/input/N`, `/set/N`, `/next`, `/previous`, `/rotate` — peeks for the saved number of seconds and then returns. `?seconds=8` on the arm request uses 8 seconds for those peeks instead. `once` clears itself when that change is taken, so the change after that sticks. `always` stays until `/peek/off`. An explicit `/peek/3` does not use up a `once`. The arm lives in the listener process and is forgotten when the process stops. Reading `/input` does not use it up. The line commands `peek once`, `peek always`, and `peek off` are the same idea on stdin and on `--bind`.

`GET /status` returns everything the switch can report:

```json
{
  "endpoint": "tcp://192.168.1.50:5000",
  "version": "0.0.7.9",
  "active_input": 2,
  "active_name": "Office",
  "input_count": 8,
  "network": {
    "ip": "192.168.1.50",
    "port": "5000",
    "gateway": "192.168.1.1",
    "mask": "255.255.255.0"
  },
  "unsolicited_reports": [],
  "names": {"1": "Living room", "2": "Office"},
  "enabled": [1, 2, 3, 5, 6, 7, 8],
  "disabled": [4],
  "rotate": {"supported": true, "configured": true, "mode": "manual", "seconds": null, "cycle": [1, 2, 4]},
  "peek": {"supported": true, "seconds": 5},
  "last_sent": {"buzzer": false, "led_timeout": "30s", "auto_detect": true}
}
```

`endpoint` is the address this process used to reach the switch. `unsolicited_reports` lists input numbers the switch announced on its own (front panel, remote, auto-detect, another client) while this request was in flight. It is usually empty.

`last_sent` is what *this host* last wrote for buzzer, front-panel display timeout, and auto input detection. The switch cannot report those values back; tesmartctl stores them in the env file (`TESMART_LAST_SENT`) whenever you change them via the panel, `POST /api/buzzer` / `/api/led` / `/api/autodetect`, or `tesmartctl buzzer` / `led` / `autodetect`. After a restart, `/status` and `/api/state` still return the remembered values so other applications can stay in sync.

`GET /status?format=text`:

```text
active_input: 2
ip: 192.168.1.50
port: 5000
gateway: 192.168.1.1
mask: 255.255.255.0
```

`/input/3?format=text` returns `3` and a newline. Buzzer, LED timeout, auto-detect, and LAN address changes are not on these routes. Change those from the command line, or from the control panel when the listener was started with `--panel`. The panel and its `/api` routes are documented in [`panel.md`](panel.md). Without `--panel`, `/panel` and `/api` return 404.

### Status codes

| Code | When |
| --- | --- |
| 200 | the command was carried out, or `/` listed the routes |
| 400 | the number is missing, not an integer, out of range, or the switch rejected the operation |
| 404 | the path is not a route; the body includes the route list |
| 502 | the connection to the switch failed while handling the request |

Error bodies are `{"error": "..."}`. With `format=text` the body is that message as plain text. Responses send `Cache-Control: no-store`.

Putting these URLs on a page you already have is covered in [`embed.md`](embed.md). A link aimed at a hidden iframe works from another site. `fetch` from that page works when the page is served from this listener's own origin.

### Examples

```bash
curl 'http://127.0.0.1:8080/input'
curl 'http://127.0.0.1:8080/status'
curl 'http://127.0.0.1:8080/status?network=0'
curl 'http://127.0.0.1:8080/input?set=3'
curl 'http://127.0.0.1:8080/input/3?format=text'
curl -X POST 'http://127.0.0.1:8080/set/2'
```
