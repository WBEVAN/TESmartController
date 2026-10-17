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
@Field static final String DOCS_URL = "https://github.com/WBEVAN/TESmartController/blob/main/Docs/hubitat.md"
@Field static final String LISTENER_DOCS = "https://github.com/WBEVAN/TESmartController/blob/main/Docs/listener.md"

definition(
    name: "TESmart Manager",
    namespace: "tesmart",
    author: "WBEVAN",
    description: "Set up and manage a TESmart HDMI/KVM switch via tesmartctl REST",
    category: "Convenience",
    iconUrl: "https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/readme-banner.png",
    iconX2Url: "https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/readme-banner.png",
    iconX3Url: "https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/readme-banner.png",
    importUrl: "https://raw.githubusercontent.com/WBEVAN/TESmartController/main/hubitat/TESmart-Manager.groovy",
    singleInstance: false,
    installOnOpen: true
)

preferences {
    page name: "mainPage", title: "TESmart Manager", install: true, uninstall: true
    page name: "connectionPage"
    page name: "namesPage"
}

def mainPage() {
    dynamicPage(name: "mainPage", title: "TESmart Manager", install: true, uninstall: true) {
        section {
            paragraph imgHtml(IMG_BANNER, "tesmartctl")
            paragraph "<b>Control a TESmart 8-port or 16-port HDMI/KVM switch</b> from Hubitat through the tesmartctl REST server."
            paragraph "Exactly one input is active at a time. This app creates a <b>TESmart Controller</b> device (MediaInputSource) and optional child switches for your dashboard."
        }
        section("1. Prerequisites") {
            paragraph """Before continuing, on a machine Hubitat can reach run:
<pre style="white-space:pre-wrap">tesmartctl listen --http 0.0.0.0:8080</pre>
Bind to <code>0.0.0.0</code> (or your LAN IP). There is no authentication — use a trusted network.
<a href="${LISTENER_DOCS}" target="_blank">HTTP server docs on GitHub</a>"""
        }
        section("2. Connection") {
            href(
                name: "toConnection",
                title: state.connectionOk ? "Connection — OK (${atomicState.lastActive != null ? "active input ${atomicState.lastActive}" : "tested"})" : "Configure server host & port",
                description: "Host, port, port count, refresh interval",
                page: "connectionPage"
            )
        }
        section("3. Port names") {
            href(
                name: "toNames",
                title: "Edit port names",
                description: "Stored on the tesmartctl server. Load from /status, edit here, save with Done to write back via POST /names.",
                page: "namesPage"
            )
        }
        section("4. Create / update device") {
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
        section("Logging & docs") {
            input name: "logEnable", type: "bool", title: "Enable debug logging", defaultValue: false
            paragraph "<a href=\"${DOCS_URL}\" target=\"_blank\">Hubitat integration docs</a> · <a href=\"https://github.com/WBEVAN/TESmartController\" target=\"_blank\">GitHub repo</a>"
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
    getChildDevices()?.each { deleteChildDevice(it.deviceNetworkId) }
}

def initialize() {
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
    app.updateLabel(deviceLabel ?: "TESmart Manager")
}

private void runPendingListenerAction() {
    String action = atomicState.pendingListenerAction
    if (!action) {
        return
    }
    atomicState.pendingListenerAction = null
    runConnectionTest()
    if (action == "refreshNames") {
        applyListenerNamesToSettings()
    }
    if (logEnable) {
        log.debug "TESmart Manager server fetch: ${atomicState.lastTestMessage}"
    }
}

private void runConnectionTest() {
    String base = listenerBaseUrl()
    if (!base) {
        atomicState.lastTestMessage = "<span style='color:red'>Host is required.</span> Enter the server IP, wait a moment for it to save, then test again."
        state.connectionOk = false
        return
    }
    String uri = "${base}/status?network=0"
    if (logEnable) log.debug "TESmart Manager test GET ${uri}"
    try {
        httpGet([uri: uri, contentType: "application/json", timeout: 20]) { resp ->
            if (resp.status == 200) {
                def json = resp.data instanceof Map ? resp.data : new JsonSlurper().parseText(resp.data?.toString() ?: "{}")
                Integer active = json.active_input as Integer
                Integer count = (json.input_count ?: prefInputCount ?: 8) as Integer
                Map names = (json.names instanceof Map) ? json.names : [:]
                atomicState.lastActive = active
                atomicState.lastInputCount = count
                atomicState.lastListenerNames = new JsonBuilder(names).toString()
                state.connectionOk = true
                atomicState.lastTestMessage = "<span style='color:green'><b>Connected.</b></span> Active input <b>${active}</b>, ${count} ports, ${names.size()} server name(s)."
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

private void upsertControllerDevice() {
    String dni = "tesmart-app-${app.id}"
    def device = getChildDevice(dni)
    String label = deviceLabel ?: "TESmart Switch"
    if (!device) {
        try {
            device = addChildDevice(
                "tesmart",
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
