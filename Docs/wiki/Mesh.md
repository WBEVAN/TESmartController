# Mesh (experimental)

Run more than one `tesmartctl` listener against the **same** TCP HDMI switch: one **proxy** owns the session; others join as **mesh clients**, share config, and relay commands. Use it for host moves and for failover when the proxy stops cleanly **or** dies abruptly (process crash or the machine goes away).

**Off by default** — listeners join only with explicit `--mesh` (`listen` or `quickstart`). TCP only. Hubitat callbacks are sent by the current proxy, so **every** mesh host must be able to reach Hubitat. In TESmart Manager (package **1.8.0+**), turn on mDNS Discover, pick the `…-mesh` failover name, and optionally **Follow listener endpoint from callbacks** after promote.

Full guide (including connection diagrams): [Docs/mesh.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/mesh.md).

```bash
tesmartctl listen --http 0.0.0.0:7272 --panel --mdns-name OfficeHDMI2 --mesh
tesmartctl mesh status
tesmartctl --via auto kit --mdns-name OfficeHDMI2-mesh
```
