/**
 *  TESmart Controller
 *
 *  Hubitat driver for a TESmart 8-port or 16-port HDMI/KVM switch exposed by
 *  ``tesmartctl listen --http``. Models the switch as a MediaInputSource
 *  (exactly one input active) and optionally creates one child switch per
 *  input for dashboards (radio-button behaviour).
 *
 *  Copyright 2026 — independent community project; not affiliated with TESmart.
 *
 *  Licensed under the MIT License. See the repository LICENSE file.
 */

import groovy.transform.Field
import groovy.json.JsonSlurper
import groovy.json.JsonBuilder

@Field static final String DRIVER_VERSION = "1.3.0"
@Field static final String CHILD_NAMESPACE = "tesmart"
@Field static final String CHILD_TYPE = "TESmart Input"
@Field static final String BUILTIN_CHILD_NAMESPACE = "hubitat"
@Field static final String BUILTIN_CHILD_TYPE = "Generic Component Switch"

// Per-device dropdown cache (best-effort for command ENUM re-evaluation).
@Field static final Map<String, List> INPUT_SOURCE_OPTIONS = [:]

metadata {
    definition(
        name: "TESmart Controller",
        namespace: "tesmart",
        author: "WBEVAN",
        description: "TESmart 8/16-port HDMI/KVM switch via tesmartctl REST listener",
        category: "Convenience",
        importUrl: "https://raw.githubusercontent.com/WBEVAN/TESmartController/main/hubitat/TESmart-Controller.groovy"
    ) {
        capability "Actuator"
        capability "Sensor"
        capability "Refresh"
        capability "MediaInputSource"

        attribute "activeInput", "number"
        attribute "activeName", "string"
        attribute "inputCount", "number"
        attribute "supportedInputs", "string"
        attribute "mediaInputSource", "string"
        attribute "healthStatus", "enum", ["online", "offline"]
        attribute "lastRefresh", "string"

        // Single selector: "2 — Office" (or "5" if unnamed). Sends the port number.
        command "setInputSource", [[
            name: "Input*",
            type: "ENUM",
            description: "Port number and local name from the last Refresh",
            constraints: inputSourceConstraints()
        ]]
        command "nextInput"
        command "previousInput"
        command "peek", [
            [
                name: "Input*",
                type: "ENUM",
                description: "Port number and local name from the last Refresh (same list as setInputSource)",
                constraints: inputSourceConstraints()
            ],
            [name: "Seconds", type: "NUMBER", description: "Optional peek duration in seconds"]
        ]
        command "createChildInputs"
        command "deleteChildInputs"
        // configureFromApp / setNameOverrides are methods for TESmart Manager (not shown as commands)
    }

    preferences {
        input name: "listenerHost", type: "text", title: "Listener host / IP",
            description: "Host running ``tesmartctl listen --http`` (reachable from Hubitat)",
            required: true, defaultValue: "192.168.1.20"
        input name: "listenerPort", type: "number", title: "Listener port",
            required: true, defaultValue: 8080, range: 1..65535
        input name: "useHttps", type: "bool", title: "Use HTTPS",
            description: "Only if you terminate TLS in front of the listener",
            defaultValue: false
        input name: "prefInputCount", type: "enum", title: "Port count (until first refresh)",
            description: "Used for dropdowns before /status reports input_count",
            options: ["8": "8 ports", "16": "16 ports"],
            defaultValue: "8"
        input name: "refreshInterval", type: "enum", title: "Status refresh interval",
            options: [
                "0": "Manual only",
                "30": "Every 30 seconds",
                "60": "Every 1 minute",
                "300": "Every 5 minutes",
                "600": "Every 10 minutes",
                "900": "Every 15 minutes",
                "1800": "Every 30 minutes"
            ],
            defaultValue: "60"
        input name: "createChildren", type: "bool",
            title: "Create child switch per input",
            description: "One dashboard tile per port; only one can be on (radio group)",
            defaultValue: true
        input name: "useBuiltinChild", type: "bool",
            title: "Use built-in Generic Component Switch children",
            description: "Off = use TESmart Input child driver (recommended)",
            defaultValue: false
        input name: "logEnable", type: "bool", title: "Enable debug logging", defaultValue: false
        input name: "txtEnable", type: "bool", title: "Enable description logging", defaultValue: true
    }
}

