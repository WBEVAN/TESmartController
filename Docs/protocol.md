# TESmart HDMI Switch Control Protocol

Applies to the TESmart 8x1 (HSW0801 family: HSW0801A10 / A1U, HKS0801A30 / A40 / A1U, HKE0802A10) and 16x1 (HSW1601 / HKS1601) HDMI switches and KVMs. The 16x1 uses the same frames with input values up to 16.

Sources: TESmart "8x1 HDMI Switch (KVM) Communication Protocol v2" and the HSW0801 user manual (section 10.6), plus community packet captures for the undocumented ASCII network protocol.

## Physical links

| Link  | Settings                                                      |
| ----- | ------------------------------------------------------------- |
| RS232 | 9600 baud, 8 data bits, 1 stop bit, no parity, 3-pin connector |
| LAN   | TCP, default `192.168.1.10:5000`, gateway `192.168.1.1`, mask `255.255.255.0` |

The LAN port is a serial-to-Ethernet bridge (CH9120/CH9121-class). Whatever bytes you send over TCP are forwarded to the switch's UART unchanged, so both links speak the identical byte protocol.

## Hex protocol (documented)

Every frame is six bytes:

```
AA BB 03 <cmd> <value> EE
```

| cmd    | Direction      | value                                   | Meaning                        |
| ------ | -------------- | --------------------------------------- | ------------------------------ |
| `0x01` | host -> switch | `0x01`..`0x08` (`0x10` on 16x1), 1-based | Switch to input                |
| `0x02` | host -> switch | `0x00` mute, `0x01` unmute               | Buzzer                         |
| `0x03` | host -> switch | `0x00` never, `0x0A` 10 s, `0x1E` 30 s   | Front-panel LED timeout        |
| `0x81` | host -> switch | `0x00` off, `0x01` on                    | Auto input detection           |
| `0x10` | host -> switch | `0x00`                                   | Query active input             |
| `0x11` | switch -> host | `0x00`..`0x07`, **0-based**              | Active input report            |

Important asymmetry: the *set input* command is 1-based (`0x01` = PC1) but the *report* is 0-based (`0x00` = PC1).

### Readable vs write-only

The only state the hex protocol can read back is the active input (`0x10` -> `0x11`). Buzzer, LED timeout, and auto-detect have no query command; the switch will accept the set commands but there is no way to confirm their current value remotely.

Input names are not part of the protocol. `tesmartctl name` stores them in the local env file and adds them to status output. The switch never sees the label.

There is also no command that reports which inputs have a signal. Auto-detect (`0x81`) can be turned on or off, and the switch will move to a live input on its own, but it never sends a bitmap of connected inputs. `tesmartctl rotate` therefore cycles `1..N` (or an `--only` list the user supplies). It does not discover connected sources.

### Unsolicited reports

The switch emits an `0x11` report on its own whenever the active input changes (front-panel button, IR remote, auto-detect, or a command from another client). A client must be prepared to receive these at any time, including in the middle of waiting for a different reply.

### Terminator byte

The documentation says frames end in `0xEE`, but `0x16` and `0x1C` have been observed in the field on `0x11` reports. Parsers should key on the `AA BB 03` header and frame length, not the terminator.

### Timing

The vendor's controller and community scripts agree the switch can drop or garble commands sent faster than roughly once per second. `tesmartctl` enforces a 1 s minimum interval between writes.

## ASCII protocol (undocumented)

The same socket also accepts plain ASCII commands used by TESmart's Windows controller to read and write the LAN settings. No `AA BB 03` preamble is used.

| Command            | Reply                      | Meaning                      |
| ------------------ | -------------------------- | ---------------------------- |
| `IP?`              | `IP:192.168.001.010;`      | Read IP address              |
| `PT?`              | `PT:05000;`                | Read TCP port                |
| `GW?`              | `GW:192.168.001.001;`      | Read gateway                 |
| `MA?`              | `MA:255.255.255.000;`      | Read subnet mask             |
| `IP:<addr>;`       | `OK`                       | Set IP address               |
| `PT:<port>;`       | `OK`                       | Set TCP port                 |
| `GW:<addr>;`       | `OK`                       | Set gateway                  |
| `MA:<mask>;`       | `OK`                       | Set subnet mask              |

Notes:

- Replies zero-pad octets and the port (`010.000.002.012`, `05000`).
- The bridge's buffer is small (about 48 bytes); the trailing `;` of a reply frequently arrives in a separate TCP segment. Read until `;` is seen.
- Written values are persisted immediately but only take effect after a power cycle.
- There is no factory-reset button for the network settings. If a wrong IP is written and the device is rebooted, recovery requires the RS232 port. `tesmartctl net set` therefore refuses to run without `--yes`.

## What tesmartctl exposes

| Protocol operation | CLI | Line listener | HTTP |
| --- | --- | --- | --- |
| Query / set active input | `input`, `status`, `rotate`, `peek` | `get`, `set N`, `next`, `peek N` | `/input`, `/input?set=N`, `/rotate`, `/peek/N`, `/status` |
| Read LAN settings | `status`, `net show` | — | `/status` |
| Write LAN settings | `net set --yes` (warns twice, reads back) | — | `POST /api/network` only with `--panel`, and only with both `"confirm": true` and `"accept_risk": true`; reads back |
| Buzzer, LED timeout, auto-detect | `buzzer`, `led`, `autodetect` | — | `/api/buzzer`, `/api/led`, `/api/autodetect` only with `--panel` |
| Arbitrary frame | `raw` | — | — |

The line listener's `status` word is an alias of `get` and returns only the active input. HTTP `GET /status` and `tesmartctl status` return the input plus the saved LAN settings. The plain HTTP routes leave write-only settings and LAN changes off, so a bookmarked URL cannot mute the buzzer or move the switch. Those controls are on the optional page at `/panel`, documented in [`panel.md`](panel.md). The listener protocol itself is documented in [`listener.md`](listener.md).

## Reference implementation

- `tesmart/protocol.py` – frame encode/decode and ASCII helpers. No I/O.
- `tesmart/transport.py` – TCP (`192.168.1.10:5000` by default) and RS232 (9600 8N1, requires `pyserial`).
- `tesmart/switch.py` – device operations, receive buffer, unsolicited `0x11` reports, and the one-second gap between commands.
- `tesmart/config.py` – resolution order: flag, env file, environment variable, built-in default.
- `tesmart/listener.py` – stdin and raw-TCP line protocol.
- `tesmart/peek.py` – peek: set input, wait on the host, set it back. Built from the two input commands; the switch has no timer.
- `tesmart/web.py` – HTTP routes.
- `tesmart/panel.py` – optional `/panel` page and `/api` routes.
- `tesmart/cli.py` – `tesmartctl` argument parsing and text/JSON output.
