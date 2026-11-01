# Look into later

Deferred ideas — not scheduled, just so they are not lost.

## Shorten mDNS TTLs

tesmartctl currently uses python-zeroconf defaults (`host_ttl` 120s, `other_ttl` 4500s / 75 min for SRV/TXT). After a hard kill, stale `_wbevan-tesmart` names linger until TTL expiry (Hubitat’s cache can hold them longer still; Discover already HTTP-probes).

**Idea:** set shorter TTLs when registering the instance and mesh alias (e.g. host 60–120s, service 120–300s) so dead listeners drop from LAN browse faster.

**Tradeoff:** more multicast refresh traffic; brief network blips can hide a live service until the next announce. Clean shutdown already unregisters.

**Where:** `tesmart/mdns.py` (`ServiceInfo` / `advertise` / `set_alias`).
