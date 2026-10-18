# tesmartctl wiki

Control TESmart 8- and 16-port HDMI / KVM switches over LAN or RS232 — CLI, HTTP listener, floating kit, full panel, and Hubitat.

**Independent community project.** Not affiliated with or endorsed by TESmart.

## Start here

1. [Setup](Setup) — install, reach the switch, save defaults, run the listener
2. [Floating kit](Floating-Kit) — the HDMI switcher UI (`/kit`, `tesmartctl kit`)
3. [Hubitat](Hubitat) — Manager app, controller, child inputs, push callbacks
4. [Control panel](Control-Panel) — full Bootstrap `/panel` and API explorer
5. [Listener & HTTP](Listener-and-HTTP) — URLs, SSE vs Hubitat callbacks, Stream Deck / Companion
6. [CLI reference](CLI-Reference) — everyday commands (input, peek, cycle, banner, names)
7. [Troubleshooting](Troubleshooting) — offline kit, reconnect, banners, `--fresh`

## Also in the repo

| Doc | Role |
| --- | --- |
| [README](https://github.com/WBEVAN/TESmartController#readme) | Install + command overview |
| [Docs/panel.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/panel.md) | Kit + panel detail |
| [Docs/listener.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/listener.md) | Line protocol + HTTP routes |
| [Docs/hubitat.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/hubitat.md) | Hubitat package detail |
| [Docs/protocol.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/protocol.md) | Wire protocol |
| [Docs/embed.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/embed.md) | Embed input buttons on your page |

## Publish this outline to GitHub Wiki

The pages in `Docs/wiki/` are the draft wiki. To publish:

```bash
# once: clone the wiki repo (repo must have Wikis enabled)
git clone https://github.com/WBEVAN/TESmartController.wiki.git
cp Docs/wiki/*.md TESmartController.wiki/
cd TESmartController.wiki
git add .
git commit -m "Initial wiki: setup, kit, Hubitat, panel"
git push
```

GitHub renders `Home.md` as the wiki home and `_Sidebar.md` as the sidebar.
