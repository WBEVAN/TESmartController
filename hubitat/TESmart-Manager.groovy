/**
 *  TESmart Manager
 *
 *  Guided Hubitat app to connect a TESmart HDMI/KVM switch through the
 *  tesmartctl REST server. Creates the TESmart Controller device, optional
 *  child input switches, and local input-name overrides.
 *
 *  Copyright 2026 — independent community project; not affiliated with TESmart.
 *
 *  Licensed under the MIT License. See the repository LICENSE file.
 */

import groovy.json.JsonBuilder
import groovy.json.JsonSlurper
import groovy.transform.Field

@Field static final String IMG_BANNER = "https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/readme-banner.png"
@Field static final String DOCS_URL = "https://wbevan.github.io/TESmartController/"
@Field static final String LISTENER_DOCS = "https://github.com/WBEVAN/TESmartController/blob/main/Docs/listener.md"
/** Hubitat package version (keep in sync with packageManifest.json). */
@Field static final String PACKAGE_VERSION = "1.8.0"
/** Oldest tesmartctl this package is tested against (must expose /compat + listener callbacks). */
@Field static final String MIN_SERVER_VERSION = "0.0.7.20"
/** DNS-SD type for NetworkUtils.getMDNSServiceResults (2.5.1+ one-shot browse). */
@Field static final String MDNS_SERVICE_TYPE = "_wbevan-tesmart._tcp.local."
/**
 * Same type for registerMDNSListener / getMDNSEntries (hub background cache).
 * Community examples omit the trailing {@code .local.} —
 * see https://community.hubitat.com/t/mdns-not-working-properly/161810/8
 */
@Field static final String MDNS_LISTENER_TYPE = "_wbevan-tesmart._tcp"
@Field static final List REQUIRED_SERVER_FEATURES = [
    "compat", "callbacks", "callbacks.name", "callbacks.health", "callbacks.listener",
    "names_changed", "last_sent", "banner", "enabled_changed", "enable", "shutdown", "online"
]

definition(
    name: "TESmart Manager",
    namespace: "wbevan_tesmart",
    author: "WBEVAN",
    description: "Set up and manage a TESmart HDMI/KVM switch via tesmartctl REST",
    category: "Convenience",
    iconUrl: "https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/readme-banner.png",
    iconX2Url: "https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/readme-banner.png",
    iconX3Url: "https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/readme-banner.png",
    importUrl: "https://raw.githubusercontent.com/WBEVAN/TESmartController/main/hubitat/TESmart-Manager.groovy",
    singleInstance: false,
    installOnOpen: true,
    oauth: true
)

mappings {
    path("/tesmart-event") {
        action: [
            POST: "handleTesmartEvent"
        ]
    }
}

preferences {
    page name: "mainPage", title: "TESmart Manager", install: true, uninstall: true
    page name: "connectionPage"
    page name: "namesPage"
}

def mainPage() {
    runPendingListenerAction()
    dynamicPage(name: "mainPage", title: "TESmart Manager", install: true, uninstall: true) {
        section {
            paragraph imgHtml(IMG_BANNER, "tesmartctl")
            paragraph "<b>Control a TESmart 8-port or 16-port HDMI/KVM switch</b> from Hubitat through the tesmartctl REST server."
            paragraph "Exactly one input is active at a time. This app creates a <b>TESmart Controller</b> device (MediaInputSource) and optional child switches for your dashboard."
            paragraph "Hubitat package <b>${PACKAGE_VERSION}</b> · requires <b>tesmartctl ${MIN_SERVER_VERSION}+</b>"
        }
        section("1. Prerequisites") {
            paragraph """Before continuing, on a machine Hubitat can reach run:
<pre style="white-space:pre-wrap">tesmartctl listen --http 0.0.0.0:8080</pre>
Bind to <code>0.0.0.0</code> (or your LAN IP). There is no authentication — use a trusted network.
Requires <b>tesmartctl ${MIN_SERVER_VERSION}+</b>.
<a href="${LISTENER_DOCS}" target="_blank">HTTP server docs on GitHub</a>"""
        }
        section("2. Connection") {
            String connTitle = "Configure server connection"
            if (state.connectionOk) {
                String ver = atomicState.lastServerVersion ? "tesmartctl ${atomicState.lastServerVersion}" : "tested"
                connTitle = "Connection — OK (Hubitat ${PACKAGE_VERSION} · ${ver}${atomicState.lastActive != null ? ", input ${atomicState.lastActive}" : ""})"
            } else if (atomicState.compatOk == false) {
                connTitle = "Connection — server version too old"
            }
            href(
                name: "toConnection",
                title: connTitle,
                description: "Hubitat package ${PACKAGE_VERSION} · mDNS discover or host/port · requires tesmartctl ${MIN_SERVER_VERSION}+",
                page: "connectionPage"
            )
            paragraph listenerStatusLine()
            input name: "btnStatus", type: "button", title: "Status"
            if (atomicState.lastStatusMessage) {
                paragraph atomicState.lastStatusMessage
            }
        }
        section("3. Port names") {
            href(
                name: "toNames",
                title: "Edit port names",
                description: "Stored on the tesmartctl server. Load from /status, edit here, save with Done to write back via POST /names.",
                page: "namesPage"
            )
        }
        section("4. Enable or disable an input") {
            paragraph "These lists are the listener’s enablement. They update from <code>GET /status</code> and from the <code>enabled_changed</code> callback."
            paragraph "<b>Enabled:</b> ${formatInputList(state.enabledInputs)}<br/><b>Disabled:</b> ${formatInputList(state.disabledInputs)}"
            input name: "mgrInput", type: "enum", title: "Input",
                options: inputChoiceOptions(), submitOnChange: true
            input name: "btnEnableInput", type: "button", title: "Enable"
            input name: "btnDisableInput", type: "button", title: "Disable"
            if (atomicState.enablementMessage) {
                paragraph atomicState.enablementMessage
            }
        }
        section("5. Create / update device") {
            input name: "deviceLabel", type: "text", title: "Device label",
                required: true, defaultValue: app.label ?: "TESmart Switch"
            input name: "createChildSwitches", type: "bool", title: "Create child switch per input",
                defaultValue: true
            input name: "doCreateDevice", type: "bool", title: "Create or update the TESmart Controller device now",
                defaultValue: true
            if (getControllerDevice()) {
                paragraph "Current device: <b>${getControllerDevice().displayName}</b> (${getControllerDevice().deviceNetworkId})"
            }
        }
        section("6. Push notifications") {
            input name: "enableCallbacks", type: "bool",
                title: "Register this hub for input, name, and enablement callbacks from the server",
                description: "tesmartctl POSTs input changes, name changes, and enabled_changed to this hub",
                defaultValue: true
            if (atomicState.callbackUrl) {
                paragraph "Callback URL registered with the server:<br/><code style='word-break:break-all'>${atomicState.callbackUrl}</code>"
            }
            if (atomicState.callbackMessage) {
                paragraph atomicState.callbackMessage
            }
        }
        section("Logging & docs") {
            input name: "logEnable", type: "bool", title: "Enable debug logging", defaultValue: false
            paragraph "<a href=\"${DOCS_URL}\" target=\"_blank\">tesmartctl docs</a> · <a href=\"https://github.com/WBEVAN/TESmartController\" target=\"_blank\">GitHub repo</a>"
        }
    }
}

