"""Central configuration — broker URL, port discovery, serial params."""

# ------------------------------------------------------------------- MQTT
MQTT_BROKER_HOST = "test.mosquitto.org"
MQTT_BROKER_PORT = 1883
MQTT_QOS = 0
MQTT_RETAIN = False

# Reconnection back-off (SRS-PY-06): 5 s initial, exponential, cap at 60 s
MQTT_RECONNECT_MIN_S = 5
MQTT_RECONNECT_MAX_S = 60

# ------------------------------------------------------------------- UART
UART_BAUDRATE = 115200
UART_PORT = "/dev/ttyACM0"
UART_RECONNECT_INTERVAL_S = 2  # SRS-PY-06