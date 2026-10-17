/**
 *  TESmart Input
 *
 *  Child switch for one TESmart HDMI/KVM input. Bound to a parent
 *  TESmart Controller port via inputNumber / inputName (from tesmartctl
 *  ``name``). Dashboards use these as a radio group: only one child is on.
 *  Turning a child on selects that input; turning the active child off is
 *  ignored.
 *
 *  Copyright 2026 — independent community project; not affiliated with TESmart.
 *
 *  Licensed under the MIT License. See the repository LICENSE file.
 */

metadata {
    definition(
        name: "TESmart Input",
        namespace: "wbevan_tesmart",
        author: "WBEVAN",
        description: "Child input switch for TESmart Controller (number + local name)",
        category: "Convenience",
        importUrl: "https://raw.githubusercontent.com/WBEVAN/TESmartController/main/hubitat/TESmart-Input.groovy"
    ) {
        capability "Actuator"
        capability "Switch"
        capability "Refresh"
        capability "Sensor"

        attribute "inputNumber", "number"
        attribute "inputName", "string"
        attribute "parentDevice", "string"
    }

    preferences {
        input name: "logEnable", type: "bool", title: "Enable debug logging", defaultValue: false
    }
}

void installed() {
    if (logEnable) log.debug "${device.displayName}: installed"
}

void updated() {
    if (logEnable) log.debug "${device.displayName}: updated"
}

void parse(String description) {
    // unused — parent calls parse(List)
}

void parse(List<Map> events) {
    // Parent is authoritative — apply events as-is. Do not re-read data values
    // afterward (empty inputName was overwriting a good event with null).
    events?.each { Map event ->
        if (!event?.name) {
            return
        }
        def value = event.value
        // Hubitat often displays "" string attributes as null; skip empty names.
        if (event.name == "inputName" && (value == null || value.toString().trim().isEmpty())) {
            return
        }
        sendEvent(event)
    }
}

void on() {
    if (logEnable) log.debug "${device.displayName}: on() → parent (input ${getDataValue('inputNumber')})"
    parent?.componentOn(device)
}

void off() {
    if (logEnable) log.debug "${device.displayName}: off() → parent (input ${getDataValue('inputNumber')})"
    parent?.componentOff(device)
}

void refresh() {
    parent?.componentRefresh(device)
}