def connectionPage() {
    // Button handlers run before Hubitat commits edited inputs; run the test
    // here so listenerHost/port from this same submit are already saved.
    runPendingListenerAction()

    boolean mdnsOn = mdnsUseEnabled()
    boolean useMesh = mdnsUseMeshEnabled()
    String activeEndpoint = listenerBaseUrl() ?: "(not set yet)"
    Map parted = mdnsHitsPartitioned()
    Map meshOpts = mdnsOptionsFromHits(parted.meshes as List)
    Map instanceOpts = mdnsOptionsFromHits(parted.instances as List)
    boolean discovered = atomicState.mdnsHitsJson != null && atomicState.mdnsHitsJson != ""
    Integer meshCount = (parted.meshes as List)?.size() ?: 0
    Integer instanceCount = (parted.instances as List)?.size() ?: 0

    dynamicPage(name: "connectionPage", title: "Server connection", nextPage: "mainPage") {
        section("1. How should Hubitat find the server?") {
            paragraph "Pick <b>one</b> path. mDNS uses names on the LAN; otherwise type a fixed IP."
            // Default off so existing installs keep host/port-only until the user opts in.
            input name: "useMdns", type: "bool",
                title: "Use mDNS discovery",
                description: "On = discover by name (no IP typing). Off = enter IP and port yourself. Default off for existing installs",
                defaultValue: false, submitOnChange: true
        }

        if (mdnsOn) {
            section("2. Discover listeners") {
                paragraph "Tap <b>Discover</b>, then continue below. (Hubitat <b>2.5.1+</b>, service <code>${MDNS_SERVICE_TYPE}</code>.)"
                input name: "btnDiscoverMdns", type: "button", title: "Discover listeners"
                if (atomicState.mdnsMessage) {
                    paragraph atomicState.mdnsMessage
                }
                if (discovered) {
                    if (meshCount > 0) {
                        List names = (parted.meshes as List).collect { (it as Map).name }.unique()
                        paragraph "<span style='color:green'><b>Mesh found:</b></span> ${names.join(', ')}"
                    } else {
                        paragraph "<span style='color:#a60'><b>No mesh failover name</b></span> on this Discover. You can still use a specific instance name below."
                    }
                    paragraph "Specific instance names found: <b>${instanceCount}</b>."
                } else {
                    paragraph "Not discovered yet."
                }
                String peersLine = meshPeersLine()
                if (peersLine) {
                    paragraph peersLine
                }
            }

            section("3. Choose what to connect to") {
                input name: "useMdnsMesh", type: "bool",
                    title: "Use mesh failover name (…-mesh)",
                    description: "On = pick the shared mesh name (follows the current proxy). Off = pick one specific mDNS instance",
                    defaultValue: false, submitOnChange: true
                if (useMesh) {
                    if (meshOpts) {
                        input name: "discoveredMeshListener", type: "enum", title: "Mesh failover name",
                            options: meshOpts, submitOnChange: true
                        input name: "btnApplyDiscovered", type: "button", title: "Use selected mesh"
                    } else if (discovered) {
                        paragraph "No <code>…-mesh</code> name in the last Discover. Turn this off to pick a specific name, or Discover again."
                    } else {
                        paragraph "Discover first (step 2)."
                    }
                    input name: "followListenerEndpoint", type: "bool",
                        title: "Follow listener endpoint from callbacks",
                        description: "Update host/port when the mesh proxy moves",
                        defaultValue: true
                } else {
                    if (instanceOpts) {
                        input name: "discoveredInstanceListener", type: "enum", title: "Specific mDNS name",
                            options: instanceOpts, submitOnChange: true
                        input name: "btnApplyDiscovered", type: "button", title: "Use selected name"
                    } else if (discovered) {
                        paragraph "No instance names in the last Discover. Try Discover again, or turn on mesh if a failover name appeared."
                    } else {
                        paragraph "Discover first (step 2)."
                    }
                }
            }

            section("4. Selected endpoint") {
                paragraph "Filled only by <b>Use selected mesh/name</b> above — not typed by hand while mDNS is on."
                if (atomicState.lastMdnsName || atomicState.lastMeshMdnsName) {
                    paragraph meshIdentityLine()
                }
                paragraph "<b>Active:</b> <code>${activeEndpoint}</code>"
                paragraph "Need a fixed IP instead? Turn <b>Use mDNS discovery</b> off in step 1."
            }
        } else {
            section("2. Enter IP and port") {
                paragraph "mDNS is off. Type the LAN address of the machine running <code>tesmartctl listen --http</code>."
                input name: "listenerHost", type: "text", title: "Server host / IP",
                    required: true, defaultValue: "192.168.1.20", submitOnChange: true
                input name: "listenerPort", type: "text", title: "Server port",
                    description: "e.g. 7272 (plain digits, no commas)",
                    required: true, defaultValue: "8080", submitOnChange: true
                input name: "useHttps", type: "bool", title: "Use HTTPS", defaultValue: false, submitOnChange: true
                paragraph "<b>Active:</b> <code>${activeEndpoint}</code>"
            }
        }

        section(mdnsOn ? "5. Options" : "3. Options") {
            input name: "prefInputCount", type: "enum", title: "Port count",
                options: ["8": "8 ports", "16": "16 ports"], defaultValue: "8"
            input name: "refreshInterval", type: "enum", title: "Status refresh interval",
                options: [
                    "0": "Manual only",
                    "30": "Every 30 seconds",
                    "60": "Every 1 minute",
                    "300": "Every 5 minutes",
                    "600": "Every 10 minutes"
                ],
                defaultValue: "60"
        }

        section(mdnsOn ? "6. Test" : "4. Test") {
            paragraph "Package <b>${PACKAGE_VERSION}</b> · needs <b>tesmartctl ${MIN_SERVER_VERSION}+</b>."
            input name: "runTest", type: "bool", title: "Test connection when saving the app (Done)",
                defaultValue: true
            input name: "btnTestConnection", type: "button", title: "Run connection test now"
            if (atomicState.lastTestMessage) {
                paragraph atomicState.lastTestMessage
            } else if (mdnsOn) {
                paragraph "Finish steps 2–3 so <b>Active</b> shows an address, then test."
            } else {
                paragraph "Set host/port, then test."
            }
        }
    }
}

def appButtonHandler(String btn) {
    // Defer work until the page re-renders — by then Hubitat has saved the form fields.
    if (btn == "btnTestConnection") {
        atomicState.pendingListenerAction = "test"
    } else if (btn == "btnRefreshNames") {
        atomicState.pendingListenerAction = "refreshNames"
    } else if (btn == "btnEnableInput") {
        atomicState.pendingListenerAction = "enable"
    } else if (btn == "btnDisableInput") {
        atomicState.pendingListenerAction = "disable"
    } else if (btn == "btnStatus") {
        atomicState.pendingListenerAction = "status"
    } else if (btn == "btnDiscoverMdns") {
        atomicState.pendingListenerAction = "discover"
    } else if (btn == "btnApplyDiscovered") {
        atomicState.pendingListenerAction = "applyDiscovered"
    }
}

def namesPage() {
    runPendingListenerAction()

    Integer count = (atomicState.lastInputCount ?: prefInputCount ?: "8") as Integer
    Map listenerNames = currentListenerNames()

    dynamicPage(name: "namesPage", title: "Port names", nextPage: "mainPage") {
        section {
            paragraph "<b>Names are stored on the tesmartctl server</b> (env file on the machine running <code>tesmartctl listen</code>), not in the TESmart hardware."
            paragraph "Load current values, edit the fields, then tap <b>Done</b> on the main page — Hubitat writes them with <code>POST /names</code>."
            input name: "btnRefreshNames", type: "button", title: "Refresh Names"
            if (atomicState.lastNamesPushMessage) {
                paragraph atomicState.lastNamesPushMessage
            }
            if (atomicState.lastTestMessage) {
                paragraph atomicState.lastTestMessage
            }
        }
        section("Ports 1–${count}") {
            (1..count).each { Integer n ->
                String fromListener = listenerNames["${n}"] ?: ""
                String current = settings."inputName${n}"?.toString()
                if (current == null || current.toString().isEmpty()) {
                    current = fromListener
                }
                input name: "inputName${n}", type: "text", title: "Input ${n}",
                    required: false, defaultValue: current ?: ""
            }
        }
    }
}

def installed() {
    log.debug "TESmart Manager installed"
    initialize()
}

def updated() {
    log.debug "TESmart Manager updated"
    initialize()
}

def uninstalled() {
    unregisterCallbackWithListener()
    getChildDevices()?.each { deleteChildDevice(it.deviceNetworkId) }
}

def initialize() {
    ensureAccessToken()
    unsubscribe()
    if (mdnsUseEnabled()) {
        registerTesmartMdnsListener()
        subscribe(location, "systemStart", "bootHandler")
    }
    if (runTest) {
        runConnectionTest()
        applyListenerNamesToSettings()
    }
    pushNamesToListener()
    if (doCreateDevice) {
        upsertControllerDevice()
    } else if (getControllerDevice()) {
        pushSettingsToDevice(getControllerDevice())
    }
    syncCallbackRegistration()
    app.updateLabel(deviceLabel ?: "TESmart Manager")
}

