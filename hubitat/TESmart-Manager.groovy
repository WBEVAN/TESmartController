/**
 *  TESmart Manager
 *
 *  Guided Hubitat app to connect a TESmart HDMI/KVM switch through the
 *  tesmartctl REST listener. Creates the TESmart Controller device, optional
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
@Field static final String IMG_BUTTONS = "https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/buttons.png"
@Field static final String IMG_DEMO = "https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/demo.gif"
@Field static final String DOCS_URL = "https://github.com/WBEVAN/TESmartController/blob/main/Docs/hubitat.md"
@Field static final String LISTENER_DOCS = "https://github.com/WBEVAN/TESmartController/blob/main/Docs/listener.md"

definition(
    name: "TESmart Manager",
    namespace: "tesmart",
    author: "WBEVAN",
    description: "Set up and manage a TESmart HDMI/KVM switch via tesmartctl REST",
    category: "Convenience",
    iconUrl: "https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/buttons.png",
    iconX2Url: "https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/buttons.png",
    iconX3Url: "https://raw.githubusercontent.com/WBEVAN/TESmartController/main/Docs/images/buttons.png",
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
            paragraph "<b>Control a TESmart 8-port or 16-port HDMI/KVM switch</b> from Hubitat through the tesmartctl REST listener."
            paragraph "Exactly one input is active at a time. This app creates a <b>TESmart Controller</b> device (MediaInputSource) and optional child switches for your dashboard."
        }
        section("How it looks") {
            paragraph imgHtml(IMG_BUTTONS, "input buttons", 320)
            paragraph "Optional browser panel and HTTP routes are documented with tesmartctl — Hubitat uses the same listener."
            paragraph imgHtml(IMG_DEMO, "control panel demo", 360)
        }
        section("1. Prerequisites") {
            paragraph """Before continuing, on a machine Hubitat can reach run:
<pre style="white-space:pre-wrap">tesmartctl listen --http 0.0.0.0:8080</pre>
Bind to <code>0.0.0.0</code> (or your LAN IP). There is no authentication — use a trusted network.
<a href="${LISTENER_DOCS}" target="_blank">Listener docs on GitHub</a>"""
        }
        section("2. Connection") {
            href(
                name: "toConnection",
                title: state.connectionOk ? "Connection — OK (${atomicState.lastActive != null ? "active input ${atomicState.lastActive}" : "tested"})" : "Configure listener host & port",
                description: "Host, port, port count, refresh interval",
                page: "connectionPage"
            )
        }
        section("3. Input names (optional)") {
            href(
                name: "toNames",
                title: "Name each port for Hubitat",
                description: "Overrides shown on child devices and in the setInputSource dropdown. Listener names from tesmartctl name are merged too.",
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
    dynamicPage(name: "connectionPage", title: "Listener connection", nextPage: "mainPage") {
        section {
            paragraph "Point Hubitat at the host running <code>tesmartctl listen --http</code>."
            input name: "listenerHost", type: "text", title: "Listener host / IP",
                required: true, defaultValue: "192.168.1.20"
            input name: "listenerPort", type: "number", title: "Listener port",
                required: true, defaultValue: 8080, range: "1..65535"
            input name: "useHttps", type: "bool", title: "Use HTTPS", defaultValue: false
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
            input name: "runTest", type: "bool", title: "Test connection when leaving this page / on save",
                defaultValue: true
            if (atomicState.lastTestMessage) {
                paragraph atomicState.lastTestMessage
            }
            href(
                name: "testNow",
                title: "Run connection test now",
                description: "GET /status?network=0",
                page: "connectionPage",
                params: [test: true]
            )
        }
        if (params?.test || runTest) {
            runConnectionTest()
            section("Result") {
                paragraph atomicState.lastTestMessage ?: "No result"
            }
        }
    }
}

def namesPage() {
    Integer count = (prefInputCount ?: "8") as Integer
    dynamicPage(name: "namesPage", title: "Input names", nextPage: "mainPage") {
        section {
            paragraph "These labels appear on child devices and in the <b>setInputSource</b> dropdown as <code>2 — Office</code>."
            paragraph "They override blank listener names. You can also set names on the listener with <code>tesmartctl name 2 Office</code>."
        }
        section("Ports 1–${count}") {
            (1..count).each { Integer n ->
                input name: "inputName${n}", type: "text", title: "Input ${n}",
                    required: false, defaultValue: settings."inputName${n}" ?: ""
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
    }
    if (doCreateDevice) {
        upsertControllerDevice()
    } else if (getControllerDevice()) {
        pushSettingsToDevice(getControllerDevice())
    }
    app.updateLabel(deviceLabel ?: "TESmart Manager")
}

private void runConnectionTest() {
    String base = listenerBaseUrl()
    if (!base) {
        atomicState.lastTestMessage = "<span style='color:red'>Host is required.</span>"
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
                atomicState.lastTestMessage = "<span style='color:green'><b>Connected.</b></span> Active input <b>${active}</b>, ${count} ports, ${names.size()} listener name(s)."
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
        listenerPort     : listenerPort as Integer,
        useHttps         : useHttps == true,
        refreshInterval  : (refreshInterval ?: "60").toString(),
        prefInputCount   : (prefInputCount ?: "8").toString(),
        createChildren   : createChildSwitches != false,
        nameOverrides    : collectNameOverrides()
    ]
    String json = new JsonBuilder(payload).toString()
    try {
        device.configureFromApp(json)
    } catch (Exception e) {
        log.warn "configureFromApp failed (${e.message}); trying settings directly"
        try {
            device.updateSetting("listenerHost", [type: "text", value: listenerHost?.toString()])
            device.updateSetting("listenerPort", [type: "number", value: (listenerPort ?: 8080).toString()])
            device.updateSetting("createChildren", [type: "bool", value: (createChildSwitches != false).toString()])
            device.updated()
        } catch (Exception e2) {
            log.error "Could not configure device: ${e2.message}"
        }
    }
}

private Map collectNameOverrides() {
    Integer count = (prefInputCount ?: "8") as Integer
    Map names = [:]
    (1..count).each { Integer n ->
        String label = settings."inputName${n}"?.toString()?.trim()
        if (label) {
            names["${n}"] = label
        }
    }
    return names
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
    return "${scheme}://${host}:${listenerPort ?: 8080}"
}

private String imgHtml(String url, String alt, Integer maxWidth = null) {
    String width = maxWidth ? "max-width:${maxWidth}px;" : "max-width:100%;"
    return "<div style='margin:0.5em 0'><img src='${url}' alt='${alt}' style='${width}height:auto;border-radius:6px'/></div>"
}
