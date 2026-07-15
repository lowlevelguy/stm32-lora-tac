"""Central configuration for the UART-MQTT bridge.

All tunable constants live here. Runtime overrides for UART_PORT and
MQTT_BROKER_HOST via environment variables are supported so the same code
can run on different machines without code edits:

    UART_PORT=/dev/ttyUSB0  python3 main.py
    MQTT_BROKER_HOST=127.0.0.1 python3 main.py
"""

import os

# ------------------------------------------------------------------- MQTT
# Default public broker per project requirement.
MQTT_BROKER_HOST = os.environ.get("MQTT_BROKER_HOST", "test.mosquitto.org")
MQTT_BROKER_PORT = int(os.environ.get("MQTT_BROKER_PORT", "1883"))
MQTT_QOS = 0
MQTT_RETAIN = False
MQTT_KEEPALIVE_S = 60

# Reconnection back-off (SRS-PY-06): 5 s initial, exponential, cap at 60 s.
MQTT_RECONNECT_MIN_S = 5
MQTT_RECONNECT_MAX_S = 60

# ------------------------------------------------------------------- UART
UART_BAUDRATE = 115200
# If set (env var or explicit override), skip auto-detection (SRS-PY-01).
UART_PORT = os.environ.get("UART_PORT")
UART_RECONNECT_INTERVAL_S = 2  # SRS-PY-06

# USB VID/PID of the STM32WL Nucleo Virtual COM Port (SRS-PY-01).
# Multiple candidates are accepted; first match wins.
UART_VID_PID_CANDIDATES = (
    ("0483", "5740"),  # STM32 Nucleo VCP — most common on Nucleo-WL55JC
    ("0483", "374B"),  # ST-Link VCP on some Nucleo revisions
)

# ----------------------------------------------------------------- Logging
# Per SRS-PY-05: [HH:MM:SS.mmm] DIR SRC->DST TID=0xNN DATA=HH HH HH HH TOPIC
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"
LOG_DATEFMT = "%H:%M:%S"

# Concise frame log format (SRS-PY-05).
FRAME_LOG_FORMAT = (
    "[{ts}] {dir} 0x{src:02X}->0x{dst:02X} TID=0x{tid:02X} "
    "DATA={data_hex} {topic}"
)
