# Command listener

`tesmartctl listen` keeps one LAN or RS232 session open so other programs can read and change the active input without paying for a new TCP connection each time. It has two fronts:

* a one-line-in, one-line-out protocol on stdin or a raw TCP port (`--bind`)
* an HTTP server (`--http`) whose routes take the input number as a path segment or a query parameter

Both use the same device connection and the same one-second pacing described in [`protocol.md`](protocol.md). A reply can take about that long. Clients are serialized, so a second request waits until the first has finished talking to the switch.

## Starting it

```bash
tesmartctl listen                    # commands on stdin
tesmartctl listen --bind 9753        # raw TCP, 127.0.0.1:9753
tesmartctl listen --bind 0.0.0.0:9753
tesmartctl listen --json             # one JSON object per line (line protocol only)
tesmartctl listen --http 8080        # HTTP, http://127.0.0.1:8080/
tesmartctl listen --http 0.0.0.0:8080
tesmartctl listen --http 8080 --panel # also the control panel at /panel
```

A bare port binds `127.0.0.1`. `--bind` and `--http` cannot be used together; start two processes to expose both. Ctrl-C stops the process. Connection flags (`--host`, `--serial`, `--inputs`, `--timeout`, `--env-file`) work the same way as the rest of `tesmartctl`; see the README.

There is no authentication. Leave the bind address on `127.0.0.1` unless every machine that can open the port is trusted.

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
| `help` or `h` | no switch traffic | `commands: get \| set <n> \| <n> \| next \| previous \| rotate [1,2,4] \| peek <n> [seconds] \| quit` |
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
{"help": "commands: get | set <n> | <n> | next | previous | rotate [1,2,4] | peek <n> [seconds] | quit"}
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
| `/input?set=3` | switch to 3, then read it back | `{"active_input": 3, "requested": 3}` |
| `/input/3` | same as `/input?set=3` | `{"active_input": 3, "requested": 3}` |
| `/input/Office`, `/set?input=Office` | select by a local name | `{"active_input": 2, "requested": 2, "active_name": "Office", ...}` |
| `/set?input=3` | same as `/input?set=3` | `{"active_input": 3, "requested": 3}` |
| `/set/3` | same as `/input?set=3` | `{"active_input": 3, "requested": 3}` |
| `/status` | active input and saved LAN settings | see below |
| `/status?network=0` | active input only | `"network": null` |

`network=0` also accepts `off`, `false`, and `no`. Skipping the LAN queries avoids four extra paced round trips.

`GET /status` returns everything the switch can report:

```json
{
  "endpoint": "tcp://192.168.1.50:5000",
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
  "rotate": {"supported": true, "configured": true, "mode": "manual", "seconds": null, "cycle": [1, 2, 4]},
  "peek": {"supported": true, "seconds": 5}
}
```

`endpoint` is the address this process used to reach the switch. `unsolicited_reports` lists input numbers the switch announced on its own (front panel, remote, auto-detect, another client) while this request was in flight. It is usually empty.

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

### Examples

```bash
curl 'http://127.0.0.1:8080/input'
curl 'http://127.0.0.1:8080/status'
curl 'http://127.0.0.1:8080/status?network=0'
curl 'http://127.0.0.1:8080/input?set=3'
curl 'http://127.0.0.1:8080/input/3?format=text'
curl -X POST 'http://127.0.0.1:8080/set/2'
```
