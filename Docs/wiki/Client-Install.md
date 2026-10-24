# Client install

On a computer that talks to a listener already running somewhere else. This computer does not open the HDMI switch.

The server should already be up: [Quick start](Quickstart).

```bash
python3 -m pip install --upgrade tesmartctl
tesmartctl install
```

Choose **Client**. Then choose how this computer finds the listener.

## mDNS

Choose **mDNS / zeroconf** (`tesmartctl --via auto`). If `zeroconf` is missing, the wizard offers to install `tesmartctl[mdns]`.

It then scans the network and lists the listeners it finds, each with the address it would use:

```text
Scanning the network for tesmartctl listeners...
Servers on the network. Which one should this client use?
  1) OfficeHDMI  http://192.168.1.20:8080
  2) LivingRoom  http://10.0.0.4:9090
  3) Type a different mDNS name
```

Pick the server. Press Enter to accept the one in brackets (a saved name when it is in the list, otherwise the first server). The chosen name is saved as `TESMART_MDNS_NAME`. The address from the scan is saved as `TESMART_VIA`, and later commands use it when a browse does not answer.

When this is the only server on the network, clients may omit the mDNS name:

```bash
tesmartctl --via auto kit
tesmartctl --via auto status
```

With more than one server, keep the name:

```bash
tesmartctl --via auto --mdns-name OfficeHDMI kit
```

If the scan finds nothing, you can still save the name the server advertises and connect when that listener is running. You can also type a different name than the ones listed.

## Typed URL

Choose **A listener URL** when mDNS is blocked (guest Wi-Fi, AP isolation, a VLAN). Enter `http://host:port`. The wizard can GET `/version` before it saves.

```bash
tesmartctl --via http://192.168.1.20:8080 status
tesmartctl kit 'http://192.168.1.20:8080/kit?app=1'
```

## After install

The wizard can write `startup.txt` in the current directory for future reference. Confirming saves the client settings and clears server HTTP keys on this computer, so this machine does not try to start its own listener.

Optional: install the local UI kit (`pywebview`) so `tesmartctl kit` opens a frameless window here. Chrome or Edge still open the kit if that package is not installed. The server still has to be serving `/kit`.

Discovery limits: [mDNS](mDNS). The questionnaire in full: [Docs/install.md](https://github.com/WBEVAN/TESmartController/blob/main/Docs/install.md).