/** Re-register background mDNS after hub reboot (cache fills over time). */
def bootHandler(evt) {
    if (mdnsUseEnabled()) {
        registerTesmartMdnsListener()
    }
}

/** Explicit opt-in only — unset / false keeps host/port-only (existing installs). */
private boolean mdnsUseEnabled() {
    return useMdns == true
}

/** Second step when mDNS is on: prefer mesh failover vs a specific instance name. */
private boolean mdnsUseMeshEnabled() {
    return mdnsUseEnabled() && useMdnsMesh == true
}

/**
 * Hubitat app-level mDNS cache (registerMDNSListener + getMDNSEntries).
 * Complements NetworkUtils.getMDNSServiceResults one-shot browse.
 */
private void registerTesmartMdnsListener() {
    if (!mdnsUseEnabled()) {
        return
    }
    try {
        registerMDNSListener(MDNS_LISTENER_TYPE)
        log.info "TESmart Manager registered mDNS listener for ${MDNS_LISTENER_TYPE}"
    } catch (MissingMethodException e) {
        log.warn "TESmart Manager registerMDNSListener unavailable: ${e.message}"
    } catch (Exception e) {
        log.warn "TESmart Manager registerMDNSListener failed: ${e.message}"
    }
}

/** tesmartctl POST /callbacks target — updates the controller device immediately. */
def handleTesmartEvent() {
    def body = request?.JSON
    if (!(body instanceof Map)) {
        try {
            body = new JsonSlurper().parseText(request?.body?.toString() ?: "{}")
        } catch (ignored) {
            body = [:]
        }
    }
    if (logEnable) {
        log.debug "TESmart callback: ${body}"
    }
    // Health probes from GET|POST /callbacks/health — acknowledge without touching the device.
    if ((body?.event ?: "").toString() == "health_check") {
        if (logEnable) {
            log.debug "TESmart health_check ok"
        }
        return
    }
    applyListenerFromCallback(body instanceof Map ? (body as Map) : [:])
    if ((body?.event ?: "").toString() in ["enabled_changed", "online"]) {
        if (body.containsKey("disabled") || (body?.event ?: "").toString() == "enabled_changed") {
            rememberEnablement(body.disabled, body.input_count as Integer)
        }
    }
    def device = getControllerDevice()
    if (!device) {
        log.warn "TESmart callback received but no controller device exists"
        return
    }
    try {
        device.handleInputChanged(new JsonBuilder(body).toString())
    } catch (Exception e) {
        log.warn "handleInputChanged failed: ${e.message}"
    }
}

private void ensureAccessToken() {
    if (!state.accessToken) {
        try {
            createAccessToken()
        } catch (Exception e) {
            log.warn "Could not create OAuth access token (${e.message}). Enable OAuth for TESmart Manager under Apps Code if push callbacks are needed."
        }
    }
}

private String hubCallbackUrl() {
    if (!state.accessToken) {
        return null
    }
    String base = null
    try {
        base = getFullLocalApiServerUrl()
    } catch (ignored) {
    }
    if (!base) {
        try {
            base = getFullApiServerUrl()
        } catch (ignored) {
        }
    }
    if (!base) {
        return null
    }
    return "${base}/tesmart-event?access_token=${state.accessToken}"
}

private void syncCallbackRegistration() {
    String url = hubCallbackUrl()
    atomicState.callbackUrl = url
    if (!url) {
        atomicState.callbackMessage = "<span style='color:red'>No callback URL (OAuth token missing).</span>"
        return
    }
    if (enableCallbacks != false) {
        registerCallbackWithListener(url)
    } else {
        unregisterCallbackWithListener(url)
    }
}

private void registerCallbackWithListener(String url = null) {
    String target = url ?: hubCallbackUrl()
    String base = listenerBaseUrl()
    if (!base || !target) {
        return
    }
    String callbackName = "Hubitat ${location?.name ?: hub?.zigbeeId ?: 'hub'}"
    String body = new JsonBuilder([url: target, name: callbackName]).toString()
    try {
        httpPost([
            uri                : "${base}/callbacks",
            contentType        : "application/json",
            requestContentType : "application/json",
            body               : body,
            timeout            : 20
        ]) { resp ->
            if (resp.status in [200, 201]) {
                atomicState.callbackMessage = "<span style='color:green'>Callback registered with the server as <b>${callbackName}</b>.</span>"
            } else {
                atomicState.callbackMessage = "<span style='color:red'>Callback register HTTP ${resp.status}. Update tesmartctl for /callbacks.</span>"
            }
        }
    } catch (Exception e) {
        atomicState.callbackMessage = "<span style='color:red'>Callback register failed: ${e.message}</span>"
        if (logEnable) {
            log.warn "POST /callbacks failed: ${e.message}"
        }
    }
}

private void unregisterCallbackWithListener(String url = null) {
    String target = url ?: atomicState.callbackUrl ?: hubCallbackUrl()
    String base = listenerBaseUrl()
    if (!base || !target) {
        return
    }
    String body = new JsonBuilder([url: target]).toString()
    try {
        httpPost([
            uri                : "${base}/callbacks/remove",
            contentType        : "application/json",
            requestContentType : "application/json",
            body               : body,
            timeout            : 20
        ]) { resp ->
            if (resp.status == 200) {
                atomicState.callbackMessage = "Callback removed from the server."
            }
        }
    } catch (ignored) {
    }
}

private Map inputChoiceOptions() {
    Integer count = (atomicState.lastInputCount ?: prefInputCount ?: "8") as Integer
    Map listenerNames = currentListenerNames()
    Map opts = [:]
    (1..count).each { Integer n ->
        String label = listenerNames["${n}"] ?: "Input ${n}"
        opts["${n}"] = "${n} — ${label}"
    }
    return opts
}

private String formatInputList(Object numbers) {
    if (!(numbers instanceof List) || ((List) numbers).isEmpty()) {
        return "none"
    }
    Map listenerNames = currentListenerNames()
    return numbers.collect { item ->
        Integer n = item as Integer
        String label = listenerNames["${n}"]
        return label ? "${n} — ${label}" : "${n}"
    }.join(", ")
}

private void rememberEnablement(Object disabledObj, Integer count) {
    Integer ports = count ?: ((atomicState.lastInputCount ?: prefInputCount ?: 8) as Integer)
    List<Integer> disabled = []
    if (disabledObj instanceof List) {
        disabledObj.each { item ->
            try {
                Integer number = item as Integer
                if (number != null && number >= 1 && number <= ports) {
                    disabled << number
                }
            } catch (ignored) {
            }
        }
    }
    disabled = disabled.unique().sort()
    List<Integer> enabled = (1..ports).findAll { Integer number -> !(number in disabled) }
    state.disabledInputs = disabled
    state.enabledInputs = enabled
}

private void setInputEnabledOnListener(boolean enabled) {
    String selected = settings.mgrInput?.toString()?.trim()
    if (!selected) {
        atomicState.enablementMessage = "<span style='color:red'>Choose an input first.</span>"
        return
    }
    String base = listenerBaseUrl()
    if (!base) {
        atomicState.enablementMessage = "<span style='color:red'>Server host is required.</span>"
        return
    }
    String path = enabled ? "enable" : "disable"
    String uri = "${base}/${path}/${selected}"
    if (logEnable) log.debug "TESmart Manager GET ${uri}"
    try {
        httpGet([uri: uri, contentType: "application/json", timeout: 20]) { resp ->
            if (resp.status != 200) {
                atomicState.enablementMessage = "<span style='color:red'>HTTP ${resp.status}</span>"
                return
            }
            def json = resp.data instanceof Map ? resp.data : new JsonSlurper().parseText(resp.data?.toString() ?: "{}")
            Integer count = (json.input_count ?: atomicState.lastInputCount ?: prefInputCount ?: 8) as Integer
            rememberEnablement(json.disabled, count)
            String word = (json.enabled == true || json.enabled?.toString() == "true") ? "enabled" : "disabled"
            atomicState.enablementMessage = "<span style='color:green'>Input <b>${json.input ?: selected}</b> ${word}.</span>"
            def device = getControllerDevice()
            if (device) {
                Map event = [
                    event      : "enabled_changed",
                    disabled   : json.disabled ?: [],
                    input_count: count,
                    source     : "command"
                ]
                try {
                    device.handleInputChanged(new JsonBuilder(event).toString())
                } catch (Exception e) {
                    log.warn "enablement device update failed: ${e.message}"
                }
            }
        }
    } catch (Exception e) {
        atomicState.enablementMessage = "<span style='color:red'>Failed: ${e.message}</span>"
        if (logEnable) log.warn "TESmart Manager enablement failed: ${e.message}"
    }
}

