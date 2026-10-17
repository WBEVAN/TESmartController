# Hubitat integration

Guided setup app plus drivers for Hubitat Elevation. They talk to `tesmartctl listen --http` over REST.

| File | Role |
| --- | --- |
| [`TESmart-Manager.groovy`](TESmart-Manager.groovy) | **Setup app** — walk through host/port, name ports, create the device |
| [`TESmart-Controller.groovy`](TESmart-Controller.groovy) | Parent device — `MediaInputSource`, refresh poll, peek, next/previous |
| [`TESmart-Input.groovy`](TESmart-Input.groovy) | Child switch — one per input (radio group for dashboards) |
| [`packageManifest.json`](packageManifest.json) | [Hubitat Package Manager](https://hubitatpackagemanager.hubitatcommunity.com/) manifest |
| [`repository.json`](repository.json) | HPM repository entry |

Images used in the app (from this repo):

- [Banner](https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/readme-banner.png)
- [Buttons](https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/buttons.png)
- [Demo GIF](https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/demo.gif)

Full install and usage: [`Docs/hubitat.md`](../Docs/hubitat.md).
