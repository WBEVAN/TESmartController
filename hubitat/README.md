# Hubitat Package (HPM)

Files follow the [Hubitat Package Manager developer documentation](https://hubitatpackagemanager.hubitatcommunity.com/devs1.html).

| File | Role |
| --- | --- |
| [`packageManifest.json`](packageManifest.json) | **Package manifest** — apps/drivers in this package |
| [`repository.json`](repository.json) | **Repository file** — lists packages (add this as a custom repository) |
| [`TESmart-Manager.groovy`](TESmart-Manager.groovy) | Setup app (`primary`) |
| [`TESmart-Controller.groovy`](TESmart-Controller.groovy) | Parent driver |
| [`TESmart-Input.groovy`](TESmart-Input.groovy) | Child input driver |

## Install with HPM

**From a URL** (package manifest — this is what shows the package name):

```text
https://raw.githubusercontent.com/WBEVAN/TESmartController/main/hubitat/packageManifest.json
```

**Custom repository** (then browse / search):

```text
https://raw.githubusercontent.com/WBEVAN/TESmartController/main/hubitat/repository.json
```

Do not paste `repository.json` into Install → From a URL. That file has no `packageName`, so HPM shows **null**.

## Versioning

Package-level SemVer only (`version` on the manifest root), not mixed with per-app/driver versions — per the [HPM developer docs](https://hubitatpackagemanager.hubitatcommunity.com/devs1.html).

## Categories and tags

From the [official settings list](https://raw.githubusercontent.com/HubitatCommunity/hubitat-packagerepositories/master/settings.json):

- Category: `Integrations`
- Tags: `LAN`, `Multimedia`

## Publishing to the public HPM catalog

When ready for all HPM users (not only a custom repo), open a PR against:

https://github.com/HubitatCommunity/hubitat-packagerepositories

adding an entry that points at this `repository.json`.

Images used by the Manager app:

- [Banner](https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/readme-banner.png)
- [Buttons](https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/buttons.png)
- [Demo GIF](https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/demo.gif)

Full setup: [`Docs/hubitat.md`](../Docs/hubitat.md).
