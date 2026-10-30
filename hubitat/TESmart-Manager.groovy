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
@Field static final String PACKAGE_VERSION = "1.7.1"
/** Oldest tesmartctl this package is tested against (must expose /compat). */
@Field static final String MIN_SERVER_VERSION = "0.0.7.18"
@Field static final List REQUIRED_SERVER_FEATURES = [
    "compat", "callbacks", "callbacks.name", "callbacks.health", "names_changed",
    "last_sent", "banner", "enabled_changed", "enable", "shutdown", "online"
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
            String connTitle = "Configure server host & port"
            if (state.connectionOk) {
                String ver = atomicState.lastServerVersion ? "tesmartctl ${atomicState.lastServerVersion}" : "tested"
                connTitle = "Connection — OK (Hubitat ${PACKAGE_VERSION} · ${ver}${atomicState.lastActive != null ? ", input ${atomicState.lastActive}" : ""})"
            } else if (atomicState.compatOk == false) {
                connTitle = "Connection — server version too old"
            }
            href(
                name: "toConnection",
                title: connTitle,
                description: "Hubitat package ${PACKAGE_VERSION} · host, port, port count, refresh · requires tesmartctl ${MIN_SERVER_VERSION}+",
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

    dynamicPage(name: "connectionPage", title: "Server connection", nextPage: "mainPage") {
        section {
            paragraph "Point Hubitat at the server running <code>tesmartctl listen --http</code>."
            input name: "listenerHost", type: "text", title: "Server host / IP",
                required: true, defaultValue: "192.168.1.20", submitOnChange: true
            input name: "listenerPort", type: "text", title: "Server port",
                description: "e.g. 7272 (plain digits, no commas)",
                required: true, defaultValue: "8080", submitOnChange: true
            input name: "useHttps", type: "bool", title: "Use HTTPS", defaultValue: false, submitOnChange: true
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
        section("Test") {
            paragraph "This Hubitat package is <b>${PACKAGE_VERSION}</b> and requires <b>tesmartctl ${MIN_SERVER_VERSION}+</b> (checked via <code>/compat</code>)."
            input name: "runTest", type: "bool", title: "Test connection when saving the app (Done)",
                defaultValue: true
            input name: "btnTestConnection", type: "button", title: "Run connection test now"
            if (atomicState.lastTestMessage) {
                paragraph atomicState.lastTestMessage
            } else {
                paragraph "Change the host/port if needed, then click <b>Run connection test now</b>."
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
    return "${ver}. ${active}"
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