// Command ENUM helper — Hubitat may only evaluate this when the driver is saved;
// the cache is still updated on every Refresh for firmwares that re-read it.
List inputSourceConstraints() {
    return cachedChoiceList("inputSourceChoices", INPUT_SOURCE_OPTIONS) ?: defaultSourceChoices()
}

// ── lifecycle ──────────────────────────────────────────────────────────────

void installed() {
    logInfo "installed v${DRIVER_VERSION}"
    sendEvent(name: "healthStatus", value: "offline")
    initialize()
}

void updated() {
    logInfo "updated v${DRIVER_VERSION}"
    if (logEnable) {
        runIn(1800, "logsOff")
    }
    initialize()
}

void initialize() {
    unschedule()
    state.driverVersion = DRIVER_VERSION
    updateInputChoiceCaches(resolveInputCountForMenu(), (state.names as Map) ?: [:])
    scheduleRefresh()
    if (createChildren) {
        createChildInputs()
    }
    runIn(2, "refresh")
}

void logsOff() {
    log.warn "${device.displayName}: debug logging disabled"
    device.updateSetting("logEnable", [value: "false", type: "bool"])
}

// ── commands ───────────────────────────────────────────────────────────────

void refresh() {
    logDebug "refresh()"
    httpGetAsync("/status?network=0", "handleStatusResponse", [action: "refresh"])
}

void setInputSource(String input) {
    String token = resolveInputSelection(input)
    if (!token) {
        logWarn "setInputSource: choose an input from the dropdown"
        return
    }
    selectInput(token)
}

/** Alias for automations that still call setInput; not shown as a separate command. */
void setInput(String input) {
    setInputSource(input)
}

void setInput(BigDecimal input) {
    setInputSource(input?.toPlainString())
}

void nextInput() {
    httpGetAsync("/next", "handleStatusResponse", [action: "next"])
}

void previousInput() {
    httpGetAsync("/previous", "handleStatusResponse", [action: "previous"])
}

void peek(String input, BigDecimal seconds = null) {
    // Same dropdown as setInputSource ("2 — Office"); always peek by port number.
    String port = resolveInputSelection(input)
    if (!port) {
        logWarn "peek: choose an input from the dropdown"
        return
    }
    String path = "/peek/${port}"
    if (seconds != null) {
        path += "?seconds=${seconds.intValue()}"
    }
    // Peek returns after restore; refresh afterwards so Hubitat matches the switch.
    httpGetAsync(path, "handlePeekResponse", [action: "peek", requested: port])
}

void peek(BigDecimal input, BigDecimal seconds = null) {
    peek(input?.toPlainString(), seconds)
}

void deleteChildInputs() {
    getChildDevices()?.each { child ->
        logInfo "removing child ${child.deviceNetworkId}"
        deleteChildDevice(child.deviceNetworkId)
    }
}

// ── child component callbacks (Generic Component Switch / TESmart Input) ───

void componentOn(child) {
    Integer number = inputNumberFromChild(child)
    if (number == null) {
        logWarn "componentOn: cannot resolve input for ${child?.deviceNetworkId}"
        return
    }
    logDebug "componentOn ${child.displayName} → input ${number}"
    selectInput(number.toString())
}

void componentOff(child) {
    // Radio group: the active input stays on. Turning it "off" is ignored;
    // selecting another input turns this one off.
    Integer number = inputNumberFromChild(child)
    Integer active = device.currentValue("activeInput") as Integer
    logDebug "componentOff ${child?.displayName} (active=${active})"
    if (number != null && number == active) {
        child.parse([[name: "switch", value: "on", descriptionText: "${child.displayName} remains active (only one input can be selected)"]])
    }
}

void componentRefresh(child) {
    refresh()
}

// ── HTTP ───────────────────────────────────────────────────────────────────

private void selectInput(String token) {
    // Encode path segments so local names with spaces work; keep '/' out of names.
    String encoded = token.split("/").collect { String part ->
        URLEncoder.encode(part, "UTF-8").replace("+", "%20")
    }.join("/")
    httpGetAsync("/input/${encoded}", "handleStatusResponse", [action: "set", requested: token])
}

