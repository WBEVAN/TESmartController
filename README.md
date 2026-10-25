# TESmart HDMI Switch & KVM Controller — REST API, Web UI, Hubitat and CLI

![tesmartctl — your TESmart switch, your controls](Docs/images/readme-banner.png)

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)

Control TESmart 8-port and 16-port HDMI switches and KVM switches over
Ethernet/LAN or RS232 using Python.

`tesmartctl` provides:

- TESmart REST API / HTTP API
- Browser-based TESmart control panel
- Command-line TESmart controller
- Python API/library
- LAN and RS232 control
- Home Assistant, Hubitat, Stream Deck and Bitfocus Companion integration
- Input switching, input naming, cycling and peeking
- Network/IP configuration
- Zeroconf mDNS / DNS-SD so clients can find the HTTP listener (`discover`, `--via auto`) without typing a host:port

## Supported TESmart models

tesmartctl supports the TESmart LAN/RS232 protocol used by many
8-port and 16-port HDMI/KVM switches, including:

- HSW0801 / HSW0801A10 / HSW0801A1U
- HKS0801 / HKS0801A20 / HKS0801A30 / HKS0801A40
- HSW1601 / HSW1601A10 / HSW1601A1U
- HKS1601 / HKS1601A10 / HKS1601A1U

Other TESmart models using the same AA BB 03 ... EE protocol may also work.

> [!IMPORTANT]
> **Independent project.** 
> This is an independent, community-developed project. It is not published by, endorsed by, sponsored by, or affiliated with TESmart.
>
> TESmart is a trademark of its respective owner.

