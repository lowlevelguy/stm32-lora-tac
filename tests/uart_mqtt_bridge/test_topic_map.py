"""Unit tests for topic_map.py — frame -> MQTT mapping and back.

The downlink path is the high-value target: ``build_downlink_frame`` is
where the audit found that ``command_frame`` silently hard-coded
``source_addr=0x00``. The byte-layout test below locks the exact on-wire
representation so the same class of regression cannot recur quietly.

Uplink paths cover the three SRS-documented TypeIDs (telemetry, ACK,
unknown) plus the downlink-command frame, which must NOT be republished
upstream: emitting it back to the broker would create an echo loop once
the dashboard subscribes to its own commands.

RSSI/SNR payload tests pin the integer-encoding contracts (big-endian
int16, signed int8) against the exact byte sequences annotated in the
SRS ``MQTT Topic Map`` sheet rows 8-9.
"""

import pytest

from lora_frame import FrameType, LoraFrame
import topic_map
from topic_map import (
    DOWNLINK_TOPIC_WILDCARD,
    Publication,
    build_downlink_frame,
    parse_downlink_payload,
    parse_downlink_topic,
    uplink_publications,
)


# ============================================================== uplink
def test_uplink_publications_telemetry_emits_three_pubs(frame_telemetry):
    """A telemetry frame maps to exactly three Publications in SRS order:
    sensor, rssi, snr. Payload bytes match SRS ``MQTT Topic Map`` rows
    4/8/9 byte-for-byte."""
    f = frame_telemetry(
        source=1, sensor_id=0x00, sensor_value=0x1A,
        rssi_raw=0xCC, snr_raw=0xFB,
    )
    pubs = uplink_publications(f)
    assert [p.topic for p in pubs] == [
        "enddev1/sensor", "enddev1/rssi", "enddev1/snr",
    ]

    # Sensor: SensorID | SensorValue (2 bytes).
    assert pubs[0].payload == b"\x00\x1A"
    # RSSI: rssi_dbm=4 packed as int16 big-endian -> 0x0004.
    assert pubs[1].payload == b"\x00\x04"
    # SNR: snr_db=-5 packed as signed int8 -> 0xFB.
    assert pubs[2].payload == b"\xFB"

    # label mirrors topic (used by SRS-PY-05 logging).
    assert pubs[0].label == "enddev1/sensor"


def test_uplink_publications_ack_emits_one_pub(frame_ack):
    """ACK frames map to a single enddev{N}/ack publication carrying the
    2-byte ActuatorID|Status payload."""
    f = frame_ack(source=1, actuator_id=0x02, status=0x01)
    pubs = uplink_publications(f)
    assert len(pubs) == 1
    assert pubs[0].topic == "enddev1/ack"
    assert pubs[0].payload == b"\x02\x01"


def test_uplink_publications_unknown_type_returns_empty_list():
    """Unknown TypeIDs are silently dropped — the parser already counted
    the frame via decode, but no MQTT pollution occurs."""
    f = LoraFrame(0, 0, 0x42, b"\x00\x00\x00\x00")
    assert uplink_publications(f) == []


def test_uplink_publications_command_returns_empty_list(frame_command):
    """Command frames belong to the downlink path. Returning [] here is
    the load-bearing defence against an echo loop: if the gateway ever
    bounces a transmission we wrote back to us, republishing it to the
    broker would surface as a phantom dashboard command."""
    f = frame_command()
    assert uplink_publications(f) == []


# ----------------------------------------------------- payload encoders
@pytest.mark.parametrize(
    "rssi_dbm, expected",
    [
        (-120, b"\xFF\x88"),   # SRS Topic Map row 8 annotated example
        (0, b"\x00\x00"),      # zero crossing
        (32767, b"\x7F\xFF"),  # int16 signed ceiling
        (-32768, b"\x80\x00"), # int16 signed floor
    ],
)
def test_rssi_payload_packs_big_endian_int16(rssi_dbm, expected):
    """_rssi_payload uses struct '>h' so -120 round-trips to 0xFF88,
    matching the SRS Topic Map row 8 annotation exactly."""
    assert topic_map._rssi_payload(rssi_dbm) == expected


@pytest.mark.parametrize(
    "snr_db, expected",
    [
        (-5, b"\xFB"),    # SRS Topic Map row 9 annotated example
        (-128, b"\x80"),  # int8 signed floor
        (127, b"\x7F"),   # int8 signed ceiling
        (0, b"\x00"),     # zero crossing
    ],
)
def test_snr_payload_packs_signed_int8(snr_db, expected):
    """_snr_payload uses struct 'b' so the SRS row 9 example -5 -> 0xFB
    decodes correctly on the dashboard side."""
    assert topic_map._snr_payload(snr_db) == expected


# ============================================================ downlink
@pytest.mark.parametrize(
    "topic, expected",
    [
        ("enddev1/actuator", 1),
        ("enddev42/actuator", 42),
        ("enddev0/actuator", 0),    # EndDevice 0 still fits in a byte
    ],
)
def test_parse_downlink_topic_happy_path(topic, expected):
    """parse_downlink_topic extracts the integer EndDevice ID for valid
    topics, supporting SRS-PY-04's DestID extraction step."""
    assert parse_downlink_topic(topic) == expected


