"""Central configuration for the dashboard.

Mirrors the shape of `uart_mqtt_bridge/config.py` so both halves of the
project keep their tunables in a sibling config module. Values here are
constants (no env-var overrides requested for this scope).

ACK_TIMEOUT_S
    Handoff doc § Wire-level event map: 2 s verify timer started when a
    downlink command is published. Generous vs. the SRS's 1 s bridge
    processing budget because the round-trip adds LoRa airtime + EndDev
    ACK delay.

SUBSCRIPTION_TOPICS
    Wildcard subscriptions per user decision 2026-07-30 (handoff open
    question #4). A '+' wildcard must occupy a whole topic level, so
    we sub `+/sensor` etc. and post-filter via `parse_uplink_topic`
    (see mqtt_worker.py). This future-proofs for EndDevice 2+ without
    editing the worker when a new device appears.
"""

# ------------------------------------------------------------------- MQTT
MQTT_BROKER_HOST = "test.mosquitto.org"
MQTT_BROKER_PORT = 1883
MQTT_QOS = 0
MQTT_KEEPALIVE_S = 60

# Wildcard subscriptions — post-filtered by parse_uplink_topic().
SUBSCRIPTION_TOPICS = (
    "+/sensor",
    "+/rssi",
    "+/snr",
    "+/ack",
)

# Downlink command topic the dashboard publishes to (single EndDevice
# for now; ControlPage builds the concrete "enddev1/actuator" string).
DOWNLINK_TOPIC_TEMPLATE = "enddev{enddev_id}/actuator"
DEFAULT_ENDDEV_ID = 1

# Default actuator ID created for any newly discovered device. The
# bridge's topic_map only knows this one for the project; the user's
# ALIASES tab can add more actuator IDs per device. ActuatorID is a
# single byte 0..255.
DEFAULT_ACTUATOR_ID = 0x00

# Integer Cmd-byte bounds (per user decision 2026-07-31: integer
# commands pack their value into the single Cmd byte of the 2-byte
# MQTT payload, keeping the bridge's parse_downlink_payload invariant
# intact). CONTROL page enforces these in the number field.
CMD_BYTE_MIN_U8 = 0
CMD_BYTE_MAX_U8 = 0xFF
CMD_BYTE_MIN_S8 = -128
CMD_BYTE_MAX_S8 = 127

# Command byte values (SRS Topic Map / lora_frame.Command) for the
# Boolean actuator type.
CMD_OFF = 0x00
CMD_ON = 0x01

# ------------------------------------------------------------------ Timing
# ACK verify window: starts on downlink publish, cancelled by /ack RX.
ACK_TIMEOUT_S = 2.0

# Telemetry staleness threshold (handoff § Wire-level event map): if no
# telemetry frame arrives in this many seconds, MONITOR fades the curve
# to @font-muted and the readout bar shows "—".
TELEMETRY_STALE_S = 15.0

# Ring buffer window (handoff § Token system + user decision 2026-07-30).
# 24 samples @ 5 s cadence = 120 s of rolling history.
RING_BUFFER_CAPACITY = 24

# 1 Hz GUI sweep cadence for PlotWidget.setData() + readout refresh.
TIMER_TICK_HZ = 1
TIMER_TICK_MS = 1000 // TIMER_TICK_HZ

# Debug log roll-off (handoff § Tab 2: DEBUG).
DEBUG_LOG_MAX_LINES = 1000

# ------------------------------------------------------------------- Layout
# Handoff § Macrostructure layout: default geometry. User widened the
# default to 1280 x 720 on 2026-07-31 (more room for all four tabs;
# supersedes the earlier 720 x 480). The rail is a QSplitter (user
# requested resizable); RAIL_WIDTH_PX is the *initial* size.
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 720
RAIL_WIDTH_PX = 100
# Allow the user to drag the splitter anywhere in this range.
RAIL_MIN_WIDTH_PX = 80
RAIL_MAX_WIDTH_PX = 320

# Font token resolved at launch in main.py via QFont.exactMatch();
# tokens.qss carries the @font-family fallback chain. This is the
# preferred face chosen by the user over the handoff's JetBrains Mono
# default (installed in ~/.fonts/FiraCode Nerd Font/).
PREFERRED_FONT = "FiraCode Nerd Font"
FONT_FALLBACK_CHAIN = (
    "FiraCode Nerd Font",
    "Consolas",
    "Noto Sans Mono",
    "monospace",
)