LAN control uses only the Python standard library. Optional extras: `pyserial` for RS232, `zeroconf` for mDNS, `pywebview` for the kit window. Python 3.10 or newer. Licensed under the [MIT License](https://github.com/WBEVAN/TESmartController/blob/main/LICENSE).

Install with `pip install tesmartctl` (add `[mdns]` for discovery). The package is on [PyPI](https://pypi.org/project/tesmartctl/); the source is on [GitHub](https://github.com/WBEVAN/TESmartController).

## Why tesmartctl?

Unlike tools that provide only a CLI or a Home Assistant integration,
tesmartctl can run as a standalone TESmart controller and exposes the
switch through:

- REST/HTTP
- Web browser
- Webview Kit (`tesmartctl[kit]`)
- Python
- Command line
- TCP
- RS232 (`tesmartctl[serial]`)
- mDNS discovery (`tesmartctl[mdns]`)

This allows a TESmart switch to be controlled from Home Assistant,
Hubitat, Stream Deck, Bitfocus Companion, curl, shell scripts or any
application capable of making an HTTP request.


## What it does

| | |
| --- | --- |
| Read | Active input, and the IP, port, gateway, and mask stored in the switch |
| Switch | Select an input by number or by a local name, or step next and previous through a list you choose |
| Enable | Enable or disable an input. Stored on the listener. A disabled input is skipped for selection, peek, and next/previous |
| Peek | Show another input for a few seconds, then return. Not a cycle |
| Cycle | Walk a chosen list by hand (`next` / `previous`) or on a timer (`cycle run`) |
| Settings | Buzzer, front-panel display timeout, auto input detection. The switch cannot report these back |
| Address | Change the switch's IP, port, gateway, and mask, with two warnings and a read-back. Applied only after a power cycle |
| Listen | Keep one connection open: a line protocol on stdin or TCP, or HTTP URLs for Stream Deck, Companion, Home Assistant, and similar |
| mDNS | Optional zeroconf advertise/browse (`tesmartctl[mdns]`): clients use `discover` or `--via auto` to reach the listener; see [`Docs/mdns.md`](Docs/mdns.md) |
| Panel / kit | `/kit` is the floating UI application kit (`tesmartctl kit`). `/panel` is the browser-only control panel. `tesmartctl install` turns both on by default; `tesmartctl quickstart` serves them |
| Banner | Brief LCD message on kit/panel via `tesmartctl banner` or `POST /banner` (needs `listen --http`) |

The switch cannot say which HDMI inputs have a signal. A cycle is the list you configure, stored on this computer, not in the switch. Local input names are stored the same way.

| Document | What it covers |
| --- | --- |
| This file | Package install, server setup, every command |
| [`Docs/install.md`](Docs/install.md) | `tesmartctl install`, then `tesmartctl quickstart` on the server |
| [Project site](https://wbevan.github.io/TESmartController/) | GitHub Pages landing (install + doc links) |
| [Wiki](https://github.com/WBEVAN/TESmartController/wiki) | Setup → kit → Hubitat → panel (source: `Docs/wiki/`) |
| [Windows](https://github.com/WBEVAN/TESmartController/wiki/Windows) | Python and pip, Command Prompt quotes, `start` for the server and a client |
| [`Docs/protocol.md`](https://github.com/WBEVAN/TESmartController/blob/main/Docs/protocol.md) | Byte protocol the switch speaks |
| [`Docs/listener.md`](https://github.com/WBEVAN/TESmartController/blob/main/Docs/listener.md) | Long-running line protocol and HTTP URLs |
| [`Docs/mdns.md`](https://github.com/WBEVAN/TESmartController/blob/main/Docs/mdns.md) | mDNS advertise / discover / `--via auto`, limitations, connection fallbacks |
| [`Docs/panel.md`](https://github.com/WBEVAN/TESmartController/blob/main/Docs/panel.md) | Floating UI application kit (`/kit`) and browser-only control panel (`/panel`) |
| [`Docs/embed.md`](https://github.com/WBEVAN/TESmartController/blob/main/Docs/embed.md) | Input buttons on a page you already have |
| [`Docs/hubitat.md`](https://github.com/WBEVAN/TESmartController/blob/main/Docs/hubitat.md) | Hubitat drivers (`hubitat/`) over the REST listener |

## Install

From PyPI:

```bash
pip install tesmartctl               # adds the tesmartctl command
pip install 'tesmartctl[serial]'     # also installs pyserial, for the RS232 port
pip install 'tesmartctl[mdns]'       # zeroconf: LAN advertise / discover / --via auto
pip install --upgrade tesmartctl     # move to the newest release
```

`pipx install tesmartctl` works too and keeps the command out of any one project's environment.

From a clone, for working on the code:

```bash
git clone https://github.com/WBEVAN/TESmartController.git
cd TESmartController
pip install -e .                     # editable: edits in this tree take effect immediately
pip install -e '.[serial]'
```

Without installing, run from the repository directory:

```bash
python3 -m tesmart status
```

If the shell cannot find `tesmartctl` after install, open a new shell, or rehash your version manager. Requires Python 3.10 or newer.

## Windows

Confirm Python and pip before `pip install` or `tesmartctl install`. In Command Prompt, both of these must print a version:

```cmd
python --version
python -m pip --version
```

If `python` is not the interpreter you installed, try the Python launcher and use that same command for pip:

```cmd
py -3 --version
py -3 -m pip --version
```

Python 3.10 or newer is required. `python -m pip` (or `py -3 -m pip`) installs into that same Python, which is the one that provides `tesmartctl`. If `python` opens the Microsoft Store instead of printing a version, install Python from [python.org](https://www.python.org/downloads/windows/) with **Add python.exe to PATH** selected, then run the checks again.

Command Prompt keeps single quotes as part of the text. When a pip command in this README is wrapped in single quotes, type it with double quotes:

```cmd
pip install "tesmartctl[serial]"
pip install "tesmartctl[mdns]"
pip install "tesmartctl[kit]"
pip install -e ".[serial]"
```

`tesmartctl quickstart` stays in its window for as long as the server runs. Open the server and a client in separate Command Prompt windows with [`start`](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/start). The first quoted string is the window title. The command follows it:

```cmd
start "TESmart server" tesmartctl quickstart
start "TESmart client" tesmartctl --via auto kit
```

[`/b`](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/start) keeps the command in this console instead of opening a new window. The first quoted string is still the window title, so leave it empty. With `/b`, Ctrl+C is ignored; use Ctrl+Break to stop the command.

```cmd
start /b "" tesmartctl quickstart
start /b "" tesmartctl --via auto kit
```

The same notes are on the [Windows wiki page](https://github.com/WBEVAN/TESmartController/wiki/Windows).

## Set up the server

On the computer that talks to the HDMI switch, configure once, then start the listener:

```bash
tesmartctl install
tesmartctl quickstart
```

`install` asks whether this computer is the server or a client. It starts from a saved env file when one exists, and from the built-in defaults otherwise, and it does not write that file until you confirm. A server install stores the switch address, the HTTP listen address (`TESMART_HTTP`), the listen port (`TESMART_HTTP_PORT`), and the browser UI choice (`TESMART_HTTP_PANEL`). During that install, the connection test can select input 9 and then return to the input that was active. If input 9 comes up, the switch is saved as 16 inputs. Otherwise it is saved as 8. You can keep that count or enter your own. `quickstart` starts that listener. `/kit` is the floating UI application kit. `/panel` is the browser-only control panel. Both are on by default. If those saved values are missing, `quickstart` offers to run `install`.

A client install on this same computer saves its listener as `TESMART_CLIENT_VIA` and `TESMART_CLIENT_MDNS_NAME`. The server listener settings stay as they are. With no client connection saved, client commands use `TESMART_VIA` and `TESMART_MDNS_NAME`. See [`Docs/install.md`](Docs/install.md).

## Help and examples

Every command prints worked examples at the end of its `--help`:

```bash
tesmartctl --help              # command list plus the most common invocations
tesmartctl input --help        # flags and examples for one command
tesmartctl net set --help
tesmartctl examples            # every example for every command, in one listing
```

The same examples appear in this README. They are generated from one table (`tesmart/examples.py`), so the terminal and the docs agree.

## Quick start: change inputs

From the command line:

```bash
tesmartctl input 3          # switch to HDMI 3
tesmartctl input Office     # switch by a local name (see `tesmartctl name`)
tesmartctl next             # next input in the saved cycle
tesmartctl peek 2           # look at HDMI 2 for 5 seconds, then come back
tesmartctl input            # which input is active now
```

From a URL, after starting the HTTP listener once (for example as a login item or a service):

```bash
tesmartctl listen --http 0.0.0.0:8080      # add --panel for the browser control panel
# with pip install 'tesmartctl[mdns]', a non-loopback bind advertises on the LAN:
tesmartctl --via auto input 3              # find that listener, then switch
```

```text
http://<host>:8080/input/3                 switch to HDMI 3
http://<host>:8080/input/Office            switch by local name
http://<host>:8080/enable/4                allow input 4 again
http://<host>:8080/disable/4               ignore input 4 for selection, peek, and next/previous
http://<host>:8080/next                    next input in the saved cycle
http://<host>:8080/previous                previous input
http://<host>:8080/peek/2?seconds=8        look at HDMI 2 for 8 seconds, then come back
http://<host>:8080/input                   read the active input, {"active_input": 3}
http://<host>:8080/input?format=text       same, as a bare number
```

`<host>` is the machine running `tesmartctl`, not the switch. Use `127.0.0.1` with a port-only `--http 8080` when the controller runs on the same computer. Use `0.0.0.0:8080` to accept other devices on your network. The listener has no authentication, so only expose it to a network you trust. mDNS advertise/discover, limitations, and typed-URL fallbacks: [`Docs/mdns.md`](https://github.com/WBEVAN/TESmartController/blob/main/Docs/mdns.md).

### Stream Deck, macro pads, and home control

Each input change is one URL or one command, so anything that can open a URL or run a shell command can drive the switch:

| Controller | How to wire one button |
| --- | --- |
| Elgato Stream Deck | **Website** action with `http://127.0.0.1:8080/input/3` and "GET request in background" ticked, or a **System > Open** action running `tesmartctl input 3`. |
| Bitfocus Companion | **Generic HTTP**, GET `http://<host>:8080/input/3`. |
| Home Assistant | A `rest_command` (`url: http://<host>:8080/input/{{ input }}`) called from a button, script, or automation. A `rest` sensor on `/input?format=text` shows the active input. |
| Hubitat | [`hubitat/`](https://github.com/WBEVAN/TESmartController/tree/main/hubitat): **TESmart Manager** setup app (HPM or manual), parent `MediaInputSource` device, child switches per port. See [`Docs/hubitat.md`](https://github.com/WBEVAN/TESmartController/blob/main/Docs/hubitat.md). |
| Homebridge / HomeKit | An HTTP switch or button plugin pointed at `/input/N`, one accessory per input. |
| Node-RED | An **http request** node, GET `/input/3`. |
| Apple Shortcuts | **Get Contents of URL** with `http://<host>:8080/input/3`. |
| Keyboard Maestro, Hammerspoon, cron | Run `tesmartctl input 3`, or `curl -s http://127.0.0.1:8080/input/3`. |

Home Assistant example (`configuration.yaml`):

```yaml
rest_command:
  tesmart_input:
    url: "http://192.168.1.20:8080/input/{{ input }}"
  tesmart_peek:
    url: "http://192.168.1.20:8080/peek/{{ input }}?seconds={{ seconds | default(5) }}"

sensor:
  - platform: rest
    name: TESmart active input
    resource: "http://192.168.1.20:8080/input?format=text"
    scan_interval: 10
```

Here `192.168.1.20` stands for the computer running the listener. Every response says which input the switch actually reports (`active_input`), so a controller can show the real state rather than assuming the switch obeyed. See [Listener](#listener) for every route.

Hubitat uses the same listener. Install via Hubitat Package Manager ([`hubitat/repository.json`](https://raw.githubusercontent.com/WBEVAN/TESmartController/main/hubitat/repository.json)) or import the app and drivers from [`hubitat/`](https://github.com/WBEVAN/TESmartController/tree/main/hubitat). **TESmart Manager** walks through host/port, optional port names, and creates the controller; it polls `/status?network=0` and can register push callbacks (separate from browser SSE). The parent is a `MediaInputSource` (one active input of 8 or 16). Child switches give you one dashboard tile per port. Details: [`Docs/hubitat.md`](https://github.com/WBEVAN/TESmartController/blob/main/Docs/hubitat.md).

## Connection

The switch's factory address is `192.168.1.10`, TCP port `5000`. You can move it to another address on your network with `tesmartctl net set`. After that, save the new address as the CLI default so later commands do not need `--host`.

Each setting (`host`, `port`, `serial`, `inputs`) is resolved in this order:

1. Command-line flag (`--host`, `--port`, `--serial`, `--inputs`)
2. Env file `~/.config/tesmartctl/env` (override the path with `--env-file` or `TESMART_ENV_FILE`)
3. Environment variable, only when it is set (`TESMART_HOST`, `TESMART_PORT`, `TESMART_SERIAL`, `TESMART_INPUTS`)
4. Built-in default (`192.168.1.10`, `5000`, no serial device, `8` inputs)

```bash
tesmartctl config show                     # effective values and where each came from
tesmartctl config set host 192.168.1.50    # also: port, serial, inputs
tesmartctl config unset host
tesmartctl config path
```

Input names are local. The switch cannot store them. They are written to the same env file as `TESMART_NAME_1=Office` and appear in `status`, `input`, and `rotate`.

```bash
tesmartctl name set 2 Office
tesmartctl name set 1 "Living room"
tesmartctl name list
tesmartctl name unset 1
tesmartctl input Office          # select by the local name
```

An input is enabled unless you disable it. A disabled input is ignored for selection, peek, and next/previous. It can stay in a saved cycle; the step skips it. At least one input must stay enabled. The setting is stored with the listener, not in the switch. A command without `--via` writes this computer. `--via` writes the listener it finds. `GET /enable/4` and `GET /disable/4` are the same action. The panel names list has a checkbox for it. The floating kit shows a disabled input as not selectable. **Hide disabled inputs** leaves only the enabled inputs on the layout, in number order. **Display names** shows the local name on the button. Both are saved with the other kit preferences.

```bash
tesmartctl disable 4
tesmartctl disable Office
tesmartctl enable 4
tesmartctl --via auto enable 4
```

LCD banner messages need a running `tesmartctl listen --http` (add `--panel` for kit/panel). They are not sent to the switch; kits and the browser panel show them.

```bash
tesmartctl banner "Door unlocked"           # timed, 8 seconds
tesmartctl banner Meeting in 5 --seconds 12
tesmartctl banner "Stay on camera" --sticky # until dismissed
tesmartctl banner                           # last message
tesmartctl banner --history
tesmartctl banner --dismiss
tesmartctl banner --clear
```

The env file is plain `KEY=VALUE` lines (`TESMART_HOST=192.168.1.50`) and can be edited by hand. `--serial /dev/tty.usbserial-xxx` uses RS232 at 9600 8N1 instead of LAN. `--inputs 16` is for a 16×1. `--timeout 1.5` is how long a reply is awaited. `--json` prints JSON from the one-shot commands.

Global flags go before the subcommand: `tesmartctl --host 192.168.1.50 status`.

## What the switch will report

| Setting | Read | Write |
| --- | --- | --- |
| Active input (1–8, or 1–16) | yes | yes, and the switch reports the input it actually selected |
| LAN IP, TCP port, gateway, mask | yes | yes, applied only after a power cycle |
| Buzzer | no | yes |
| Front-panel LED timeout (never, 10s, 30s) | no | yes |
| Auto input detection | no | yes |

Buzzer, LED timeout, and auto-detect accept a set command and send nothing back. A successful command means the bytes were sent, not that the value was read back.

The switch drops or garbles commands sent faster than about once a second. `tesmartctl` waits that long between writes on one connection.

This is an 8-input, 1-output switch. The protocol can read the selected input and cannot read which inputs have a signal, so it cannot calculate which sources are connected. The front-panel LEDs show that locally. `rotate` therefore walks every input from 1 to 8, or an explicit list you pass with `--only`.

## Commands

### Read

```bash
tesmartctl status                 # active input plus saved LAN settings
tesmartctl status --json
tesmartctl status --no-network    # skip the four LAN queries
tesmartctl input                  # active input only
tesmartctl net show               # IP, port, gateway, mask
tesmartctl monitor                # print the input whenever it changes
```

`status` looks like:

```text
Endpoint:      tcp://192.168.1.50:5000
Active input:  2 (Office) of 8
Enabled:       1 (Living room), 2 (Office), 3, 5, 6, 7, 8
Disabled:      4
Input names (stored locally, not on the switch):
  1  Living room
  2  Office
Network (persisted; applied after power cycle):
  IP:          192.168.1.50
  Port:        5000
  Gateway:     192.168.1.1
  Mask:        255.255.255.0
Cycle:         manual (next/previous); 1, 2, 4 (chosen inputs)
Peek:          5s on another input, then back (`peek N`)
```

The cycle is stored on this machine. `manual` moves only when you ask for `next` or `previous`. `automatic` is the same list on a timer, and it starts only while `cycle run` is running. With no saved list the cycle is every input.

Peek is not a cycle. `peek 3` shows input 3 for a few seconds and then returns to the input that was active. The duration is stored on this machine.

`monitor` stays running and prints a new line when the front panel, the remote, auto-detect, or another client changes the input. Ctrl-C stops it.

### Change the input

```bash
tesmartctl input 3                # switch to HDMI 3 and read it back
tesmartctl input 3 --no-verify    # send the command and do not read it back
tesmartctl cycle inputs 1,2,4              # save the inputs to walk
tesmartctl cycle mode manual               # next and previous only
tesmartctl next                            # Office -> next chosen input
tesmartctl previous
tesmartctl cycle mode automatic --every 15 # then start the timer:
tesmartctl cycle run
tesmartctl --json input 3
```

Inputs are 1-based. The command exits non-zero when the switch reports a different input than the one requested. `input 9` is rejected locally and is not sent.

### Peek at another input

```bash
tesmartctl peek 3                 # show input 3 for 5 seconds, then come back
tesmartctl peek Office --seconds 10
tesmartctl peek 3 --seconds 8 --save   # and make 8 seconds the default
```

`peek` flips to the input, waits, and flips back to the one that was active. Ctrl-C comes back at once. If the input is changed by something else during the peek (front panel, remote, another client), `peek` leaves that choice alone, reports it, and exits non-zero. The default duration is `5` seconds, stored as `TESMART_PEEK_SECONDS` in the env file.

The listener can also make the *next* ordinary input change a peek, so a button that calls `/input/3` peeks instead of sticking. `GET /peek/once` arms a single change. `GET /peek/always` keeps doing it until `GET /peek/off`. Detail is in [`Docs/listener.md`](https://github.com/WBEVAN/TESmartController/blob/main/Docs/listener.md).

### Write-only settings

```bash
tesmartctl buzzer off             # on | off
tesmartctl led 10                 # never | 10 | 30   (seconds)
tesmartctl autodetect on          # on | off
```

Accepted words for on/off also include `1`/`0`, `true`/`false`, `enable`/`disable`, `unmute`/`mute`.

### LAN address

```bash
tesmartctl net set \
  --ip 192.168.1.50 --gateway 192.168.1.1 --mask 255.255.255.0 --yes
tesmartctl net show               # read the stored values back before rebooting
```

`--tcp-port` sets the switch's own listen port (factory `5000`). Each value is stored immediately and used only after the switch is power-cycled. There is no factory reset for these settings.

**Risk notice.** A wrong IP address, port, gateway, or mask makes the switch unreachable over the network, and recovery is only possible with a serial (RS232) cable. This software sends the values exactly as entered; it cannot check that they are valid for your network, cannot undo the change, and cannot recover a switch that is no longer reachable. You change these settings at your own risk. This software and its authors accept no liability for any loss, damage, downtime, or cost that results from doing so.

`net set` warns twice: without `--yes` it prints the notice and refuses; with `--yes` it prints the notice again, writes, then reads every value back from the switch and reports whether each one is `stored` or a `MISMATCH`. The exit code is `0` only when every value reads back as requested. Until the switch is power-cycled it keeps answering on its current address, so a mismatch can still be corrected.

`--save-default` writes the new `--ip` and `--tcp-port` into the CLI env file after the read-back confirms them, so later commands follow the switch to the new address.

### Raw bytes

```bash
tesmartctl raw AA BB 03 10 00 EE
# Sent:     AA BB 03 10 00 EE
# Received: AA BB 03 11 01 EE
#   frame cmd=0x11 value=0x01 term=0xEE -> input 2
```

`--listen 3` waits longer for the reply. See [`Docs/protocol.md`](https://github.com/WBEVAN/TESmartController/blob/main/Docs/protocol.md) for the frame layout. The input query value is 0-based in the reply (`0x01` is input 2).

## Listener

The usual way to run the server is `tesmartctl install`, then `tesmartctl quickstart`. `quickstart` is `listen` with the address, port, browser UI, and mDNS options that install saved. The commands below are that same listener written out, including the line protocol `quickstart` does not start. Full detail is in [`Docs/listener.md`](https://github.com/WBEVAN/TESmartController/blob/main/Docs/listener.md).

```bash
tesmartctl listen                         # one command per line on stdin
printf 'get\nset 3\nquit\n' | tesmartctl listen

tesmartctl listen --bind 9753             # raw TCP on this computer only, 127.0.0.1:9753
tesmartctl listen --bind 0.0.0.0:9753     # raw TCP on every interface
echo set 4 | nc 127.0.0.1 9753            # prints the input the switch reports

tesmartctl listen --http 8080             # HTTP on this computer only, http://127.0.0.1:8080/
tesmartctl listen --http 0.0.0.0:8080     # HTTP on every interface; other devices use this computer's address
tesmartctl listen --http 0.0.0.0:8080 --panel
tesmartctl listen --simulate --http 8080 --panel   # demo: no HDMI switch required
tesmartctl quickstart --simulate
```

`--simulate` keeps the active input in memory. Selection, peek, next, previous, and cycle behave as they do with hardware, and nothing is sent to a switch. Use it for a demo or when you are developing without the switch on the network.

`--bind` and `--http` cannot be combined. Run two processes if you want both. A port number alone binds `127.0.0.1`, so only this computer can connect. `0.0.0.0` accepts every network interface. Other devices then use the address of the computer running `tesmartctl`, for example `http://192.168.1.20:8080/input/3`. There is no authentication, so only do this on a network you trust.

`--panel` is off unless you add it. `tesmartctl install` turns it on by default. It serves the floating UI application kit at `/kit` and the browser-only control panel at `/panel` (buzzer, display timeout, auto-detect, LAN address, API explorer). Both UIs subscribe to `GET /api/events` (Server-Sent Events) so input, name, and banner changes appear immediately; a slower `/api/state` poll remains as a fallback. Detail — including how to open the kit from another machine — is in [`Docs/panel.md`](https://github.com/WBEVAN/TESmartController/blob/main/Docs/panel.md).

```bash
pip install 'tesmartctl[kit]'
tesmartctl listen --http 0.0.0.0:8080 --panel
tesmartctl kit --listen 8080                                    # frameless on this host
tesmartctl kit 'http://192.168.1.20:8080/kit?app=1'              # frameless here, remote listener (quote the URL)
```

![Floating kit](https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/kit.png)

The kit is the compact switcher: input tiles, LCD banners (including message history), clickable PEEK (Off → Once → Always), Cycle Inputs, and body/LCD/glass themes. On macOS, `tesmartctl kit` is frameless; `tesmartctl kit --fresh` ignores saved window prefs once. Guided wiki: [TESmartController/wiki](https://github.com/WBEVAN/TESmartController/wiki).

![Demo: input switching, Peek, and API explorer](Docs/images/demo.gif)

![Full control panel](https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/panel.png)

The **API** button on `/panel` opens an explorer that lists every `/api` route, sends it, and shows the raw status and body the listener returned.

![API explorer](https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/api-explorer.png)

Line commands: `get` (also `port`, `status`, `?`) prints the active input; `set 3`, `set Office`, or just `3` switches and prints the input the switch reports; `next` or `rotate 1,2,4` moves to the next input; `peek 3` or `peek 3 10` shows an input briefly and replies once back; `quit` closes the session. `--json` makes each reply one JSON object. The line command `status` is only the active input. The full readable status is the HTTP route and `tesmartctl status`.

A full session on stdin:

```text
$ tesmartctl listen
Command listener on stdin (Ctrl-C or 'quit' to stop).
  HDMI switch:  tcp://192.168.1.50:5000
commands: get | set <n> | <n> | next | previous | rotate [1,2,4] | peek <n> [seconds] | quit
get
2
set 3
3
Office
2
next
3
quit
```

HTTP routes, GET or POST:

```text
http://127.0.0.1:8080/                       list of routes
http://127.0.0.1:8080/input                  {"active_input": 2}
http://127.0.0.1:8080/status                 input plus saved IP, port, gateway, mask
http://127.0.0.1:8080/status?network=0       input only, skips the LAN queries
http://127.0.0.1:8080/names                  local input names (GET); POST JSON to set
http://127.0.0.1:8080/enable/4               allow input 4 (also /enable?input=4 or a local name)
http://127.0.0.1:8080/disable/Office         ignore that input for selection, peek, and next/previous
http://127.0.0.1:8080/banner                 last LCD message (GET); POST JSON to set / dismiss / clear
http://127.0.0.1:8080/events                 Server-Sent Events stream (kit/panel use /api/events)
http://127.0.0.1:8080/status?format=text     same fields as plain lines
http://127.0.0.1:8080/rotate                 next input; {"previous": 2, "active_input": 3}
http://127.0.0.1:8080/rotate?only=1,2,4     next input within that list
http://127.0.0.1:8080/input?rotate=1        same as /rotate
http://127.0.0.1:8080/peek/3                 show 3 for the saved seconds, then back; replies once back
http://127.0.0.1:8080/peek?input=3&seconds=8 same with an explicit duration
http://127.0.0.1:8080/peek/once              the next input change peeks, then later changes stick
http://127.0.0.1:8080/peek/always            every plain input change peeks until /peek/off
http://127.0.0.1:8080/peek/off               plain input changes stick again
http://127.0.0.1:8080/input?set=3            {"active_input": 3, "requested": 3}
http://127.0.0.1:8080/input/3                same as /input?set=3
http://127.0.0.1:8080/input/Office           switch by a local name
http://127.0.0.1:8080/set?input=3            same as /input?set=3
http://127.0.0.1:8080/get                    same as /input
http://127.0.0.1:8080/input/3?format=text    3
```

`active_input` is what the switch reports after the change. Compare it with `requested` when they might differ. `400` is a bad number or a switch rejection, `404` is an unknown route, `502` means the switch connection failed. `/status` takes a few seconds because each LAN query is paced.

## Library

```python
from tesmart import TcpTransport, TesmartSwitch, LedTimeout

with TesmartSwitch(TcpTransport("192.168.1.50")) as sw:
    print(sw.get_active_input())
    sw.set_input(4)
    sw.set_buzzer(False)
    sw.set_led_timeout(LedTimeout.SECONDS_30)
    sw.set_auto_detect(True)
    print(sw.read_status())
```

`SerialTransport("/dev/tty.usbserial-xxx")` is the RS232 equivalent and imports `pyserial`.

## Layout

| Path | Responsibility |
| --- | --- |
| `tesmart/protocol.py` | Hex and ASCII encode/decode, no I/O |
| `tesmart/transport.py` | TCP and serial transports |
| `tesmart/switch.py` | Device operations, receive buffer, command pacing |
| `tesmart/config.py` | Flag, env file, environment variable, default |
| `tesmart/peek.py` | Peek: flip to an input, wait, flip back |
| `tesmart/listener.py` | Stdin and raw-TCP command listener |
| `tesmart/web.py` | HTTP routes |
| `tesmart/panel.py` | `/panel` and its `/api` routes, only with `--panel` |
| `tesmart/events.py` | Server-Sent Events hub (`/events`, `/api/events`) for kit/panel |
| `tesmart/static/` | Panel HTML, CSS, and JS |
| `tesmart/cli.py` | `tesmartctl` argument parsing and output |
| `tesmart/examples.py` | Worked examples shown by `--help` and `tesmartctl examples` |
| `Docs/wiki/` | Source for the [GitHub Wiki](https://github.com/WBEVAN/TESmartController/wiki) |
| `Docs/protocol.md` | Wire protocol |
| `Docs/listener.md` | Line protocol and HTTP routes |
| `Docs/panel.md` | Floating kit and full control panel |
| `Docs/embed.md` | Input buttons on another page |
| `examples/buttons/` | Sample page of input links, no script or framework |
| `Docs/images/` | Screenshots used in the documentation |
| `LICENSE` | MIT License |

## License

[MIT](https://github.com/WBEVAN/TESmartController/blob/main/LICENSE). This software is provided as is, without warranty. Changing the switch's network address can make it unreachable over the LAN; that risk, and the absence of any liability for it, is spelled out in [LAN address](#lan-address) and in the panel before a write is accepted.
