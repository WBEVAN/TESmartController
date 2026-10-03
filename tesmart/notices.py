"""User-facing warnings shared by the CLI, the panel API, and the docs.

Changing the switch's LAN address is the one operation this tool offers that
can take the device out of reach. The wording here is deliberate: it is shown
twice before a write and once more with the read-back afterwards.
"""

NETWORK_RISK_LINES = (
    "A wrong IP address, port, gateway, or mask makes the switch unreachable over the network.",
    "There is no factory reset for these settings. Recovery is only possible with a serial (RS232) cable.",
    "This software sends the values exactly as entered. It cannot check that they are valid for your "
    "network, cannot undo the change, and cannot recover a switch that is no longer reachable.",
)

NETWORK_LIABILITY = (
    "You change these settings at your own risk. This software and its authors accept no liability "
    "for any loss, damage, downtime, or cost that results from doing so."
)

NETWORK_AFTER_WRITE = (
    "The switch is still answering on its current address. The stored values take effect only after "
    "a power cycle. After that, connect to the new address."
)


def network_risk_text() -> str:
    return "\n".join(NETWORK_RISK_LINES + (NETWORK_LIABILITY,))
