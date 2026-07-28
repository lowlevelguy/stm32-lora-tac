"""MQTT topic + payload mapping for uplink and downlink frames.

Per MQTT Topic Map sheet (latest revision):

  Uplink (UART -> MQTT):
    Telemetry (TypeID=0x01)
        -> enddev{SourceID}/sensor  payload = 2 bytes: SensorID (1B) | SensorValue (1B)
        -> enddev{SourceID}/rssi    payload = 2 bytes, int16 signed big-endian
        -> enddev{SourceID}/snr    payload = 1 byte,  int8 signed
    ACK (TypeID=0x03)
        -> enddev{SourceID}/ack    payload = 2 bytes: ActuatorID (1B) | Status (1B)

  Downlink (MQTT -> UART, SRS-PY-03 / SRS-PY-04):
    Subscription wildcard: enddev+/actuator (normalised to +/actuator,
        see DOWNLINK_TOPIC_WILDCARD below)
    MQTT payload: 2 bytes ActuatorID (1B) | Cmd (1B)
    UART frame built: 0xA5 | GatewayID (0x00) | EndDeviceID | 0x02 |
                      ActuatorID | Cmd | 0x00 | 0x00

RSSI is packed as int16 signed big-endian (struct '>h'): the SRS Topic
Map row example b'\\xFF\\x88' decodes to exactly -120 under big-endian,
matching the annotated value, so no endianness ambiguity exists.
"""

import re
import struct

from lora_frame import FrameType, LoraFrame


# Lightweight value carrier so main.py can log topics uniformly.
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class Publication:
    """A single MQTT publication to be sent by the mqtt client."""
    topic: str
    payload: bytes
    # Human-readable label used for SRS-PY-05 logging (topic column).
    label: str


# ============================================================ UPLINK

def uplink_publications(frame: LoraFrame) -> list[Publication]:
    """Map a decoded uplink frame to one or more MQTT publications.

    Returns an empty list for frames that are not uplink-relevant
    (e.g. unknown TypeID, or downlink command frames that we ourselves
    emitted and that have no business being republished upstream).
    """
    match frame.type_id:
        case FrameType.TELEMETRY:
            return _telemetry_publications(frame)
        case FrameType.ACK:
            return _ack_publications(frame)
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

    # RSSI — 2-byte int16 signed big-endian (Topic Map row "enddev1/rssi").
    rssi = frame.telemetry_rssi_dbm
    if rssi is not None:
        pubs.append(Publication(
            topic=f"enddev{frame.source_addr}/rssi",
            payload=_rssi_payload(rssi),
            label=f"enddev{frame.source_addr}/rssi",
        ))

    # SNR — 1-byte int8 signed (Topic Map row "enddev1/snr").
    snr = frame.telemetry_snr_db
    if snr is not None:
        pubs.append(Publication(
            topic=f"enddev{frame.source_addr}/snr",
            payload=_snr_payload(snr),
            label=f"enddev{frame.source_addr}/snr",
        ))

    return pubs


def _ack_publications(frame: LoraFrame) -> list[Publication]:
    """ACK uplink — enddev{SourceID}/ack, 2 bytes ActuatorID | Status."""
    payload = frame.ack_payload
    if payload is None:
        return []
    return [Publication(
        topic=f"enddev{frame.source_addr}/ack",
        payload=payload,
        label=f"enddev{frame.source_addr}/ack",
    )]


def _rssi_payload(rssi_dbm: int) -> bytes:
    """Pack RSSI as int16 signed big-endian per SRS Topic Map.

    Range -32768..32767; SRS-GW-05 yields -200..+55 in practice.
    """
    return struct.pack(">h", int(rssi_dbm))


def _snr_payload(snr_db: int) -> bytes:
    """Pack SNR as int8 signed (single byte)."""
    return struct.pack("b", int(snr_db))


# ============================================================ DOWNLINK

# Topic the bridge subscribes to for actuator commands (SRS-PY-03).
#
# NOTE: SRS-PY-03 writes the filter as "enddev+/actuator", but that is not
# valid MQTT: a '+' wildcard must occupy an entire topic level by itself
# (it cannot be a suffix of "enddev"). The valid filter that achieves the
# SRS's intent - matching enddev1/actuator, enddev2/actuator, ... - is
# "+/actuator". Spurious matches on non-enddev topics are filtered out
# downstream by parse_downlink_topic's regex (^enddev(\d+)$).
DOWNLINK_TOPIC_WILDCARD = "+/actuator"

# Topic pattern that matches a concrete published actuator command.
# Captures the EndDevice ID (digits only) as group "enddev".
_DOWNLINK_TOPIC_RE = re.compile(r"^enddev(?P<enddev>\d+)/actuator$")


def parse_downlink_topic(topic: str) -> int | None:
    """Extract the EndDevice ID from an actuator topic.

    Returns the integer EndDevice ID, or None if the topic does not match.
    Per SRS-PY-04 note: "DestID extracted from enddev{N}/actuator -> N
    converted to byte". IDs > 255 cannot fit in a single DestID byte and
    are rejected.
    """
    m = _DOWNLINK_TOPIC_RE.match(topic)
    if m is None:
        return None
    n = int(m.group("enddev"))
    if not (0 <= n <= 0xFF):
        return None
    return n


def build_downlink_frame(
    dest_addr: int, actuator_id: int, cmd: int,
) -> bytes:
    """Build an 8-byte UART frame for a downlink actuator command.

    Per SRS-PY-04:
      SOF=0xA5 | SourceID=GatewayID (0x00) | DestID=EndDeviceID |
      TypeID=0x02 | ActuatorID | Cmd | 0x00 | 0x00

    Returns the raw 8 bytes ready to be written to the serial port.
    """
    frame = LoraFrame.command_frame(
        dest_addr=dest_addr,
        actuator_id=actuator_id,
        cmd=cmd,
    )
    return frame.encode()


def parse_downlink_payload(payload: bytes) -> tuple[int, int] | None:
    """Parse an MQTT downlink payload into (ActuatorID, Cmd).

    Expected format (SRS-MQTT-02, Topic Map): 2 bytes
    ActuatorID (1B) | Cmd (1B).
    Returns None if the payload is malformed.
    """
    if len(payload) != 2:
        return None
    return payload[0], payload[1]