private void runPendingListenerAction() {
    String action = atomicState.pendingListenerAction
    if (!action) {
        return
    }
    atomicState.pendingListenerAction = null
    if (action == "enable" || action == "disable") {
        setInputEnabledOnListener(action == "enable")
        return
    }
    if (action == "status") {
        refreshListenerStatus()
        return
    }
    if (action == "discover") {
        discoverMdnsListeners()
        return
    }
    if (action == "applyDiscovered") {
        applyDiscoveredListener()
        return
    }
    runConnectionTest()
    if (action == "refreshNames") {
        applyListenerNamesToSettings()
    }
    if (logEnable) {
        log.debug "TESmart Manager server fetch: ${atomicState.lastTestMessage}"
    }
}

private String listenerStatusLine() {
    String ver = atomicState.lastServerVersion ? "tesmartctl <b>${atomicState.lastServerVersion}</b>" : "server version not read yet"
    String active = atomicState.lastActive != null ? "Active input <b>${atomicState.lastActive}</b>." : "Active input not read yet."
    String mesh = meshIdentityLine()
    return mesh ? "${ver}. ${active}<br/>${mesh}" : "${ver}. ${active}"
}

private String meshIdentityLine() {
    String mdns = (atomicState.lastMdnsName ?: "").toString().trim()
    String mesh = (atomicState.lastMeshMdnsName ?: "").toString().trim()
    String role = (atomicState.lastMeshRole ?: "").toString().trim()
    if (!mdns && !mesh && !role) {
        return ""
    }
    List bits = []
    if (mdns) {
        bits << "instance <b>${mdns}</b>"
    }
    if (mesh) {
        bits << "mesh alias <b>${mesh}</b>"
    }
    if (role) {
        bits << "role <b>${role}</b>"
    }
    return bits.join(" · ")
}

/** Mesh roster from the last GET /status (not the one-shot Hubitat mDNS browse). */
private String meshPeersLine() {
    try {
        def parsed = new JsonSlurper().parseText(atomicState.lastMeshPeersJson ?: "[]")
        if (!(parsed instanceof List) || !(parsed as List)) {
            return ""
        }
        List lines = []
        (parsed as List).each { item ->
            if (!(item instanceof Map)) {
                return
            }
            Map p = item as Map
            String name = (p.mdns_name ?: p.node_id ?: "?").toString()
            String role = (p.role ?: "?").toString()
            String url = (p.url ?: "").toString()
            String selfBit = p.self ? " (this listener)" : ""
            lines << "• <b>${role}</b> ${name}${url ? " → <code>${url}</code>" : ""}${selfBit}"
        }
        if (!lines) {
            return ""
        }
        return "<b>Mesh peers</b> (from server /status — full roster):<br/>${lines.join('<br/>')}"
    } catch (ignored) {
        return ""
    }
}

/** Read GET /status and refresh the version, active input, and enablement lists. */
private void refreshListenerStatus() {
    String base = listenerBaseUrl()
    if (!base) {
        atomicState.lastStatusMessage = "<span style='color:red'>Server host is required.</span>"
        return
    }
    String uri = "${base}/status?network=0"
    if (logEnable) log.debug "TESmart Manager status GET ${uri}"
    try {
        httpGet([uri: uri, contentType: "application/json", timeout: 20]) { resp ->
            if (resp.status != 200) {
                atomicState.lastStatusMessage = "<span style='color:red'>HTTP ${resp.status}</span>"
                return
            }
            def json = resp.data instanceof Map ? resp.data : new JsonSlurper().parseText(resp.data?.toString() ?: "{}")
            applyListenerStatus(json)
            String ver = atomicState.lastServerVersion ? "tesmartctl <b>${atomicState.lastServerVersion}</b>" : "the listener"
            atomicState.lastStatusMessage = "<span style='color:green'><b>Online.</b></span> ${ver}, active input <b>${atomicState.lastActive}</b>."
        }
    } catch (Exception e) {
        atomicState.lastStatusMessage = "<span style='color:red'>Unreachable: ${e.message}</span>"
        if (logEnable) log.warn "TESmart Manager status failed: ${e.message}"
    }
}

private void applyListenerStatus(Map json) {
    Integer active = json.active_input as Integer
    Integer count = (json.input_count ?: prefInputCount ?: 8) as Integer
    Map names = (json.names instanceof Map) ? json.names : [:]
    if (json.version) {
        atomicState.lastServerVersion = json.version.toString()
    }
    atomicState.lastActive = active
    atomicState.lastInputCount = count
    atomicState.lastListenerNames = new JsonBuilder(names).toString()
    if (json.containsKey("disabled")) {
        rememberEnablement(json.disabled, count)
    }
    Map identity = [:]
    if (json.mdns_name) {
        identity.mdns_name = json.mdns_name
    }
    boolean inMesh = false
    if (json.mesh instanceof Map) {
        Map mesh = json.mesh as Map
        // Standalone listeners omit mesh; treat enabled:false / missing role as not meshed.
        inMesh = mesh.enabled != false && (mesh.role || mesh.peers || mesh.mesh_mdns_name)
        if (inMesh) {
            if (mesh.role) {
                identity.role = mesh.role
            }
            if (mesh.mesh_mdns_name && !identity.mesh_mdns_name) {
                identity.mesh_mdns_name = mesh.mesh_mdns_name
            }
            if (mesh.mdns_name && !identity.mdns_name) {
                identity.mdns_name = mesh.mdns_name
            }
            if (mesh.peers instanceof List) {
                atomicState.lastMeshPeersJson = new JsonBuilder(mesh.peers).toString()
            }
        }
    }
    if (json.mesh_mdns_name && inMesh) {
        identity.mesh_mdns_name = json.mesh_mdns_name
    }
    if (inMesh) {
        rememberMeshIdentity(identity)
    } else {
        // Standalone /status must not keep a previous mesh alias / peer roster.
        clearMeshIdentity(keepMdnsName: (identity.mdns_name ?: json.mdns_name)?.toString())
    }
    state.connectionOk = true
    def device = getControllerDevice()
    if (!device) {
        return
    }
    Map event = [
        event       : "online",
        active_input: active,
        input_count : count,
        names       : names,
        source      : "status"
    ]
    if (json.containsKey("disabled")) {
        event.disabled = json.disabled
    }
    try {
        device.handleInputChanged(new JsonBuilder(event).toString())
    } catch (Exception e) {
        log.warn "status device update failed: ${e.message}"
    }
}

private void runConnectionTest() {
    String base = listenerBaseUrl()
    if (!base) {
        atomicState.lastTestMessage = "<span style='color:red'>Host is required.</span> Enter the server IP, wait a moment for it to save, then test again."
        state.connectionOk = false
        atomicState.compatOk = false
        return
    }
    if (!checkServerCompatibility(base)) {
        return
    }
    String uri = "${base}/status?network=0"
    if (logEnable) log.debug "TESmart Manager test GET ${uri}"
    try {
        httpGet([uri: uri, contentType: "application/json", timeout: 20]) { resp ->
            if (resp.status == 200) {
                def json = resp.data instanceof Map ? resp.data : new JsonSlurper().parseText(resp.data?.toString() ?: "{}")
                applyListenerStatus(json)
                Integer count = (json.input_count ?: prefInputCount ?: 8) as Integer
                Map names = (json.names instanceof Map) ? json.names : [:]
                String ver = atomicState.lastServerVersion ? " Server <b>${atomicState.lastServerVersion}</b>." : ""
                atomicState.lastTestMessage = "<span style='color:green'><b>Connected.</b></span>${ver} Active input <b>${atomicState.lastActive}</b>, ${count} ports, ${names.size()} server name(s)."
            } else {
                state.connectionOk = false
                atomicState.lastTestMessage = "<span style='color:red'>HTTP ${resp.status}</span>"
            }
        }
    } catch (Exception e) {
        state.connectionOk = false
        atomicState.lastTestMessage = "<span style='color:red'>Failed: ${e.message}</span><br/>Check that tesmartctl is listening on ${base} and Hubitat can reach it."
        if (logEnable) log.warn "TESmart Manager test failed: ${e.message}"
    }
}

