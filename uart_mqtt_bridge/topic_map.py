"""MQTT topic builder — map LoRa frame fields to topic strings.

Topics per SRS-MQTT-01 / MQTT Topic Map:
  Telemetry → enddev{SourceID}/sensor/{TypeID}
  ACK       → enddev{SourceID}/ack            (future downlink)
"""

from lora_frame import LoraFrame, FrameType
import struct


# ------------------------------------------------------------------- builders

def uplink_topic(frame: LoraFrame) -> str | None:
    """Return MQTT topic for an uplink telemetry frame, or None."""
    match frame.type_id:
        case FrameType.TELEMETRY:
            return f"enddev{frame.source_addr}/sensor"
        case FrameType.ACK:
            return f"enddev{frame.source_addr}/ack"
        case _:
            return None


def uplink_payload(frame: LoraFrame) -> int | None:
    """Return MQTT payload for an uplink frame."""
    match frame.type_id:
        case FrameType.TELEMETRY:
            return struct.pack(">BB",
                int(frame.telemetry_sensor_id), int(frame.telemetry_value))
        case FrameType.ACK:
            return struct.pack(">BB",
                int(frame.ack_actuator_id), int(frame.ack_status))
        case _:
            return None


# ------------------------------------------------------------------ downlink
# Future: topic_map.downlink_topic(frame), etc.  (SRS-PY-04)