private void httpGetAsync(String path, String callback, Map data = [:]) {
    String base = listenerBaseUrl()
    if (!base) {
        logWarn "listener host is not configured"
        sendEvent(name: "healthStatus", value: "offline")
        return
    }
    Map params = [
        uri                : "${base}${path}",
        contentType        : "application/json",
        requestContentType : "application/json",
        timeout            : 20
    ]
    logDebug "GET ${params.uri}"
    try {
        asynchttpGet(callback, params, data)
    } catch (Exception e) {
        logWarn "HTTP request failed: ${e.message}"
        sendEvent(name: "healthStatus", value: "offline")
    }
}

void handleStatusResponse(response, Map data = [:]) {
    if (response.hasError()) {
        logWarn "status error: ${response.getErrorMessage()}"
        sendEvent(name: "healthStatus", value: "offline")
        return
    }
    Integer status = response.getStatus()
    if (status != 200) {
        logWarn "status HTTP ${status}: ${response.getErrorData() ?: response.getData()}"
        sendEvent(name: "healthStatus", value: "offline")
        return
    }

    def json = response.json
    if (json == null) {
        // Fall back to parsing body text if the platform did not populate .json
        try {
            json = new groovy.json.JsonSlurper().parseText(response.getData() as String)
        } catch (ignored) {
            logWarn "could not parse JSON body"
            sendEvent(name: "healthStatus", value: "offline")
            return
        }
    }

    applyStatus(json as Map, data)
}

void handlePeekResponse(response, Map data = [:]) {
    // Peek payload includes active_input after restore; reuse the same path.
    handleStatusResponse(response, data)
    // Listener may still be settling; one more light refresh helps.
    runIn(2, "refresh")
}

private void applyStatus(Map json, Map data = [:]) {
    sendEvent(name: "healthStatus", value: "online")

    Integer active = (json.active_input ?: json.activeInput) as Integer
    String activeName = (json.active_name ?: json.activeName) as String
    Integer count = (json.input_count ?: json.inputCount ?: resolveInputCount()) as Integer

    // Listener names (tesmartctl name); merge with Hubitat/app overrides.
    state.listenerNames = coerceNamesMap(json.names)
    Map names = effectiveNames()
    state.names = names
    state.inputCount = count
    updateInputChoiceCaches(count, names)

    if (!activeName) {
        activeName = lookupInputName(active, names)
    }

    List<String> supported = buildSupportedInputs(count, names)
    String sourceLabel = labelForInput(active, names)

    sendEvent(name: "inputCount", value: count)
    sendEvent(name: "supportedInputs", value: new JsonBuilder(supported).toString())
    if (active != null) {
        sendEvent(name: "activeInput", value: active, descriptionText: txtEnable ? "${device.displayName} active input is ${active}" : null)
        sendEvent(name: "mediaInputSource", value: sourceLabel, descriptionText: txtEnable ? "${device.displayName} input source is ${sourceLabel}" : null)
    }
    if (activeName) {
        sendEvent(name: "activeName", value: activeName)
    } else if (active != null) {
        sendEvent(name: "activeName", value: sourceLabel)
    }
    sendEvent(name: "lastRefresh", value: new Date().format("yyyy-MM-dd HH:mm:ss", location.timeZone))

    if (createChildren) {
        syncChildren(active)
    }

    logDebug "applyStatus active=${active} name=${activeName} count=${count} names=${names} action=${data?.action}"
}

/**
 * Called by TESmart Manager to push host/port/options onto this device.
 * ``settingsJson`` is a JSON object, e.g. {"listenerHost":"192.168.1.20","listenerPort":8080}.
 */
void configureFromApp(String settingsJson) {
    if (!settingsJson?.trim()) {
        logWarn "configureFromApp: empty settings"
        return
    }
    def parsed
    try {
        parsed = new JsonSlurper().parseText(settingsJson)
    } catch (Exception e) {
        logWarn "configureFromApp: bad JSON (${e.message})"
        return
    }
    if (!(parsed instanceof Map)) {
        logWarn "configureFromApp: expected a JSON object"
        return
    }
    Map opts = parsed as Map
    if (opts.listenerHost != null) {
        device.updateSetting("listenerHost", [type: "text", value: opts.listenerHost.toString()])
    }
    if (opts.listenerPort != null) {
        device.updateSetting("listenerPort", [type: "number", value: opts.listenerPort.toString()])
    }
    if (opts.useHttps != null) {
        device.updateSetting("useHttps", [type: "bool", value: opts.useHttps.toString()])
    }
    if (opts.refreshInterval != null) {
        device.updateSetting("refreshInterval", [type: "enum", value: opts.refreshInterval.toString()])
    }
    if (opts.prefInputCount != null) {
        device.updateSetting("prefInputCount", [type: "enum", value: opts.prefInputCount.toString()])
    }
    if (opts.createChildren != null) {
        device.updateSetting("createChildren", [type: "bool", value: opts.createChildren.toString()])
    }
    if (opts.nameOverrides instanceof Map) {
        state.nameOverrides = coerceNamesMap(opts.nameOverrides)
    }
    logInfo "configured from TESmart Manager"
    updated()
}