/**
 * POST /compat with this package's minimum server version and required features.
 * Returns true when the server is reachable and compatible.
 */
private boolean checkServerCompatibility(String base) {
    Map body = [
        min_server    : MIN_SERVER_VERSION,
        client        : "hubitat",
        client_version: PACKAGE_VERSION,
        features      : REQUIRED_SERVER_FEATURES
    ]
    String payload = new JsonBuilder(body).toString()
    if (logEnable) {
        log.debug "TESmart Manager compat POST ${base}/compat ${payload}"
    }
    try {
        boolean ok = false
        httpPost([
            uri                : "${base}/compat",
            contentType        : "application/json",
            requestContentType : "application/json",
            body               : payload,
            timeout            : 20
        ]) { resp ->
            if (resp.status == 404) {
                atomicState.compatOk = false
                state.connectionOk = false
                atomicState.lastServerVersion = null
                atomicState.lastTestMessage = "<span style='color:red'><b>Server too old.</b></span> No <code>/compat</code> route. Upgrade tesmartctl to <b>${MIN_SERVER_VERSION}+</b> on the host, then retest."
                return
            }
            if (resp.status != 200) {
                atomicState.compatOk = false
                state.connectionOk = false
                atomicState.lastTestMessage = "<span style='color:red'>Compatibility check HTTP ${resp.status}</span>"
                return
            }
            def json = resp.data instanceof Map ? resp.data : new JsonSlurper().parseText(resp.data?.toString() ?: "{}")
            if (json.error == "not supported") {
                atomicState.compatOk = false
                state.connectionOk = false
                atomicState.lastTestMessage = "<span style='color:red'><b>Server too old.</b></span> ${json.message ?: 'Upgrade tesmartctl.'} Need <b>${MIN_SERVER_VERSION}+</b>."
                return
            }
            String serverVer = (json.version ?: "").toString()
            if (serverVer) {
                atomicState.lastServerVersion = serverVer
            }
            boolean compatible = json.compatible == true
            atomicState.compatOk = compatible
            if (!compatible) {
                state.connectionOk = false
                String detail = json.message ?: "incompatible"
                List missing = (json.missing_features instanceof List) ? json.missing_features : []
                if (missing) {
                    detail += " (missing: ${missing.join(', ')})"
                }
                atomicState.lastTestMessage = "<span style='color:red'><b>Incompatible.</b></span> ${detail}<br/>This Hubitat package ${PACKAGE_VERSION} needs tesmartctl <b>${MIN_SERVER_VERSION}+</b> (found <b>${serverVer ?: 'unknown'}</b>)."
                return
            }
            ok = true
        }
        return ok
    } catch (Exception e) {
        // Older servers may reject POST /compat as connection failure — try GET /version.
        if (logEnable) {
            log.warn "POST /compat failed (${e.message}); trying GET /version"
        }
        return checkServerVersionFallback(base)
    }
}

/** Fallback when /compat is missing: require GET /version and compare locally. */
private boolean checkServerVersionFallback(String base) {
    try {
        boolean ok = false
        httpGet([uri: "${base}/version", contentType: "application/json", timeout: 20]) { resp ->
            if (resp.status == 404) {
                atomicState.compatOk = false
                state.connectionOk = false
                atomicState.lastTestMessage = "<span style='color:red'><b>Server too old.</b></span> No <code>/version</code> or <code>/compat</code>. Upgrade tesmartctl to <b>${MIN_SERVER_VERSION}+</b>."
                return
            }
            if (resp.status != 200) {
                atomicState.compatOk = false
                state.connectionOk = false
                atomicState.lastTestMessage = "<span style='color:red'>Version check HTTP ${resp.status}</span>"
                return
            }
            def json = resp.data instanceof Map ? resp.data : new JsonSlurper().parseText(resp.data?.toString() ?: "{}")
            String serverVer = (json.version ?: "").toString()
            atomicState.lastServerVersion = serverVer ?: null
            boolean enough = versionAtLeast(serverVer, MIN_SERVER_VERSION)
            atomicState.compatOk = enough
            if (!enough) {
                state.connectionOk = false
                atomicState.lastTestMessage = "<span style='color:red'><b>Incompatible.</b></span> Server <b>${serverVer ?: 'unknown'}</b> is older than required <b>${MIN_SERVER_VERSION}</b>."
                return
            }
            ok = true
        }
        return ok
    } catch (Exception e) {
        atomicState.compatOk = false
        state.connectionOk = false
        atomicState.lastTestMessage = "<span style='color:red'><b>Cannot check server version.</b></span> ${e.message}<br/>Upgrade tesmartctl to <b>${MIN_SERVER_VERSION}+</b> and ensure Hubitat can reach ${base}/compat."
        if (logEnable) {
            log.warn "version fallback failed: ${e.message}"
        }
        return false
    }
}

/** Compare dotted numeric versions (e.g. 0.0.7.9). */
private boolean versionAtLeast(String have, String need) {
    if (!need) {
        return true
    }
    if (!have) {
        return false
    }
    List a = have.tokenize('.')?.findAll { it ==~ /^\d+$/ }?.collect { it as Integer } ?: []
    List b = need.tokenize('.')?.findAll { it ==~ /^\d+$/ }?.collect { it as Integer } ?: []
    int n = Math.max(a.size(), b.size())
    for (int i = 0; i < n; i++) {
        int left = (i < a.size()) ? (a[i] as Integer) : 0
        int right = (i < b.size()) ? (b[i] as Integer) : 0
        if (left > right) {
            return true
        }
        if (left < right) {
            return false
        }
    }
    return true
}

private void upsertControllerDevice() {
    String dni = "tesmart-app-${app.id}"
    def device = getChildDevice(dni)
    String label = deviceLabel ?: "TESmart Switch"
    if (!device) {
        try {
            device = addChildDevice(
                "wbevan_tesmart",
                "TESmart Controller",
                dni,
                [
                    name        : label,
                    label       : label,
                    isComponent : false
                ]
            )
            log.info "Created TESmart Controller ${dni}"
        } catch (Exception e) {
            log.error "Could not create TESmart Controller — install the TESmart Controller and TESmart Input drivers first (${e.message})"
            return
        }
    } else {
        try {
            device.setLabel(label)
        } catch (ignored) {
        }
    }
    pushSettingsToDevice(device)
    try {
        device.refresh()
    } catch (ignored) {
    }
}

private void pushSettingsToDevice(device) {
    if (!device) {
        return
    }
    Map payload = [
        listenerHost     : listenerHost,
        listenerPort     : parseListenerPort(listenerPort),
        useHttps         : useHttps == true,
        refreshInterval  : (refreshInterval ?: "60").toString(),
        prefInputCount   : (prefInputCount ?: "8").toString(),
        createChildren   : createChildSwitches != false,
        nameOverrides    : [:],
        // From the last connection test / Refresh Names — so children get real labels.
        listenerNames    : currentListenerNames()
    ]
    String json = new JsonBuilder(payload).toString()
    try {
        device.configureFromApp(json)
    } catch (Exception e) {
        log.warn "configureFromApp failed (${e.message}); trying settings directly"
        try {
            device.updateSetting("listenerHost", [type: "text", value: listenerHost?.toString()])
            device.updateSetting("listenerPort", [type: "text", value: parseListenerPort(listenerPort).toString()])
            device.updateSetting("createChildren", [type: "bool", value: (createChildSwitches != false).toString()])
            device.updated()
        } catch (Exception e2) {
            log.error "Could not configure device: ${e2.message}"
        }
    }
}

/** Copy listener names into app settings so the edit fields show current values. */
private void applyListenerNamesToSettings() {
    Integer count = (atomicState.lastInputCount ?: prefInputCount ?: "8") as Integer
    Map names = currentListenerNames()
    (1..count).each { Integer n ->
        String label = names["${n}"] ?: ""
        app.updateSetting("inputName${n}", [type: "text", value: label])
    }
}

