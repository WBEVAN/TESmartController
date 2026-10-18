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

@Field static final String DRIVER_VERSION = "1.6.0"
@Field static final String CHILD_NAMESPACE = "wbevan_tesmart"
@Field static final String CHILD_TYPE = "TESmart Input"
@Field static final String BUILTIN_CHILD_NAMESPACE = "hubitat"
@Field static final String BUILTIN_CHILD_TYPE = "Generic Component Switch"

// Per-device dropdown cache (best-effort for command ENUM re-evaluation).
@Field static final Map<String, List> INPUT_SOURCE_OPTIONS = [:]

metadata {
    definition(
        name: "TESmart Controller",
        namespace: "wbevan_tesmart",
        author: "WBEVAN",
        description: "TESmart 8/16-port HDMI/KVM switch via tesmartctl REST server",
        category: "Convenience",
        importUrl: "https://raw.githubusercontent.com/WBEVAN/TESmartController/main/hubitat/TESmart-Controller.groovy"
    ) {
        capability "Actuator"
        capability "Sensor"
        capability "Refresh"
        capability "MediaInputSource"
        // Lets Rule Machine / Notifications send text to the kit/panel LCD via deviceNotification.
        capability "Notification"

        attribute "activeInput", "number"
        attribute "previousInput", "number"
        attribute "activeName", "string"
        attribute "inputCount", "number"
        attribute "supportedInputs", "string"
        attribute "mediaInputSource", "string"
        attribute "healthStatus", "enum", ["online", "offline"]
        attribute "lastRefresh", "string"
        attribute "lastChangeSource", "string"
        attribute "peekFollow", "enum", ["off", "once", "always"]
        attribute "bannerText", "string"
        attribute "bannerShowing", "enum", ["true", "false"]
        attribute "bannerMode", "enum", ["timed", "sticky", "none"]
        attribute "bannerRemaining", "number"

        // STRING (not ENUM): Hubitat freezes command ENUM lists at install time, so they
        // never pick up server names. Use Preferences → Switch/Peek for named dropdowns.
        // These commands remain for Rule Machine / automations (pass "2" or "2 - New Mac").
        command "setInputSource", [[
            name: "Input*",
            type: "STRING",
            description: "Port number or Preferences value, e.g. 2 or 2 - New Mac"
        ]]
        command "nextInput"
        command "previousInput"
        command "peek", [
            [
                name: "Input*",
                type: "STRING",
                description: "Port number or Preferences value, e.g. 2 or 2 - New Mac"
            ],
            [name: "Seconds", type: "NUMBER", description: "Optional peek duration in seconds"]
        ]
        command "setPeekFollow", [
            [
                name: "Mode*",
                type: "ENUM",
                constraints: ["off", "once", "always"],
                description: "off = stick; once = next change peeks; always = every change peeks"
            ],
            [name: "Seconds", type: "NUMBER", description: "Optional peek duration for follow peeks (1–600)"]
        ]
        command "showBanner", [
            [name: "Text*", type: "STRING", description: "LCD message on kit/panel (max ~160 chars)"],
            [name: "Seconds", type: "NUMBER", description: "Timed display 1–60; blank = server default (8)"]
        ]
        command "showStickyBanner", [
            [name: "Text*", type: "STRING", description: "Keep showing until dismiss (bannerAction)"]
        ]
        // One dropdown + Run on the device page (dismiss / reshow / clear).
        command "bannerAction", [[
            name: "Action*",
            type: "ENUM",
            constraints: [
                "dismiss — hide, keep history",
                "reshow — show last again",
                "clear — erase message AND history"
            ],
            description: "Dismiss hides on kit/panel; reshow restores last; clear wipes message and all history"
        ]]
        command "createChildInputs"
        command "deleteChildInputs"
        // configureFromApp / setNameOverrides are methods for TESmart Manager (not shown as commands)
    }

    preferences {
        // These three are the device-page controls with live names. Hubitat rebuilds
        // preference enums every time you open Preferences; command ENUMs cannot do that.
        input name: "uiSwitchInput", type: "enum", title: "Switch to input",
            description: "Select a port (updates immediately). List is rebuilt from the server each time you open Preferences",
            options: liveInputOptions(), submitOnChange: true
        input name: "uiPeekInput", type: "enum", title: "Peek at input",
            description: "Temporarily show this port, then return. Same named list as Switch to input",
            options: liveInputOptions(), submitOnChange: true
        input name: "uiPeekSeconds", type: "number", title: "Peek seconds (optional)",
            description: "How long Peek lasts; blank uses the server default", range: "1..600"
        input name: "uiPeekFollow", type: "enum", title: "Peek follow mode",
            description: "Off = input changes stick; Once = next change peeks then sticks; Always = every change peeks until Off",
            options: ["off": "Off (stick)", "once": "Once", "always": "Always"],
            submitOnChange: true
        input name: "notifySticky", type: "bool", title: "Notifications stay until dismissed",
            description: "When on, Notification / deviceNotification uses a sticky LCD banner; when off, timed (see seconds below)",
            defaultValue: false
        input name: "notifySeconds", type: "number", title: "Notification seconds (timed)",
            description: "How long Notification messages stay on the kit LCD when not sticky; blank = server default (8)",
            range: "1..60"
        input name: "listenerHost", type: "text", title: "Server host / IP",
            description: "Host running ``tesmartctl listen --http`` (reachable from Hubitat)",
            required: true, defaultValue: "192.168.1.20"
        input name: "listenerPort", type: "text", title: "Server port",
            description: "e.g. 7272 (plain digits, no commas)",
            required: true, defaultValue: "8080"
        input name: "useHttps", type: "bool", title: "Use HTTPS",
            description: "Only if you terminate TLS in front of the server",
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

// Command ENUM helper — Hubitat often freezes these after first save.
List inputSourceConstraints() {
    return cachedChoiceList("inputSourceChoices", INPUT_SOURCE_OPTIONS) ?: defaultSourceChoices()
}

/**
 * Preference enum options — rebuilt every time the device page opens (not the
 * frozen command-ENUM cache). Always "N - Name" or "N - Input N".
 */
List liveInputOptions() {
    try {
        Integer count = resolveInputCountForMenu()
        return (1..count).collect { Integer n -> choiceLabel(n, effectiveNames()) }
    } catch (ignored) {
        return defaultSourceChoices()
    }
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
    // Preference dropdowns (live named lists) — handle before re-init so the
    // selection is not lost, then clear so the control can be used again.
    String switchSel = settings.uiSwitchInput?.toString()?.trim()
    String peekSel = settings.uiPeekInput?.toString()?.trim()
    String peekFollowSel = settings.uiPeekFollow?.toString()?.trim()
    BigDecimal peekSecs = settings.uiPeekSeconds as BigDecimal
    if (switchSel) {
        clearUiEnum("uiSwitchInput")
        setInputSource(switchSel)
    }
    if (peekSel) {
        clearUiEnum("uiPeekInput")
        peek(peekSel, peekSecs)
    }
    if (peekFollowSel) {
        clearUiEnum("uiPeekFollow")
        setPeekFollow(peekFollowSel, peekSecs)
    }
    initialize()
}

private void clearUiEnum(String name) {
    try {
        device.updateSetting(name, [type: "enum", value: ""])
    } catch (ignored) {
    }
}

void initialize() {
    unschedule()
    state.driverVersion = DRIVER_VERSION
    updateInputChoiceCaches(resolveInputCountForMenu(), effectiveNames())
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

/**
 * Arm peek-follow on the listener: ``off``, ``once``, or ``always``.
 * Optional seconds overrides the duration used for those follow peeks.
 */
void setPeekFollow(String mode, BigDecimal seconds = null) {
    String normalized = (mode ?: "").trim().toLowerCase()
    if (!(normalized in ["off", "once", "always"])) {
        logWarn "setPeekFollow: mode must be off, once, or always (got '${mode}')"
        return
    }
    String path = "/peek/${normalized}"
    if (seconds != null && normalized != "off") {
        path += "?seconds=${seconds.intValue()}"
    }
    httpGetAsync(path, "handlePeekFollowResponse", [action: "peekFollow", mode: normalized])
}

/**
 * Hubitat Notification capability — used by Rule Machine “Notify”, Notifications
 * app, etc. Shows the text on the kit/panel LCD (same store as showBanner).
 */
void deviceNotification(String text) {
    String message = text?.trim()
    if (!message) {
        logWarn "deviceNotification: empty message"
        return
    }
    if (notifySticky) {
        showStickyBanner(message)
        return
    }
    BigDecimal seconds = settings.notifySeconds as BigDecimal
    showBanner(message, seconds)
}

/** Timed LCD banner on kit/panel (needs ``tesmartctl listen --http``). */
void showBanner(String text, BigDecimal seconds = null) {
    String message = text?.trim()
    if (!message) {
        logWarn "showBanner: text is required"
        return
    }
    Map body = [text: message]
    if (seconds != null) {
        body.seconds = seconds.intValue()
    }
    httpPostAsync("/banner", body, "handleBannerResponse", [action: "showBanner"])
}

/** Sticky LCD banner until dismiss (bannerAction → dismiss, or clear). */
void showStickyBanner(String text) {
    String message = text?.trim()
    if (!message) {
        logWarn "showStickyBanner: text is required"
        return
    }
    httpPostAsync("/banner", [text: message, sticky: true], "handleBannerResponse", [action: "showStickyBanner"])
}

/**
 * Device-page dropdown: dismiss / reshow / clear.
 * Also accepts short tokens: ``dismiss``, ``reshow``, ``clear`` (Rule Machine).
 */
void bannerAction(String action) {
    String token = (action ?: "").trim().toLowerCase()
    if (token.startsWith("dismiss")) {
        dismissBanner()
        return
    }
    if (token.startsWith("reshow") || token.startsWith("show")) {
        reshowBanner()
        return
    }
    if (token.startsWith("clear")) {
        clearBanner()
        return
    }
    logWarn "bannerAction: choose dismiss, reshow, or clear (got '${action}')"
}

/** Hide LCD message; keep last message and history. */
void dismissBanner() {
    httpPostAsync("/banner", [dismiss: true], "handleBannerResponse", [action: "dismissBanner"])
}

/** Put the last banner back on the LCD (if one exists). */
void reshowBanner() {
    httpPostAsync("/banner", [show: true], "handleBannerResponse", [action: "reshowBanner"])
}

/** Erase the LCD message and the entire banner history on the listener. */
void clearBanner() {
    httpPostAsync("/banner", [clear: true], "handleBannerResponse", [action: "clearBanner"])
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
        logWarn "server host is not configured"
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

private void httpPostAsync(String path, Map body, String callback, Map data = [:]) {
    String base = listenerBaseUrl()
    if (!base) {
        logWarn "server host is not configured"
        sendEvent(name: "healthStatus", value: "offline")
        return
    }
    String payload = new JsonBuilder(body ?: [:]).toString()
    Map params = [
        uri                : "${base}${path}",
        contentType        : "application/json",
        requestContentType : "application/json",
        body               : payload,
        timeout            : 20
    ]
    logDebug "POST ${params.uri} ${payload}"
    try {
        asynchttpPost(callback, params, data)
    } catch (Exception e) {
        logWarn "HTTP POST failed: ${e.message}"
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

void handlePeekFollowResponse(response, Map data = [:]) {
    if (response.hasError()) {
        logWarn "peek follow error: ${response.getErrorMessage()}"
        sendEvent(name: "healthStatus", value: "offline")
        return
    }
    Integer status = response.getStatus()
    if (status != 200) {
        logWarn "peek follow HTTP ${status}: ${response.getErrorData() ?: response.getData()}"
        sendEvent(name: "healthStatus", value: "offline")
        return
    }
    def json = response.json
    if (json == null) {
        try {
            json = new JsonSlurper().parseText(response.getData() as String)
        } catch (ignored) {
            logWarn "peek follow: could not parse JSON body"
            return
        }
    }
    sendEvent(name: "healthStatus", value: "online")
    applyPeekFollow(json instanceof Map ? (json as Map) : [:], data?.mode?.toString())
    logInfo "peek follow → ${device.currentValue('peekFollow')}"
}

void handleBannerResponse(response, Map data = [:]) {
    if (response.hasError()) {
        logWarn "banner error: ${response.getErrorMessage()}"
        sendEvent(name: "healthStatus", value: "offline")
        return
    }
    Integer status = response.getStatus()
    if (status != 200) {
        logWarn "banner HTTP ${status}: ${response.getErrorData() ?: response.getData()}"
        sendEvent(name: "healthStatus", value: "offline")
        return
    }
    def json = response.json
    if (json == null) {
        try {
            json = new JsonSlurper().parseText(response.getData() as String)
        } catch (ignored) {
            logWarn "banner: could not parse JSON body"
            return
        }
    }
    sendEvent(name: "healthStatus", value: "online")
    if (json instanceof Map) {
        applyBannerState((json as Map).banner)
    }
    logDebug "banner action=${data?.action} showing=${device.currentValue('bannerShowing')} text=${device.currentValue('bannerText')}"
}

/**
 * Called by TESmart Manager when tesmartctl POSTs a callback
 * (``input_changed``, ``names_changed``, or ``banner``).
 * ``jsonText`` is the callback body (previous, active_input, names, banner, source, …).
 */
void handleInputChanged(String jsonText) {
    if (!jsonText?.trim()) {
        return
    }
    def parsed
    try {
        parsed = new JsonSlurper().parseText(jsonText)
    } catch (Exception e) {
        logWarn "handleInputChanged: bad JSON (${e.message})"
        return
    }
    if (!(parsed instanceof Map)) {
        logWarn "handleInputChanged: expected a JSON object"
        return
    }
    Map json = parsed as Map
    String eventKind = (json.event ?: "input_changed").toString()
    if (eventKind == "health_check") {
        logDebug "health_check callback ignored on device"
        return
    }
    if (json.source) {
        sendEvent(name: "lastChangeSource", value: json.source.toString())
    }
    if (eventKind == "banner") {
        applyBannerState(json.banner)
        logInfo "banner via callback: showing=${device.currentValue('bannerShowing')} text=${device.currentValue('bannerText')}"
        return
    }
    Integer previous = (json.previous ?: json.previous_input ?: json.previousInput) as Integer
    if (previous != null) {
        sendEvent(name: "previousInput", value: previous)
    }
    applyStatus(json, [action: "callback", event: eventKind])
    if (eventKind == "names_changed") {
        def changed = json.changed instanceof Map ? json.changed : [:]
        logInfo "names changed via callback (${changed.size()} port(s), source=${json.source ?: 'unknown'})"
    } else {
        logInfo "input changed via callback: ${previous} → ${json.active_input ?: json.activeInput} (${json.source ?: 'unknown'})"
    }
}

private void applyStatus(Map json, Map data = [:]) {
    sendEvent(name: "healthStatus", value: "online")

    Integer active = (json.active_input ?: json.activeInput) as Integer
    String activeName = (json.active_name ?: json.activeName) as String
    Integer count = (json.input_count ?: json.inputCount ?: resolveInputCount()) as Integer

    // Server names (tesmartctl name); merge with Hubitat/app overrides.
    // When the JSON has no "names" key, keep whatever we already loaded.
    // Never replace a good table with an empty parse of a non-Map JSON object.
    if (json.names != null) {
        Map parsedNames = coerceNamesMap(json.names)
        if (parsedNames || isExplicitlyEmptyNames(json.names)) {
            state.listenerNames = parsedNames
        } else {
            logWarn "status names present but could not parse; keeping existing server names"
        }
    }
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

    // Banner + peek-follow travel on GET /status (and /api/state); keep Hubitat in sync.
    if (json.containsKey("banner")) {
        applyBannerState(json.banner)
    }
    applyPeekFollow(json, null)

    if (createChildren) {
        syncChildren(active)
    }

    logDebug "applyStatus active=${active} name=${activeName} count=${count} names=${names} action=${data?.action}"
}

/** Apply LCD banner attributes from a status/callback ``banner`` object (or null when cleared). */
private void applyBannerState(Object bannerObj) {
    if (bannerObj == null) {
        sendEvent(name: "bannerText", value: "")
        sendEvent(name: "bannerShowing", value: "false")
        sendEvent(name: "bannerMode", value: "none")
        sendEvent(name: "bannerRemaining", value: 0)
        return
    }
    if (!(bannerObj instanceof Map)) {
        logWarn "applyBannerState: expected a map or null"
        return
    }
    Map banner = bannerObj as Map
    String text = (banner.text ?: "").toString()
    boolean showing = banner.showing == true || banner.showing?.toString()?.toLowerCase() == "true"
    String mode = (banner.mode ?: (showing ? "timed" : "none")).toString()
    if (!(mode in ["timed", "sticky"])) {
        mode = showing ? "timed" : "none"
    }
    Integer remaining = null
    if (banner.remaining != null) {
        try {
            remaining = banner.remaining as Integer
        } catch (ignored) {
            remaining = null
        }
    }
    sendEvent(name: "bannerText", value: text)
    sendEvent(name: "bannerShowing", value: showing ? "true" : "false")
    sendEvent(name: "bannerMode", value: showing ? mode : (text ? mode : "none"))
    if (remaining != null) {
        sendEvent(name: "bannerRemaining", value: remaining)
    } else if (!showing) {
        sendEvent(name: "bannerRemaining", value: 0)
    }
}

/** Read peek-follow from ``peek.follow``, top-level ``peek_follow``, or an explicit mode. */
private void applyPeekFollow(Map json, String fallbackMode) {
    String mode = null
    if (json?.peek instanceof Map && (json.peek as Map).follow != null) {
        mode = (json.peek as Map).follow.toString()
    } else if (json?.peek_follow != null) {
        mode = json.peek_follow.toString()
    } else if (fallbackMode) {
        mode = fallbackMode
    }
    if (!mode) {
        return
    }
    mode = mode.trim().toLowerCase()
    if (!(mode in ["off", "once", "always"])) {
        return
    }
    sendEvent(name: "peekFollow", value: mode)
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
        device.updateSetting("listenerPort", [type: "text", value: opts.listenerPort.toString()])
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
    // Seed names from Manager (connection test) so children are created labeled correctly.
    if (opts.listenerNames instanceof Map) {
        state.listenerNames = coerceNamesMap(opts.listenerNames)
        state.names = effectiveNames()
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
    String currentLabel = (child.getLabel() ?: "").toString().trim()
    String currentName = (child.getName() ?: "").toString().trim()
    String displayed = currentLabel ?: currentName

    // Auto-rename when the label is still ours (or an older numeric form).
    // Leave custom Hubitat labels alone.
    boolean stillManaged = !displayed ||
        displayed == previousAuto ||
        displayed == autoLabel ||
        displayed == number.toString() ||
        displayed == "Input ${number}" ||
        displayed ==~ /^Input \d+$/ ||
        (inputName && displayed == inputName)
    if (stillManaged && displayed != autoLabel) {
        // Component children often ignore setLabel/setName; delete + recreate
        // is the reliable way to get "Old Mac" instead of "Input 1" in the UI.
        logInfo "recreating child ${child.deviceNetworkId}: '${displayed}' → '${autoLabel}'"
        String dni = child.deviceNetworkId
        try {
            deleteChildDevice(dni)
        } catch (Exception e) {
            logWarn "could not remove child ${dni} for rename: ${e.message}"
            try {
                child.setLabel(autoLabel)
                child.setName(autoLabel)
            } catch (ignored) {
            }
        }
        child = ensureChild(number)
        if (!child) {
            return
        }
    }

    child.updateDataValue("inputNumber", number.toString())
    child.updateDataValue("autoLabel", autoLabel)
    child.updateDataValue("parentDevice", parentLabel)
    if (inputName) {
        child.updateDataValue("inputName", inputName)
    } else {
        try {
            child.removeDataValue("inputName")
        } catch (ignored) {
            child.updateDataValue("inputName", "")
        }
    }
    // Always push name/label even when recreate was skipped.
    try {
        if ((child.getLabel() ?: "") != autoLabel) {
            child.setLabel(autoLabel)
        }
    } catch (ignored) {
    }
    try {
        if ((child.getName() ?: "") != autoLabel) {
            child.setName(autoLabel)
        }
    } catch (ignored) {
    }

    String desiredSwitch = (active != null && number == active) ? "on" : "off"
    List<Map> events = [
        [name: "inputNumber", value: number, descriptionText: "${autoLabel} is input ${number}"],
        [name: "parentDevice", value: parentLabel]
    ]
    // Always publish inputName (empty string clears); child driver applies label from this.
    events << [name: "inputName", value: (inputName ?: ""), descriptionText: "${autoLabel} name is ${inputName ?: 'unset'}"]
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
    Integer port = parseListenerPort(listenerPort)
    String scheme = useHttps ? "https" : "http"
    return "${scheme}://${host}:${port}"
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
    Integer count = 8
    Map names = [:]
    try {
        count = resolveInputCountForMenu()
        names = effectiveNames()
    } catch (ignored) {
        count = 8
    }
    return (1..count).collect { Integer n -> choiceLabel(n, names) }
}

/** Menu value: always ``1 - Old Mac`` or ``1 - Input 1`` when unnamed. */
private String choiceLabel(Integer number, Map names) {
    if (number == null) {
        return null
    }
    String named = lookupInputName(number, names)
    String suffix = named ?: "Input ${number}"
    return "${number} - ${suffix}"
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

/**
 * MediaInputSource / supportedInputs value: ``2 — Office`` when named, else ``2``.
 * Matches the setInputSource / peek / preference dropdowns.
 */
private String labelForInput(Integer number, Map names) {
    return choiceLabel(number, names)
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
    if (number == null || names == null || names.isEmpty()) {
        return null
    }
    // Hubitat/Java maps: GString keys from "${n}" do not match String keys.
    // Always use number.toString(), then scan entries as a fallback.
    String want = number.toString()
    def named = names.get(want)
    if (named == null) {
        named = names.get(number)
    }
    if (named == null) {
        names.each { k, v ->
            if (named == null && k != null && k.toString() == want) {
                named = v
            }
        }
    }
    if (named == null || named.toString().trim().isEmpty()) {
        return null
    }
    return named.toString().trim()
}

/** Server names plus Hubitat/app overrides (overrides win). */
private Map effectiveNames() {
    Map merged = [:]
    coerceNamesMap(state.listenerNames).each { k, v -> merged[k.toString()] = v }
    coerceNamesMap(state.nameOverrides).each { k, v -> merged[k.toString()] = v }
    // Legacy: older builds only kept state.names
    if (!merged) {
        coerceNamesMap(state.names).each { k, v -> merged[k.toString()] = v }
    }
    return merged
}

private boolean isExplicitlyEmptyNames(raw) {
    if (raw instanceof Map) {
        return (raw as Map).isEmpty()
    }
    String text = raw?.toString()?.trim()
    return text == "{}" || text == "[:]" || text == ""
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
    // Hubitat async JSON may yield a non-Map (e.g. JSONObject); re-parse via JSON.
    if (!(raw instanceof Map)) {
        try {
            raw = new JsonSlurper().parseText(new JsonBuilder(raw).toString())
        } catch (ignored) {
            try {
                raw = new JsonSlurper().parseText(raw.toString())
            } catch (ignored2) {
                return out
            }
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
        // Force Java String keys (not GString) so later .get(number.toString()) matches.
        out.put(k.toString(), label)
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