/** Replace Hubitat-side name overrides (JSON object of port→name). Listener names still win unless override is set. */
void setNameOverrides(String namesJson) {
    if (!namesJson?.trim()) {
        state.nameOverrides = [:]
    } else {
        try {
            def parsed = new JsonSlurper().parseText(namesJson)
            state.nameOverrides = coerceNamesMap(parsed)
        } catch (Exception e) {
            logWarn "setNameOverrides: bad JSON (${e.message})"
            return
        }
    }
    state.names = effectiveNames()
    updateInputChoiceCaches(resolveInputCount(), state.names as Map)
    if (createChildren) {
        syncChildren(device.currentValue("activeInput") as Integer)
    }
    logInfo "name overrides updated: ${state.nameOverrides}"
}

// ── children ───────────────────────────────────────────────────────────────

void createChildInputs() {
    Integer count = resolveInputCount()
    logInfo "creating/updating ${count} child input devices"
    syncChildren(device.currentValue("activeInput") as Integer)
}

private void syncChildren(Integer active) {
    Integer count = resolveInputCount()
    (1..count).each { Integer n ->
        def child = ensureChild(n)
        if (child) {
            syncChildIdentity(child, n, active)
        }
    }
}

private def ensureChild(Integer number) {
    String dni = childDni(number)
    def child = getChildDevice(dni)
    if (child) {
        return child
    }

    String label = displayLabelForInput(number, effectiveNames())
    Map props = [
        name        : label,
        label       : label,
        isComponent : true
    ]

    try {
        if (useBuiltinChild) {
            child = addChildDevice(BUILTIN_CHILD_NAMESPACE, BUILTIN_CHILD_TYPE, dni, props)
        } else {
            child = addChildDevice(CHILD_NAMESPACE, CHILD_TYPE, dni, props)
        }
        logInfo "created child ${dni} (${label})"
    } catch (Exception e) {
        // Fall back to built-in component switch if the custom child is not installed.
        if (!useBuiltinChild) {
            logWarn "TESmart Input driver not found (${e.message}); using Generic Component Switch for ${dni}"
            child = addChildDevice(BUILTIN_CHILD_NAMESPACE, BUILTIN_CHILD_TYPE, dni, props)
        } else {
            throw e
        }
    }
    return child
}

/**
 * Keep each child linked to its port: inputNumber, inputName (from tesmartctl
 * names), device label, parent display name, and on/off for the active input.
 */
private void syncChildIdentity(child, Integer number, Integer active) {
    Map names = effectiveNames()
    String inputName = lookupInputName(number, names)
    String autoLabel = displayLabelForInput(number, names)
    String parentLabel = device.displayName ?: device.getName()
    String previousAuto = child.getDataValue("autoLabel")
    String currentLabel = child.getLabel() ?: child.getName()

    child.updateDataValue("inputNumber", number.toString())
    child.updateDataValue("autoLabel", autoLabel)
    child.updateDataValue("parentDevice", parentLabel)
    if (inputName) {
        child.updateDataValue("inputName", inputName)
    } else {
        // Remove so getDataValue does not keep a stale empty string.
        try {
            child.removeDataValue("inputName")
        } catch (ignored) {
            child.updateDataValue("inputName", "")
        }
    }

    // Auto-rename when the label is still ours (or an older numeric form).
    // Leave custom Hubitat labels alone.
    boolean stillManaged = !currentLabel ||
        currentLabel == previousAuto ||
        currentLabel == autoLabel ||
        currentLabel == number.toString() ||
        currentLabel == "Input ${number}" ||
        (inputName && currentLabel == inputName)
    if (stillManaged && currentLabel != autoLabel) {
        try {
            child.setLabel(autoLabel)
        } catch (ignored) {
            // older hubs
        }
        try {
            child.setName(autoLabel)
        } catch (ignored) {
            // older hubs
        }
    }

    String desiredSwitch = (active != null && number == active) ? "on" : "off"
    List<Map> events = [
        [name: "inputNumber", value: number, descriptionText: "${autoLabel} is input ${number}"],
        [name: "parentDevice", value: parentLabel]
    ]
    if (inputName) {
        events << [name: "inputName", value: inputName, descriptionText: "${autoLabel} name is ${inputName}"]
    }
    if (child.currentValue("switch") != desiredSwitch) {
        events << [name: "switch", value: desiredSwitch, descriptionText: "${autoLabel} is ${desiredSwitch}"]
    }
    child.parse(events)
}