/** Write Hubitat name fields to the listener via POST /names. */
private void pushNamesToListener() {
    String base = listenerBaseUrl()
    if (!base) {
        return
    }
    Integer count = (atomicState.lastInputCount ?: prefInputCount ?: "8") as Integer
    Map names = [:]
    (1..count).each { Integer n ->
        String label = settings."inputName${n}"?.toString()?.trim() ?: ""
        names["${n}"] = label
    }
    // Skip POST if every field is still blank and we never loaded names (avoid wiping).
    boolean anySet = names.values().any { it }
    if (!anySet && !atomicState.lastListenerNames) {
        return
    }
    String body = new JsonBuilder([names: names]).toString()
    if (logEnable) {
        log.debug "TESmart Manager POST ${base}/names ${body}"
    }
    try {
        httpPost([
            uri                : "${base}/names",
            contentType        : "application/json",
            requestContentType : "application/json",
            body               : body,
            timeout            : 20
        ]) { resp ->
            if (resp.status == 200) {
                def json = resp.data instanceof Map ? resp.data : new JsonSlurper().parseText(resp.data?.toString() ?: "{}")
                Map saved = (json.names instanceof Map) ? (json.names as Map) : [:]
                atomicState.lastListenerNames = new JsonBuilder(saved).toString()
                atomicState.lastNamesPushMessage = "<span style='color:green'><b>Saved ${saved.size()} name(s)</b> to the server.</span>"
                applyListenerNamesToSettings()
            } else {
                atomicState.lastNamesPushMessage = "<span style='color:red'>Could not save names (HTTP ${resp.status}). Update tesmartctl so <code>POST /names</code> is available.</span>"
            }
        }
    } catch (Exception e) {
        atomicState.lastNamesPushMessage = "<span style='color:red'>Could not save names: ${e.message}</span><br/>Need a recent tesmartctl with <code>POST /names</code>."
        if (logEnable) {
            log.warn "TESmart Manager POST /names failed: ${e.message}"
        }
    }
}

/** Names last returned by GET /status or POST /names on the listener. */
private Map currentListenerNames() {
    try {
        String raw = atomicState.lastListenerNames
        if (!raw) {
            return [:]
        }
        def parsed = new JsonSlurper().parseText(raw)
        if (!(parsed instanceof Map)) {
            return [:]
        }
        Map out = [:]
        (parsed as Map).each { k, v ->
            if (v != null && v.toString().trim()) {
                out[k.toString()] = v.toString().trim()
            }
        }
        return out
    } catch (ignored) {
        return [:]
    }
}

private def getControllerDevice() {
    return getChildDevices()?.find { it.typeName == "TESmart Controller" } ?: getChildDevices()?.find { true }
}

private String listenerBaseUrl() {
    String host = (listenerHost ?: "").trim()
    if (!host) {
        return null
    }
    if (host.startsWith("http://") || host.startsWith("https://")) {
        return host.replaceAll(/\/$/, "")
    }
    String scheme = useHttps ? "https" : "http"
    return "${scheme}://${host}:${parseListenerPort(listenerPort)}"
}

/**
 * One-shot mDNS browse for tesmartctl. Do not schedule this on a timer —
 * continuous browse risks multicast storms on some hubs.
 * Needs Hubitat 2.5.1+ NetworkUtils.getMDNSServiceResults — call it directly;
 * preflight probes falsely report unsupported on some hubs.
 */
private void discoverMdnsListeners() {
    if (!mdnsUseEnabled()) {
        atomicState.mdnsHitsJson = "[]"
        atomicState.mdnsMessage = "mDNS discovery is off. Enable <b>Use mDNS discovery</b> first."
        return
    }
    Integer timeoutMs = 15000  // Hubitat clamps to 500..15000 ms
    try {
        // Ensure background cache is collecting (may already be warm from initialize).
        registerTesmartMdnsListener()

        List hits = []
        Map hitKeys = [:]

        // 1) Hub background cache — registerMDNSListener + getMDNSEntries
        //    https://community.hubitat.com/t/mdns-not-working-properly/161810/8
        List cached = []
        try {
            log.info "TESmart Manager mDNS query: getMDNSEntries(serviceType=${MDNS_LISTENER_TYPE})"
            cached = getMDNSEntries(MDNS_LISTENER_TYPE) ?: []
            log.info "TESmart Manager mDNS getMDNSEntries return: size=${cached.size()} value=${cached}"
            Integer cidx = 0
            cached.each { item ->
                log.info "TESmart Manager mDNS cache item[${cidx}]: map=${item instanceof Map} value=${item}"
                if (item instanceof Map) {
                    Map row = item as Map
                    log.info "TESmart Manager mDNS cache item[${cidx}] keys=${row.keySet()}"
                    Map hit = mdnsHitFromCacheRow(row)
                    if (hit && !hitKeys[hit.key]) {
                        hitKeys[hit.key] = true
                        hits << hit
                    }
                }
                cidx++
            }
        } catch (MissingMethodException e) {
            log.warn "TESmart Manager getMDNSEntries unavailable: ${e.message}"
        } catch (Exception e) {
            log.warn "TESmart Manager getMDNSEntries failed: ${e.message}"
        }

        // 2) One-shot browse — NetworkUtils.getMDNSServiceResults (2.5.1+)
        log.info "TESmart Manager mDNS query: NetworkUtils.getMDNSServiceResults(" +
            "serviceType=${MDNS_SERVICE_TYPE}, timeoutMs=${timeoutMs})"
        List results = hubitat.helper.NetworkUtils.getMDNSServiceResults(MDNS_SERVICE_TYPE, timeoutMs)
        int rawCount = (results ?: []).size()
        // Do not call getClass() — Hubitat sandbox rejects it.
        log.info "TESmart Manager mDNS getMDNSServiceResults return: size=${rawCount} list=${results instanceof List} value=${results}"
        Integer idx = 0
        (results ?: []).each { item ->
            boolean isMap = item instanceof Map
            log.info "TESmart Manager mDNS browse item[${idx}]: map=${isMap} value=${item}"
            if (isMap) {
                Map row = item as Map
                log.info "TESmart Manager mDNS browse item[${idx}] keys=${row.keySet()} " +
                    "name=${row.name} qualifiedName=${row.qualifiedName} server=${row.server} " +
                    "port=${row.port} ip4Addresses=${row.ip4Addresses} ip6Addresses=${row.ip6Addresses} " +
                    "textRecords=${row.textRecords}"
                Map hit = mdnsHitFromBrowseRow(row)
                if (hit && !hitKeys[hit.key]) {
                    hitKeys[hit.key] = true
                    hits << hit
                } else if (!hit) {
                    log.warn "TESmart Manager mDNS browse skip unusable row: ${row}"
                }
            } else {
                log.warn "TESmart Manager mDNS browse skip non-Map item value=${item}"
            }
            idx++
        }

        log.info "TESmart Manager mDNS merged hits before live probe (${hits.size()}): ${hits}"
        // mDNS (and Hubitat getMDNSEntries) keep dead ads until TTL expires —
        // probe HTTP so a shut-down listener drops off Discover immediately.
        List live = []
        Integer stale = 0
        hits.each { hit ->
            if (!(hit instanceof Map)) {
                return
            }
            Map h = hit as Map
            if (mdnsListenerReachable(h.host?.toString(), h.port as Integer)) {
                live << h
            } else {
                stale++
                log.info "TESmart Manager mDNS stale (no HTTP): ${h.name} → ${h.host}:${h.port}"
            }
        }
        hits = live
        log.info "TESmart Manager mDNS live hits (${hits.size()}, dropped ${stale} stale): ${hits}"
        // Hubitat's mDNS browse often misses other hosts (e.g. Luckfox …-mesh).
        // Expand from any reachable node's GET /status mesh.peers + mesh alias URL.
        Integer beforeExpand = hits.size()
        hits = expandMdnsHitsFromMeshStatus(hits)
        Integer expanded = hits.size() - beforeExpand
        if (expanded > 0) {
            log.info "TESmart Manager mDNS expanded +${expanded} from /status mesh roster: ${hits}"
        }
        // Mesh alias first, then proxy, then name.
        hits = hits.sort { a, b ->
            int aa = a.alias ? 0 : 1
            int ba = b.alias ? 0 : 1
            if (aa != ba) {
                return aa <=> ba
            }
            int ar = (a.role == "proxy") ? 0 : 1
            int br = (b.role == "proxy") ? 0 : 1
            if (ar != br) {
                return ar <=> br
            }
            return (a.name as String) <=> (b.name as String)
        }
        atomicState.mdnsHitsJson = new JsonBuilder(hits).toString()
        if (!hits) {
            String staleNote = stale ? " Dropped ${stale} stale mDNS ad(s) that did not answer HTTP." : ""
            atomicState.mdnsMessage = "No live <code>_wbevan-tesmart</code> listeners (browse=${rawCount}, cache=${cached.size()}).${staleNote} Is tesmartctl still advertising?"
        } else {
            String staleNote = stale ? " Dropped ${stale} stale." : ""
            String expandNote = expanded > 0 ? " +${expanded} from mesh /status." : ""
            atomicState.mdnsMessage = "<span style='color:green'><b>Found ${hits.size()} live listener(s).</b></span>${staleNote}${expandNote} (browse=${rawCount}, cache=${cached.size()}) Select one (prefer <code>…-mesh</code>), then <b>Use selected listener</b>."
        }
    } catch (MissingMethodException e) {
        atomicState.mdnsHitsJson = "[]"
        atomicState.mdnsMessage = "<span style='color:red'>mDNS Discover needs Hubitat platform <b>2.5.1+</b> (NetworkUtils.getMDNSServiceResults).</span>"
        log.warn "TESmart Manager mDNS browse unavailable: ${e.message}"
    } catch (Exception e) {
        atomicState.mdnsHitsJson = "[]"
        atomicState.mdnsMessage = "<span style='color:red'>mDNS Discover failed: ${e.message}</span>"
        log.warn "TESmart Manager mDNS browse failed: ${e.message}"
    }
}

