"""PyQt6 dashboard package — standalone MQTT client for the LoRa network.

See `.agents/session_handoff_dashboard_layout.md` for the full design
brief. This package owns SRS-MQTT-01 and SRS-MQTT-02; it never touches
the UART port and never imports the bridge's UARTParser or MQTTGateway.

Cross-package import shim
-------------------------
The handoff document sanctions importing ``lora_frame`` from the
sibling ``uart_mqtt_bridge`` package (for downlink command validation
only — see control_page.py). The setup lives in ``_bridge_import.py``
and runs once at the top of ``main.py`` before any other dashboard
module is imported. See that file for the path-merge reasoning.
"""

# This file is intentionally minimal. The path shim runs in
# _bridge_import.py at launch time (main.py imports it first), so
# merely importing ``dashboard`` does not need to perform the dance.
