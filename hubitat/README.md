# Hubitat Package (HPM)

Files follow the [Hubitat Package Manager developer documentation](https://hubitatpackagemanager.hubitatcommunity.com/devs1.html).

| File | Role |
| --- | --- |
| [`packageManifest.json`](packageManifest.json) | Package manifest (`packageName`, apps, drivers, `oauth`) |
| [`repository.json`](repository.json) | Repository listing **and** the same installable package (includes `packageName` so Install → From a URL never shows `null`) |
| [`TESmart-Manager.groovy`](TESmart-Manager.groovy) | Setup app (`primary`, OAuth for push callbacks) |
| [`TESmart-Controller.groovy`](TESmart-Controller.groovy) | Parent driver (`MediaInputSource`) |
| [`TESmart-Input.groovy`](TESmart-Input.groovy) | Child input driver |

Current package version is in `packageManifest.json` (also mirrored on `repository.json`). Full end-user setup, commands, and callbacks: [`Docs/hubitat.md`](../Docs/hubitat.md).

## Install with HPM

Either URL works for **Install → From a URL** and shows **TESmart Controller**:

```text
https://raw.githubusercontent.com/WBEVAN/TESmartController/main/hubitat/packageManifest.json
https://raw.githubusercontent.com/WBEVAN/TESmartController/main/hubitat/repository.json
```

For **Add custom repository**, use `repository.json`, refresh repositories, then browse/install.

If Uninstall already lists a package named **`null`**, uninstall that entry first (incomplete earlier install), then install again with one of the URLs above.

## Server requirement

This Hubitat package expects a recent **`tesmartctl listen --http`** on a host the hub can reach.

- Minimum server: **0.0.7.9+** (Manager checks `POST /compat` on connection test)
- Namespace: **`wbevan_tesmart`**
- Push notifications need OAuth on the Manager app (set via `"oauth": true` in the manifest)

Upgrade the host package when the connection test reports incompatible / missing `/compat`.

## Versioning

Package-level SemVer only (`version` on the manifest root), not mixed with per-app/driver versions — per the [HPM developer docs](https://hubitatpackagemanager.hubitatcommunity.com/devs1.html). Bump `packageManifest.json` and `repository.json` together when releasing Hubitat changes; keep Manager’s `MIN_SERVER_VERSION` / `PACKAGE_VERSION` aligned with that release.

## Categories and tags

From the [official settings list](https://raw.githubusercontent.com/HubitatCommunity/hubitat-packagerepositories/master/settings.json):

- Category: `Integrations`
- Tags: `LAN`, `Multimedia`, `HDMI`, `KVM`

## Publishing to the public HPM catalog

When ready for all HPM users, open a PR against [hubitat-packagerepositories](https://github.com/HubitatCommunity/hubitat-packagerepositories) pointing at this `repository.json`.