/**
 * True when host:port answers a quick HTTP GET (mDNS ads linger after shutdown).
 * Hubitat httpGet with a closure waits for the response before returning.
 */
private boolean mdnsListenerReachable(String host, Integer port) {
    if (!host || !port) {
        return false
    }
    String uri = "http://${host}:${port}/version"
    boolean ok = false
    try {
        httpGet([uri: uri, contentType: "application/json", timeout: 3]) { resp ->
            ok = (resp?.status == 200)
        }
    } catch (Exception e) {
        log.info "TESmart Manager mDNS probe fail ${uri}: ${e.message}"
        ok = false
    }
    return ok
}

/** GET /status from a discovered listener (for mesh peer / alias expansion). */
private Map mdnsFetchStatus(String host, Integer port) {
    if (!host || !port) {
        return null
    }
    String uri = "http://${host}:${port}/status?network=0"
    Map out = null
    try {
        httpGet([uri: uri, contentType: "application/json", timeout: 5]) { resp ->
            if (resp?.status == 200) {
                def data = resp.data
                if (data instanceof Map) {
                    out = data as Map
                } else {
                    out = new JsonSlurper().parseText(data?.toString() ?: "{}") as Map
                }
            }
        }
    } catch (Exception e) {
        log.info "TESmart Manager mDNS /status fail ${uri}: ${e.message}"
    }
    return out
}

private Map parseHttpBaseUrl(String base) {
    String text = (base ?: "").toString().trim().replaceAll(/\/$/, "")
    if (!text) {
        return null
    }
    try {
        // http://host:port or http://host
        def m = (text =~ /^(?i)https?:\/\/\[?([^\/\]:]+)\]?(?::(\d+))?/)
        if (!m.find()) {
            return null
        }
        String host = m.group(1)
        Integer port = m.group(2) ? (m.group(2) as Integer) : (text.toLowerCase().startsWith("https") ? 443 : 80)
        return [host: host, port: port]
    } catch (ignored) {
        return null
    }
}

/**
 * Hubitat NetworkUtils often returns only one LAN host. Use any live node's
 * mesh snapshot to add the failover alias and other peers (e.g. Luckfox).
 */
private List expandMdnsHitsFromMeshStatus(List liveHits) {
    Map keys = [:]
    List out = []
    (liveHits ?: []).each { item ->
        if (!(item instanceof Map)) {
            return
        }
        Map hit = item as Map
        out << hit
        if (hit.key) {
            keys[hit.key] = true
        }
    }
    // Snapshot seeds — out grows as we add.
    List seeds = out.collect { it }
    seeds.each { seed ->
        if (!(seed instanceof Map)) {
            return
        }
        Map seedHit = seed as Map
        Map status = mdnsFetchStatus(seedHit.host?.toString(), seedHit.port as Integer)
        if (!status) {
            return
        }
        Map mesh = (status.mesh instanceof Map) ? (status.mesh as Map) : [:]
        String meshName = (status.mesh_mdns_name ?: mesh.mesh_mdns_name ?: seedHit.mesh_name ?: "").toString().trim()
        String base = (mesh.base_url ?: "").toString().trim()
        if (meshName && base) {
            Map parsed = parseHttpBaseUrl(base)
            if (parsed) {
                Map aliasHit = mdnsHitMap(
                    meshName,
                    parsed.host as String,
                    parsed.port as Integer,
                    [role: "proxy", mesh_name: meshName, kind: "mesh"]
                )
                aliasHit.alias = true
                if (!keys[aliasHit.key] && mdnsListenerReachable(aliasHit.host as String, aliasHit.port as Integer)) {
                    keys[aliasHit.key] = true
                    out << aliasHit
                    log.info "TESmart Manager mDNS +alias from /status: ${aliasHit}"
                } else if (!keys[aliasHit.key]) {
                    log.info "TESmart Manager mDNS alias from /status not reachable: ${aliasHit.host}:${aliasHit.port}"
                }
            }
        }
        List peers = (mesh.peers instanceof List) ? (mesh.peers as List) : []
        peers.each { peer ->
            if (!(peer instanceof Map)) {
                return
            }
            Map p = peer as Map
            String name = (p.mdns_name ?: "").toString().trim()
            String url = (p.url ?: "").toString().trim()
            if (!name || !url) {
                return
            }
            Map parsed = parseHttpBaseUrl(url)
            if (!parsed) {
                return
            }
            String role = (p.role ?: "").toString().trim()
            Map peerHit = mdnsHitMap(
                name,
                parsed.host as String,
                parsed.port as Integer,
                [role: role, mesh_name: meshName]
            )
            if (name.toLowerCase().endsWith("-mesh")) {
                peerHit.alias = true
            }
            if (!keys[peerHit.key] && mdnsListenerReachable(peerHit.host as String, peerHit.port as Integer)) {
                keys[peerHit.key] = true
                out << peerHit
                log.info "TESmart Manager mDNS +peer from /status: ${peerHit}"
            }
        }
    }
    return out
}

/** Normalize one-shot browse row → hit map (or null). */
private Map mdnsHitFromBrowseRow(Map row) {
    String name = (row.name ?: row.qualifiedName ?: "").toString().trim()
    if (!name) {
        return null
    }
    String shortName = name
    if (shortName.contains(".")) {
        shortName = shortName.tokenize(".")[0]
    }
    List ipv4 = (row.ip4Addresses instanceof List) ? (row.ip4Addresses as List) : []
    String host = ipv4 ? ipv4[0]?.toString() : (row.server ?: "").toString().replaceAll(/\.$/, "")
    Integer port = null
    try {
        port = row.port != null ? (row.port as Integer) : null
    } catch (ignored) {
        port = null
    }
    if (!host || !port) {
        return null
    }
    Map txt = (row.textRecords instanceof Map) ? (row.textRecords as Map) : [:]
    return mdnsHitMap(shortName, host, port, txt)
}

/**
 * Normalize getMDNSEntries / cache row → hit map (or null).
 * Field names vary by firmware; accept several common shapes.
 */