private String childDni(Integer number) {
    return "${device.deviceNetworkId}-input-${number}"
}

private Integer inputNumberFromChild(child) {
    if (!child) {
        return null
    }
    String stored = child.getDataValue("inputNumber")
    if (stored?.isInteger()) {
        return stored.toInteger()
    }
    def matcher = (child.deviceNetworkId =~ /-input-(\d+)$/)
    if (matcher.find()) {
        return matcher.group(1).toInteger()
    }
    return null
}

// ── helpers ────────────────────────────────────────────────────────────────

private String listenerBaseUrl() {
    String host = (listenerHost ?: "").trim()
    if (!host) {
        return null
    }
    // Allow pasting a full base URL into the host field.
    if (host.startsWith("http://") || host.startsWith("https://")) {
        return host.replaceAll(/\/$/, "")
    }
    Integer port = (listenerPort ?: 8080) as Integer
    String scheme = useHttps ? "https" : "http"
    return "${scheme}://${host}:${port}"
}

private Integer resolveInputCount() {
    return resolveInputCountForMenu()
}

private Integer resolveInputCountForMenu() {
    Integer fromState = state?.inputCount as Integer
    Integer fromAttr = null
    try {
        fromAttr = device?.currentValue("inputCount") as Integer
    } catch (ignored) {
        // metadata / preference render may not have device yet
    }
    Integer fromPref = (prefInputCount ?: "8") as Integer
    return fromState ?: fromAttr ?: fromPref ?: 8
}

/**
 * Refresh the setInputSource / peek dropdown cache ("2 — Office", …).
 * Hubitat often freezes command ENUM lists until the driver is re-saved;
 * re-open the device page after Refresh when names change.
 */
private void updateInputChoiceCaches(Integer count, Map names) {
    List<String> sources = (1..count).collect { Integer n -> choiceLabel(n, names) }
    state.inputSourceChoices = sources
    String key = choiceCacheKey()
    if (key) {
        INPUT_SOURCE_OPTIONS[key] = sources
    }
    updateDataValue("inputSourceChoices", new JsonBuilder(sources).toString())
    logDebug "input menu updated for ${count} ports: ${sources}"
}

private String choiceCacheKey() {
    try {
        return device?.deviceNetworkId ?: device?.id?.toString()
    } catch (ignored) {
        return null
    }
}

private List cachedChoiceList(String dataName, Map<String, List> fieldMap) {
    String key = choiceCacheKey()
    if (key && fieldMap[key] instanceof List && fieldMap[key]) {
        return fieldMap[key] as List
    }
    try {
        if (state?."${dataName}" instanceof List && state."${dataName}") {
            return state."${dataName}" as List
        }
    } catch (ignored) {
    }
    try {
        String raw = getDataValue(dataName)
        if (raw) {
            def parsed = new JsonSlurper().parseText(raw)
            if (parsed instanceof List && parsed) {
                return parsed.collect { it.toString() }
            }
        }
    } catch (ignored) {
    }
    return null
}

private List defaultSourceChoices() {
    Integer count = 16
    Map names = [:]
    try {
        count = resolveInputCountForMenu()
        names = (state?.names instanceof Map) ? (state.names as Map) : [:]
    } catch (ignored) {
        count = 16
    }
    return (1..count).collect { Integer n -> choiceLabel(n, names) }
}

