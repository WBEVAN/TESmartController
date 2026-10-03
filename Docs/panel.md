# Control panel

`tesmartctl listen --http PORT --panel` serves a browser page at `/panel`. It is off unless `--panel` is given. Without the flag, `/panel`, `/static/...`, and `/api/...` answer `404` with a short explanation.

```bash
tesmartctl listen --http 8080 --panel
# open http://127.0.0.1:8080/panel
```

`--panel` on its own is an error: the page is part of the HTTP listener. `--bind` and `--http` are still mutually exclusive. A bare port binds `127.0.0.1`. There is no authentication, same as the rest of the listener.

The page is a replacement for the vendor Windows controller. It uses Bootstrap from a CDN and styles from `/static/panel.css`. Outcomes appear as toasts. Changing the switch's address opens a three-step dialog in the page; the browser's own alert and confirm dialogs are not used.

**API** in the top bar opens a dialog that lists every `/api` route (`GET /api` returns the same list as `catalog`). Picking a route fills the path and a sample body; **Send** shows the status code and the raw body. `POST /api/network` is listed but not sent from there: address changes stay on **Change address**.

## What the page controls

| Section | What it does | Where it is stored |
| --- | --- | --- |
| Inputs | One button per input. The active input is highlighted and named. | The switch |
| Previous / Next | Step through the saved cycle | The switch, using the local cycle list |
| Peek | With Peek on, an input button shows that input for the number of seconds beside it and then returns. The header shows a countdown and **Back now**. Changing the seconds saves the default. | This listener times it; the duration is in the env file |
| Auto cycle | Steps to the next cycle input on the saved interval while Auto cycle is switched on. Stops when the tab is hidden or Auto cycle is switched off. | This browser tab only |
| Buzzer, display timeout, auto input detection | The same three write-only settings as the vendor General page | Sent to the switch. The page highlights the last value *this listener* sent, because the switch cannot report them. |
| Input names | Local labels, saved when you leave the field. Empty clears a name. | The env file on this computer |
| Cycle | Which inputs Previous, Next, and Auto cycle walk, plus manual or automatic and the interval. Saved on every change. | The env file |
| Network | Shows the address stored in the switch. Query reads it again. Apply writes IP, port, gateway, or mask after two separate acknowledgements, then reads the values back. | The switch. Used only after a power cycle. |

The switch still cannot say which HDMI inputs have a signal. The cycle is the list you choose. Leaving every box clear means all inputs.

Names, cycle, and the peek duration have no Save button. They involve no switch traffic, so each change is written to the env file as it happens and a brief "Saved" appears in the card footer. Settings that *do* reach the switch (inputs, buzzer, display timeout, auto-detect, network) are sent only when you press their control.

Peek is not a cycle: one flip there, one flip back. Clicking another input while a peek is running moves the peek to that input and restarts the countdown; the return point stays the original input. If the input is changed by anything else during the peek (front panel, remote, another client), the peek does not snap back over that choice. The auto cycle pauses while a peek is running.

## Changing the address

This is the one action on the page that can take the switch out of reach, so it is deliberately slow:

1. **Change address…** opens the form under a standing warning.
2. **Apply…** opens the dialog at *Warning 1 of 2*: the values about to be written, the fact that a wrong value makes the switch unreachable with no factory reset and RS232 as the only recovery, and a checkbox that must be ticked before **Continue** is enabled.
3. *Warning 2 of 2* states that the change is made at the user's own risk, that the software sends the values exactly as entered and cannot validate, undo, or recover them, and that the software and its authors accept no liability for any loss, damage, downtime, or cost. A second checkbox must be ticked before **Write to switch** is enabled.
4. The listener writes the values and then **reads all four back from the switch**. The final step shows a table of requested versus reported values with `stored` or `mismatch` per row. Green means the switch holds the new value and will use it after the next power cycle. Red means it does not; nothing changes until a power cycle, so a mismatch can be corrected or left alone.

The API enforces the same two acknowledgements: `POST /api/network` is rejected unless the body has both `"confirm": true` and `"accept_risk": true`. The wording the dialog uses is available from `GET /api/network/notice` so other clients can show it.

## API

The page talks to JSON routes under `/api`. `GET /api` lists them. Errors are `{"error": "..."}`.

| Method and path | Body | Result |
| --- | --- | --- |
| `GET /api/state` | | Active input, names, cycle, LAN settings, and `last_sent` |
| `GET /api/state?network=0` | | Same, without the four LAN queries. The page polls this. |
| `POST /api/input` | `{"input": 3}` or `{"input": "Office"}` | `active_input` after the switch reports it |
| `POST /api/next`, `POST /api/previous` | | Next or previous input in the saved cycle |
| `POST /api/peek` | `{"input": 3, "seconds": 5}` | Switches now and returns at once. `/api/state` carries `peek.active` with `previous`, `peeked`, `seconds`, `remaining` until the listener has returned. |
| `POST /api/peek` | `{"cancel": true}` | Return to the previous input now |
| `POST /api/peek-default` | `{"seconds": 8}` | Save the default peek duration |
| `POST /api/buzzer` | `{"on": false}` | Sent. The switch does not confirm it. |
| `POST /api/led` | `{"timeout": "never"}`, `"10"`, or `"30"` | Sent. The switch does not confirm it. |
| `POST /api/autodetect` | `{"on": true}` | Sent. The switch does not confirm it. |
| `GET /api/network` | | IP, port, gateway, and mask stored in the switch |
| `GET /api/network/notice` | | Risk and liability wording as `risks`, `liability`, `after_write` |
| `POST /api/network` | `{"ip": "192.168.1.50", "confirm": true, "accept_risk": true}` | Writes, reads back, returns `requested`, `acknowledged`, `stored`, `verified`, `all_verified` |
| `POST /api/names` | `{"names": {"1": "Office", "2": ""}}` | Empty string removes that name |
| `POST /api/cycle` | `{"inputs": [1, 3, 5], "mode": "manual", "seconds": 15}` | An empty `inputs` list means every input |

`POST /api/network` may include any of `ip`, `port`, `gateway`, and `mask`. At least one is required, and both `confirm` and `accept_risk` must be true. `stored` is the full set of four values the switch reports after the write; `verified` compares each requested key against it. `all_verified` is the only result that should be treated as "stored, safe to power-cycle".

The plain listener route `/peek/N` blocks until the switch is back and returns the full result. `/api/peek` is the non-blocking form the page uses so it can show the countdown. Both share one timer per listener; starting a second peek retargets the running one.

These routes exist only so the page can drive the device. The plain listener URLs (`/input`, `/status`, `/rotate`, and the rest in [`listener.md`](listener.md)) are unchanged and still do not mute the buzzer or move the switch's address.

## Files

| Path | Responsibility |
| --- | --- |
| `tesmart/panel.py` | `/panel`, `/static`, and `/api` |
| `tesmart/static/panel.html` | Page structure |
| `tesmart/static/panel.css` | Layout the Bootstrap stylesheet does not cover |
| `tesmart/static/panel.js` | `Api`, `Toaster`, `NetworkWizard`, and `Panel` |
| `tesmart/notices.py` | The risk and liability wording, shared by the CLI and the API |