private Map mdnsHitFromCacheRow(Map row) {
    String name = (row.name ?: row.instance ?: row.serviceName ?: row.qualifiedName ?: "").toString().trim()
    if (!name) {
        return null
    }
    String shortName = name
    if (shortName.contains(".")) {
        shortName = shortName.tokenize(".")[0]
    }
    String host = (row.ip ?: row.ipAddress ?: row.host ?: "").toString().trim()
    if (!host && row.ip4Addresses instanceof List && (row.ip4Addresses as List)) {
        host = (row.ip4Addresses as List)[0]?.toString()
    }
    if (!host && row.server) {
        host = row.server.toString().replaceAll(/\.$/, "")
    }
    Integer port = null
    try {
        if (row.port != null) {
            port = row.port as Integer
        }
    } catch (ignored) {
        port = null
    }
    Map txt = [:]
    if (row.textRecords instanceof Map) {
        txt = row.textRecords as Map
    } else if (row.properties instanceof Map) {
        txt = row.properties as Map
    } else if (row.txt instanceof Map) {
        txt = row.txt as Map
    }
    if (!host || !port) {
        log.warn "TESmart Manager mDNS cache skip ${shortName}: host=${host} port=${row.port} raw=${row}"
        return null
    }
    return mdnsHitMap(shortName, host, port, txt)
}

private Map mdnsHitMap(String shortName, String host, Integer port, Map txt) {
    String role = (txt?.role ?: "").toString().trim()
    String meshName = (txt?.mesh_name ?: "").toString().trim()
    String kind = (txt?.kind ?: "").toString().trim()
    boolean alias = (kind == "mesh") || shortName.toLowerCase().endsWith("-mesh")
    return [
        name     : shortName,
        host     : host,
        port     : port,
        role     : role,
        mesh_name: meshName,
        alias    : alias,
        key      : "${shortName}|${host}|${port}"
    ]
}

/** Split last Discover hits into mesh failover aliases vs specific instance names. */
private Map mdnsHitsPartitioned() {
    List meshes = []
    List instances = []
    try {
        def parsed = new JsonSlurper().parseText(atomicState.mdnsHitsJson ?: "[]")
        if (!(parsed instanceof List)) {
            return [meshes: meshes, instances: instances]
        }
        (parsed as List).each { item ->
            if (!(item instanceof Map)) {
                return
            }
            Map hit = item as Map
            if (!hit.key) {
                return
            }
            if (hit.alias) {
                meshes << hit
            } else {
                instances << hit
            }
        }
    } catch (ignored) {
    }
    return [meshes: meshes, instances: instances]
}

private Map mdnsOptionsFromHits(List hits) {
    Map out = [:]
    (hits ?: []).each { item ->
        if (!(item instanceof Map)) {
            return
        }
        Map hit = item as Map
        String key = (hit.key ?: "").toString()
        if (!key) {
            return
        }
        out[key] = mdnsChoiceLabel(hit)
    }
    return out
}

/** Label mesh failover vs specific instance for the pickers. */
private String mdnsChoiceLabel(Map hit) {
    String endpoint = "${hit.name} → ${hit.host}:${hit.port}"
    if (hit.alias) {
        return "mesh failover · ${endpoint}"
    }
    String meshName = (hit.mesh_name ?: "").toString().trim()
    String role = (hit.role ?: "").toString().trim()
    if (meshName) {
        String roleBit = role ? " (${role})" : ""
        return "instance${roleBit} · ${endpoint} (mesh group ${meshName})"
    }
    if (role) {
        return "standalone (${role}) · ${endpoint}"
    }
    return "standalone · ${endpoint}"
}

private void applyDiscoveredListener() {
    if (!mdnsUseEnabled()) {
        atomicState.mdnsMessage = "<span style='color:red'>mDNS discovery is off.</span>"
        return
    }
    boolean wantMesh = mdnsUseMeshEnabled()
    String pick = wantMesh ?
        (settings.discoveredMeshListener ?: "").toString().trim() :
        (settings.discoveredInstanceListener ?: "").toString().trim()
    if (!pick) {
        atomicState.mdnsMessage = wantMesh ?
            "<span style='color:red'>Select a mesh failover name first.</span>" :
            "<span style='color:red'>Select a specific mDNS name first.</span>"
        return
    }
    List parts = pick.tokenize("|")
    if (parts.size() < 3) {
        atomicState.mdnsMessage = "<span style='color:red'>Invalid discovery selection.</span>"
        return
    }
    String name = parts[0]
    String host = parts[1]
    String port = parts[2]
    boolean meshFailover = wantMesh || name.toLowerCase().endsWith("-mesh")
    app.updateSetting("listenerHost", [type: "text", value: host])
    app.updateSetting("listenerPort", [type: "text", value: port])
    if (meshFailover) {
        atomicState.lastMdnsName = name
        atomicState.lastMeshMdnsName = name
        atomicState.lastMeshRole = "proxy"
        // Mesh alias moves with the proxy — keep callback follow on.
        app.updateSetting("followListenerEndpoint", [type: "bool", value: true])
        atomicState.mdnsMessage = "<span style='color:green'><b>Using mesh failover ${name}</b> at ${host}:${port}.</span> Callback follow is on. Run the connection test."
    } else {
        // Standalone pick must not inherit a previous mesh alias / peer list.
        clearMeshIdentity(keepMdnsName: name)
        atomicState.mdnsMessage = "<span style='color:green'><b>Using standalone mDNS name ${name}</b> at ${host}:${port}.</span> Run the connection test."
    }
    if (logEnable) {
        log.debug "TESmart Manager applied mDNS pick ${name} → ${host}:${port} meshFailover=${meshFailover}"
    }
}

/** Update host/port from callback listener identity (mesh failover). */
private void applyListenerFromCallback(Map body) {
    def listener = body?.listener
    if (!(listener instanceof Map)) {
        return
    }
    Map info = listener as Map
    rememberMeshIdentity(info)
    // Only when the user opted into mesh failover + follow.
    if (!mdnsUseMeshEnabled() || followListenerEndpoint == false) {
        return
    }
    String host = (info.host ?: "").toString().trim()
    Integer port = null
    try {
        if (info.port != null) {
            port = info.port as Integer
        }
    } catch (ignored) {
        port = null
    }
    String base = (info.base_url ?: "").toString().trim().replaceAll(/\/$/, "")
    if ((!host || port == null) && base) {
        try {
            URI uri = new URI(base)
            if (!host) {
                host = uri.host
            }
            if (port == null && uri.port > 0) {
                port = uri.port
            }
        } catch (ignored) {
        }
    }
    if (!host) {
        return
    }
    String curHost = (listenerHost ?: "").toString().trim()
    Integer curPort = parseListenerPort(listenerPort)
    Integer newPort = port != null ? port : curPort
    if (host.equalsIgnoreCase(curHost) && newPort == curPort) {
        return
    }
    app.updateSetting("listenerHost", [type: "text", value: host])
    app.updateSetting("listenerPort", [type: "text", value: newPort.toString()])
    log.info "TESmart Manager followed listener endpoint from callback: ${host}:${newPort}"
    def device = getControllerDevice()
    if (device) {
        pushSettingsToDevice(device)
    }
}

private void rememberMeshIdentity(Map info) {
    if (!info) {
        return
    }
    String mdns = (info.mdns_name ?: "").toString().trim()
    String mesh = (info.mesh_mdns_name ?: "").toString().trim()
    String role = (info.role ?: "").toString().trim()
    if (mdns) {
        atomicState.lastMdnsName = mdns
    }
    if (mesh) {
        atomicState.lastMeshMdnsName = mesh
    }
    if (role) {
        atomicState.lastMeshRole = role
    }
}

/** Drop mesh alias / role / peers (standalone listener or standalone Discover pick). */
private void clearMeshIdentity(Map opts = [:]) {
    String keep = (opts.keepMdnsName ?: "").toString().trim()
    if (keep) {
        atomicState.lastMdnsName = keep
    } else {
        atomicState.remove("lastMdnsName")
    }
    atomicState.remove("lastMeshMdnsName")
    atomicState.remove("lastMeshRole")
    atomicState.lastMeshPeersJson = "[]"
}

/** Plain text port field — strip commas Hubitat may have stored from the old number input. */
private Integer parseListenerPort(raw) {
    String text = (raw ?: "8080").toString().replaceAll(/[,_\s]/, "").trim()
    if (!text.isInteger()) {
        return 8080
    }
    Integer port = text as Integer
    return (port >= 1 && port <= 65535) ? port : 8080
}

private String imgHtml(String url, String alt, Integer maxWidth = null) {
    String width = maxWidth ? "max-width:${maxWidth}px;" : "max-width:100%;"
    return "<div style='margin:0.5em 0'><img src='${url}' alt='${alt}' style='${width}height:auto;border-radius:6px'/></div>"
}