/** Display value for menus: "2 — Office" or "5". */
private String choiceLabel(Integer number, Map names) {
    String named = lookupInputName(number, names)
    return named ? "${number} — ${named}" : "${number}"
}

/**
 * Accept a dropdown value ("3", "3 — Office", or a bare local name) and return
 * the token to send to /input/...
 */
private String resolveInputSelection(String raw) {
    if (!raw?.trim()) {
        return null
    }
    String text = raw.trim()
    // "2 — Office" or "2 - Office" from our menus → use the port number
    def numbered = (text =~ /^(\d+)\s*[—\-].+/)
    if (numbered.find()) {
        return numbered.group(1)
    }
    if (text.isInteger()) {
        return text
    }
    // Bare local name from Rule Machine / MediaInputSource
    return text
}

private List<String> buildSupportedInputs(Integer count, Map names) {
    List<String> list = []
    (1..count).each { Integer n ->
        list << labelForInput(n, names)
    }
    return list
}

/** MediaInputSource value: local name when set, otherwise the port number. */
private String labelForInput(Integer number, Map names) {
    if (number == null) {
        return null
    }
    String named = lookupInputName(number, names)
    return named ?: number.toString()
}

/** Device/dashboard label: local name when set, otherwise "Input N". */
private String displayLabelForInput(Integer number, Map names) {
    if (number == null) {
        return null
    }
    String named = lookupInputName(number, names)
    return named ?: "Input ${number}"
}

private String lookupInputName(Integer number, Map names) {
    if (number == null || names == null) {
        return null
    }
    // Prefer string keys (JSON from /status); fall back to integer keys.
    // Avoid names?[n] — Hubitat's Groovy parses ?[ as a ternary.
    def named = names.get("${number}")
    if (named == null) {
        named = names.get(number)
    }
    if (named == null || named.toString().trim().isEmpty()) {
        return null
    }
    return named.toString().trim()
}

/** Listener names plus Hubitat/app overrides (overrides win). */
private Map effectiveNames() {
    Map merged = [:]
    coerceNamesMap(state.listenerNames).each { k, v -> merged[k] = v }
    coerceNamesMap(state.nameOverrides).each { k, v -> merged[k] = v }
    // Legacy: older builds only kept state.names
    if (!merged && state.names instanceof Map) {
        merged.putAll(coerceNamesMap(state.names))
    }
    return merged
}

private Map coerceNamesMap(raw) {
    Map out = [:]
    if (raw == null) {
        return out
    }
    if (raw instanceof CharSequence) {
        try {
            raw = new JsonSlurper().parseText(raw.toString())
        } catch (ignored) {
            return out
        }
    }
    if (!(raw instanceof Map)) {
        return out
    }
    (raw as Map).each { k, v ->
        if (v == null) {
            return
        }
        String label = v.toString().trim()
        if (!label) {
            return
        }
        out[k.toString()] = label
    }
    return out
}

private void scheduleRefresh() {
    Integer seconds = (refreshInterval ?: "60") as Integer
    if (seconds <= 0) {
        logInfo "periodic refresh disabled"
        return
    }
    if (seconds == 60) {
        runEvery1Minute("refresh")
    } else if (seconds == 300) {
        runEvery5Minutes("refresh")
    } else if (seconds == 600) {
        runEvery10Minutes("refresh")
    } else if (seconds == 900) {
        runEvery15Minutes("refresh")
    } else if (seconds == 1800) {
        runEvery30Minutes("refresh")
    } else {
        // 30s (and any other custom value): self-rescheduling.
        runIn(seconds, "refreshLoop")
    }
    logInfo "refresh scheduled every ${seconds}s"
}

void refreshLoop() {
    refresh()
    Integer seconds = (refreshInterval ?: "60") as Integer
    if (seconds > 0 && seconds != 60 && seconds != 300 && seconds != 600 && seconds != 900 && seconds != 1800) {
        runIn(seconds, "refreshLoop")
    }
}

private void logDebug(String msg) {
    if (logEnable) {
        log.debug "${device.displayName}: ${msg}"
    }
}

private void logInfo(String msg) {
    if (txtEnable != false) {
        log.info "${device.displayName}: ${msg}"
    }
}

private void logWarn(String msg) {
    log.warn "${device.displayName}: ${msg}"
}
