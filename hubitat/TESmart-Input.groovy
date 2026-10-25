/**
 *  TESmart Input
 *
 *  Child switch for one TESmart HDMI/KVM input. Bound to a parent
 *  TESmart Controller port via inputNumber / inputName (from tesmartctl
 *  ``name``). Dashboards use these as a radio group: only one child is on.
 *  Turning a child on selects that input; turning the active child off is
 *  ignored. Peek looks at this input, then returns. Enable and disable
 *  change whether the listener will select or peek this input.
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
        attribute "inputEnabled", "enum", ["enabled", "disabled"]

        command "peek", [[name: "Seconds", type: "NUMBER", description: "Optional. Blank uses the listener default"]]
        command "enableInput"
        command "disableInput"
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
        if (event.name == "inputName") {
            String label = value?.toString()?.trim()
            if (!label) {
                // Keep attribute unset when the server has no name for this port.
                return
            }
            sendEvent(name: "inputName", value: label, descriptionText: event.descriptionText)
            // Component children often keep the create-time device name; set both
            // here so the Devices list / breadcrumb show "Old Mac" not "Input 1".
            try {
                if (device.getLabel() != label) {
                    device.setLabel(label)
                }
            } catch (ignored) {
            }
            try {
                if (device.getName() != label) {
                    device.setName(label)
                }
            } catch (ignored) {
            }
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

void peek(BigDecimal seconds = null) {
    if (logEnable) log.debug "${device.displayName}: peek() → parent (input ${getDataValue('inputNumber')})"
    parent?.componentPeek(device, seconds)
}

void enableInput() {
    if (logEnable) log.debug "${device.displayName}: enableInput() → parent"
    parent?.componentSetEnabled(device, true)
}

void disableInput() {
    if (logEnable) log.debug "${device.displayName}: disableInput() → parent"
    parent?.componentSetEnabled(device, false)
}

void refresh() {
    parent?.componentRefresh(device)
}
