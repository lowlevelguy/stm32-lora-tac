"""MQTT topic + payload mapping for uplink frames.

Per MQTT Topic Map sheet (latest revision):

    Telemetry (TypeID=0x01)
        -> enddev{SourceID}/sensor  payload = 2 bytes: SensorID (1B) | SensorValue (1B)
        -> enddev{SourceID}/rssi    payload = ASCII signed int (dBm)
        -> enddev{SourceID}/snr    payload = ASCII signed int (dB)

ACK (TypeID=0x03) — future:
        -> enddev{SourceID}/ack     payload = ActuatorID (1B) | Status (1B)

Downlink (SRS-PY-03, SRS-PY-04) — future:
    enddev{N}/actuator MQTT message -> 8-byte UART frame for the gateway.

The uplink function returns a list of (topic, payload) tuples, because a
telemetry frame fans out to three topics. Other frame types produce zero
or one publication, which fits the same contract.
"""

from dataclasses import dataclass

from lora_frame import LoraFrame, FrameType


@dataclass(slots=True, frozen=True)
class Publication:
    """A single MQTT publication to be sent by the mqtt client."""
    topic: str
    payload: bytes
    # Human-readable label used for SRS-PY-05 logging (topic column).
    label: str


def uplink_publications(frame: LoraFrame) -> list[Publication]:
    """Map a decoded uplink frame to one or more MQTT publications.

    Returns an empty list for frames that are not uplink-relevant (e.g.
    unknown TypeID, downlink commands that arrived via MQTT).
    """
    match frame.type_id:
        case FrameType.TELEMETRY:
            return _telemetry_publications(frame)
        case FrameType.ACK:
            # ACK uplink handling is reserved for future iterations.
            return []
        case _:
            return []


def _telemetry_publications(frame: LoraFrame) -> list[Publication]:
    pubs: list[Publication] = []

    # Sensor telemetry — 2 bytes: SensorID || SensorValue.
    sensor_payload = frame.telemetry_sensor_payload
    if sensor_payload is not None:
        pubs.append(Publication(
            topic=f"enddev{frame.source_addr}/sensor",
            payload=sensor_payload,
            label=f"enddev{frame.source_addr}/sensor",
        ))

    # RSSI — ASCII signed integer (Topic Map: int16 signed, example "-120").
    rssi = frame.telemetry_rssi_dbm
    if rssi is not None:
        pubs.append(Publication(
            topic=f"enddev{frame.source_addr}/rssi",
            payload=str(rssi).encode("ascii"),
            label=f"enddev{frame.source_addr}/rssi",
        ))

    # SNR — ASCII signed integer (Topic Map: int8 signed, example "-5").
    snr = frame.telemetry_snr_db
    if snr is not None:
        pubs.append(Publication(
            topic=f"enddev{frame.source_addr}/snr",
            payload=str(snr).encode("ascii"),
            label=f"enddev{frame.source_addr}/snr",
        ))

    return pubs


# ----------------------------------------------------------- future downlink
#
# def build_downlink_frame(src_addr: int, dest_addr: int,
#                          actuator_id: int, cmd: int) -> bytes:
#     """Build an 8-byte UART frame from an MQTT actuator message (SRS-PY-04)."""
#     ...
#
# DOWNLINK_TOPIC_WILDCARD = "enddev{N}/actuator"
#
# def parse_downlink_topic(topic: str) -> int | None:
#     """Extract EndDevice ID from an enddev{N}/actuator topic."""
#     ...