@pytest.mark.parametrize(
    "topic",
    [
        "device1/actuator",          # wrong prefix
        "enddev1/sensor",            # wrong suffix
        "enddev/actuator",           # no digits
        "enddevabc/actuator",        # non-numeric digits
        "enddev256/actuator",        # > 255 cannot fit in a DestID byte
        "enddev1/actuator/extra",    # extra topic levels
    ],
)
def test_parse_downlink_topic_rejects_malformed(topic):
    """Each malformed topic must return None so main.py's on_actuator
    callback can drop the message cleanly instead of building a
    mis-addressed UART frame."""
    assert parse_downlink_topic(topic) is None


@pytest.mark.parametrize(
    "payload, expected",
    [
        (b"\x00\x01", (0, 1)),
        (b"\xFF\xFE", (255, 254)),
    ],
)
def test_parse_downlink_payload_happy_path(payload, expected):
    """parse_downlink_payload returns (ActuatorID, Cmd) for valid 2-byte
    payloads."""
    assert parse_downlink_payload(payload) == expected


@pytest.mark.parametrize(
    "payload",
    [b"", b"\x00", b"\x00\x01\x02"],
)
def test_parse_downlink_payload_rejects_wrong_length(payload):
    """Any payload length other than 2 is malformed: SRS-MQTT-02 contract
    is exactly ActuatorID(1B) | Cmd(1B)."""
    assert parse_downlink_payload(payload) is None


def test_build_downlink_frame_byte_layout():
    """THE PRIMARY REGRESSION TEST for the audit's silent source_addr bug.

    build_downlink_frame(dest_addr=1, actuator_id=0, cmd=1) must emit
    exactly:

        SOF=0xA5 | SourceID=0x00 | DestID=0x01 | TypeID=0x02 |
        ActuatorID=0x00 | Cmd=0x01 | 0x00 | 0x00
        => b'\\xA5\\x00\\x01\\x02\\x00\\x01\\x00\\x00'

    The SourceID byte (offset 1) is GatewayID=0x00 per SRS-PY-04 and the
    reconciled firmware. A future change that lets a caller inject a
    non-zero SourceID through command_frame would corrupt downlink
    semantics: the endpoint would see the command as arriving from
    another EndDevice rather than from the core network. This assertion
    fails loudly in that case.
    """
    raw = build_downlink_frame(dest_addr=1, actuator_id=0, cmd=1)
    assert raw == b"\xA5\x00\x01\x02\x00\x01\x00\x00"


@pytest.mark.parametrize(
    "field, value, expected_byte_offset, expected_byte",
    [
        ("dest_addr",   256, 2, None),   # raises before reaching the wire
        ("actuator_id", 256, 4, 0x00),   # masked & 0xFF -> 0x00
        ("cmd",         256, 5, 0x00),   # masked & 0xFF -> 0x00
        ("actuator_id", 0x1FF, 4, 0xFF), # 0x1FF & 0xFF == 0xFF (low byte wins)
        ("cmd",         0x100, 5, 0x00), # 0x100 & 0xFF == 0x00
    ],
)
def test_build_downlink_frame_range_handling(
    field, value, expected_byte_offset, expected_byte,
):
    """dest_addr out of range propagates ValueError (it is passed straight
    into LoraFrame.__post_init__).

    actuator_id and cmd are deliberately masked with ``& 0xFF`` inside
    command_frame (lora_frame.py:111), so the SUT contract is masking,
    not rejection. This test pins both behaviours: dest_addr raises,
    actuator_id/cmd truncate to the low byte. Locking the masking here
    prevents a future refactor from silently rejecting values that the
    current code accepts (or vice versa).
    """
    kwargs = {"dest_addr": 1, "actuator_id": 0, "cmd": 0}
    kwargs[field] = value
    if expected_byte is None:
        # dest_addr path: __post_init__ raises ValueError.
        with pytest.raises(ValueError):
            build_downlink_frame(**kwargs)
    else:
        raw = build_downlink_frame(**kwargs)
        assert raw[expected_byte_offset] == expected_byte


def test_downlink_topic_wildcard_is_normalised_mqtt_filter():
    """DOWNLINK_TOPIC_WILDCARD is the legal MQTT filter "+/actuator",
    NOT the SRS-PY-03 literal text "enddev+/actuator" which violates the
    MQTT spec (a '+' wildcard must occupy an entire topic level).

    The spurious matches on non-enddev topics that "+/actuator" admits
    are filtered downstream by parse_downlink_topic's regex, so this
    normalisation loses no functionality. SRS-PY-03's Notes column
    documents this deviation explicitly.
    """
    assert DOWNLINK_TOPIC_WILDCARD == "+/actuator"


# ------------------------------------------------- Publication dataclass
def test_publication_is_frozen():
    """Publication is frozen=True so a callback cannot mutate a pub in
    flight (defence-in-depth against accidental topic/payload edits
    between uplink_publications() and mqtt.publish())."""
    p = Publication(topic="t", payload=b"\x00", label="t")
    with pytest.raises(Exception):
        p.topic = "other"  # type: ignore[misc]
