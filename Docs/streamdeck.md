# Elgato Stream Deck

A native Stream Deck plugin is coming soon. The public page, with each key’s property inspector, is [Elgato Stream Deck](https://wbevan.github.io/TESmartController/streamdeck.html).

![Stream Deck plugin preview](../streamdeck/StreamDeckPlugin.gif)

The action list group is **TESmart Controller**. Every key talks to a running `tesmartctl listen` HTTP listener.

## Connection

Connection mode and the server URL are shared by every key. The mDNS name is saved on the key being edited.

| Setting | Required when | Notes |
| --- | --- | --- |
| Connection | Always | **Auto** (default) or **Direct HTTP**. Changing it changes every key. |
| mDNS name | Connection is Auto | Chooses this key’s listener. Required when more than one listener is advertised. Refresh searches again. |
| Server URL | Connection is Direct HTTP | The computer running tesmartctl, such as `http://127.0.0.1:8080`. Not the switch address. Changing it changes every key. |

Auto tries the last URL this key reached, `_wbevan-tesmart._tcp` advertisements, `TESMART_VIA` from the tesmartctl env file, then `http://127.0.0.1:8080`.

## Buttons

| Button | Stream Deck action | Press | Settings besides connection |
| --- | --- | --- | --- |
| Input | Input | Select one HDMI input. The key stays lit while it is live. | **Input.** Menu of listener names. Default is input 1. A disabled input can still be assigned and draws a red frame and a red HDMI connector. |
| Peek | Peek Input | Show one input, then return. The key stays lit until the peek ends. | **Input.** **Seconds,** 1–30, default 5. |
| Next | Next Input | Next input in the listener’s saved cycle. | None. |
| Previous | Previous Input | Previous input in the listener’s saved cycle. | None. |
| Cycle | Cycle Input | Start or stop stepping through the inputs checked on this key. | **Inputs,** at least two. **Seconds,** 2–60, default 10. This list is separate from the saved cycle. |
| Banner | Banner | Show the listener message. Press dismisses it. | **Dismiss:** This Stream Deck, or TESmart Controller for every device. **Sound:** None, or Play a file. **Sound file** when a file is selected. |
| Peek Settings Toggle | Peek Mode | Off, then Once, then Always, then Off. Does not move the switch. | **Seconds,** 1–30, default 5. Used by Once and Always. The mode is one setting for the whole listener. |

Until that plugin is published, a Stream Deck Website action can call the listener. See [Stream Deck, macro pads, and home control](../README.md#stream-deck-macro-pads-and-home-control) in the README.
