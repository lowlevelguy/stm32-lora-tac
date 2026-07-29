"""Unit tests for lora_frame.LoraFrame — the 8-byte frame codec.

Covers decode/encode, __post_init__ validation, the three typed property
families (telemetry / command / ack), the ``command_frame`` factory, and
the ``__str__`` format. Together with test_topic_map.py, this file
satisfies the unsatisfied verification clause of SRS-SYS-02: "unit tests
for decode/encode".

The five example byte strings in _SRS_EXAMPLES are copied verbatim from
the SRS workbook ``LoRa Frame Format`` sheet rows 16-20 (post-audit
correction: all SourceID fields now use 0x00 for gateway-originated
frames). Encoding round-trip against these literals is the strongest
contract we can write against the SRS short of diffing the workbook in
CI, which is out of scope.
"""

import pytest

from lora_frame import FrameType, LoraFrame


# SRS ``LoRa Frame Format`` sheet rows 16-20. Description | 8 raw bytes.
_SRS_EXAMPLES = [
    (
        "row 16 telemetry val=255, enddev1->gw",
        b"\xA5\x01\x00\x01\x01\x00\x00\xFF",
    ),
    (
        "row 17 telemetry val=1234, enddev1->gw",
        b"\xA5\x01\x00\x01\x01\x00\x04\xD2",
    ),
    (
        "row 18 cmd LED ON actuator=1, gw->enddev1",
        b"\xA5\x00\x01\x02\x01\x01\x00\x00",
    ),
    (
        "row 19 cmd LED OFF actuator=1, gw->enddev1",
        b"\xA5\x00\x01\x02\x01\x00\x00\x00",
    ),
    (
        "row 20 ack LED OK, enddev1->gw",
        b"\xA5\x01\x00\x03\x01\x00\x00\x00",
    ),
]


# --------------------------------------------------------------------- decode
def test_decode_happy_path():
    """decode() of a valid 8-byte frame populates all dataclass fields."""
    raw = b"\xA5\x01\x00\x01\x01\x1A\xCC\xFB"
    frame = LoraFrame.decode(raw)
    assert frame is not None
    assert frame.source_addr == 0x01
    assert frame.dest_addr == 0x00
    assert frame.type_id == FrameType.TELEMETRY
    assert frame.data == b"\x01\x1A\xCC\xFB"


def test_decode_rejects_short_buffer():
    """decode() returns None when fewer than 8 bytes are supplied."""
    assert LoraFrame.decode(b"\xA5\x01") is None


def test_decode_rejects_wrong_sof():
    """decode() requires SOF (0xA5) at byte 0; any other leading byte is
    rejected so the SOF state machine in uart_parser can trust it."""
    assert LoraFrame.decode(b"\xA4\x01\x00\x01\x01\x1A\xCC\xFB") is None


# ------------------------------------------------------------------- round-trip
@pytest.mark.parametrize("description, raw", _SRS_EXAMPLES)
def test_encode_round_trip(description, raw):
    """decode().encode() must reproduce the input bytes exactly.

    This locks the wire format against accidental struct.pack format
    drift or endian flips. Parametrised over five SRS examples so a
    single test failure points at the specific frame variant that
    broke.
    """
    frame = LoraFrame.decode(raw)
    assert frame is not None, f"decode failed for {description}"
    assert frame.encode() == raw, f"round-trip mismatch for {description}"


# --------------------------------------------------------- __post_init__ guards
def test_post_init_rejects_wrong_data_length():
    """__post_init__ raises ValueError for any data length other than 4."""
    with pytest.raises(ValueError, match="data must be 4 bytes"):
        LoraFrame(0, 0, 0, b"\x00")


@pytest.mark.parametrize("bad_addr", [-1, 256])
def test_post_init_rejects_source_addr_out_of_range(bad_addr):
    with pytest.raises(ValueError, match="source_addr out of range"):
        LoraFrame(bad_addr, 0, 0, b"\x00\x00\x00\x00")


@pytest.mark.parametrize("bad_addr", [-1, 256])
def test_post_init_rejects_dest_addr_out_of_range(bad_addr):
    with pytest.raises(ValueError, match="dest_addr out of range"):
        LoraFrame(0, bad_addr, 0, b"\x00\x00\x00\x00")


@pytest.mark.parametrize("bad_tid", [-1, 256])
def test_post_init_rejects_type_id_out_of_range(bad_tid):
    with pytest.raises(ValueError, match="type_id out of range"):
        LoraFrame(0, 0, bad_tid, b"\x00\x00\x00\x00")


# --------------------------------------------------------- command_frame factory
def test_command_factory_pins_source_addr_to_gateway_id():
    """command_frame source_addr is always 0x00 (GatewayID).

    Regression net for the audit's finding that command_frame silently
    ignored the source_addr argument and hardcoded 0x00. The SRS and
    firmware both agree on 0x00; this assertion exists so any future
    change to that contract surfaces here rather than corrupting
    downlink frames on the wire.
    """
    frame = LoraFrame.command_frame(dest_addr=0x01, actuator_id=0x00, cmd=0x01)
    assert frame.source_addr == 0x00
    assert frame.type_id == FrameType.COMMAND


def test_command_factory_byte_layout_matches_srs():
    """command_frame(...) byte layout matches SRS LoRa Frame Format row 18.

    Uses ActuatorID=0x00, Cmd=0x01 to mirror the firmware's notion of
    the LED actuator (ActuatorID=0x00 per SRS row 10's note), which is
    what the audit reconciled to. The SRS example row in the workbook
    uses ActuatorID=0x01; both variants are correct downlink frames, but
    the firmware is authoritative per the project's "firmware wins"
    SRS-reconciliation rule, so this is the value that matters here.
    """
    raw = LoraFrame.command_frame(
        dest_addr=0x01, actuator_id=0x00, cmd=0x01,
    ).encode()
    assert raw == b"\xA5\x00\x01\x02\x00\x01\x00\x00"


# ----------------------------------------------------------- telemetry views
def test_telemetry_views_extract_correct_bytes(frame_telemetry):
    """Telemetry typed properties pull SensorID/SensorValue/SensorPayload
    from Data[0..1]."""
    f = frame_telemetry(sensor_id=0x00, sensor_value=0x1A)
    assert f.telemetry_sensor_id == 0x00
    assert f.telemetry_sensor_value == 0x1A
    assert f.telemetry_sensor_payload == b"\x00\x1A"


@pytest.mark.parametrize(
    "raw, expected_dbm",
    [
        (0xCC, 4),       # common positive-link case (raw - 200)
        (0x00, -200),    # floor of valid LoRa reception
        (0xFF, 55),      # ceiling of uint8 representation
    ],
)
def test_telemetry_rssi_dbm_offset(frame_telemetry, raw, expected_dbm):
    """telemetry_rssi_dbm applies the SRS-GW-05 +200 offset convention."""
    f = frame_telemetry(rssi_raw=raw)
    assert f.telemetry_rssi_dbm == expected_dbm


@pytest.mark.parametrize(
    "raw, expected_db",
    [
        (0xFB, -5),    # SRS Topic Map row 9 example
        (0x0A, 10),    # positive SNR, common strong-link case
        (0x80, -128),  # int8 signed floor
        (0x7F, 127),   # int8 signed ceiling
    ],
)
def test_telemetry_snr_signed_int8(frame_telemetry, raw, expected_db):
    """telemetry_snr_db interprets Data[3] as a signed int8 two's-complement
    value, matching LoRa modem convention."""
    f = frame_telemetry(snr_raw=raw)
    assert f.telemetry_snr_db == expected_db


# ------------------------------------------------------------ command views
def test_command_views_extract_actuator_and_cmd(frame_command):
    """command_actuator_id / command_cmd read Data[0] and Data[1]."""
    f = frame_command(dest=1, actuator_id=0x05, cmd=0x01)
    assert f.command_actuator_id == 0x05
    assert f.command_cmd == 0x01


def test_typed_views_return_none_on_wrong_frame_type(
    frame_telemetry, frame_command, frame_ack,
):
    """Each typed property returns None when called on a frame of a
    mismatched TypeID. This locks the type-id switch discipline that
    prevents the bridge from publishing garbage MQTT payloads when an
    unexpected frame type arrives."""
    t = frame_telemetry()
    assert t.command_actuator_id is None
    assert t.command_cmd is None
    assert t.ack_actuator_id is None
    assert t.ack_status is None
    assert t.ack_payload is None

    c = frame_command()
    assert c.telemetry_sensor_id is None
    assert c.telemetry_rssi_dbm is None
    assert c.telemetry_snr_db is None
    assert c.ack_status is None

    a = frame_ack()
    assert a.telemetry_sensor_value is None
    assert a.command_actuator_id is None
    assert a.command_cmd is None


# ----------------------------------------------------------------- ack views
def test_ack_views_extract_actuator_status_payload(frame_ack):
    """ack_actuator_id / ack_status / ack_payload read Data[0..1]."""
    f = frame_ack(actuator_id=0x02, status=0x01)
    assert f.ack_actuator_id == 0x02
    assert f.ack_status == 0x01
    assert f.ack_payload == b"\x02\x01"


# ------------------------------------------------------------------ __str__
def test_str_format_contains_all_fields():
    """__str__ includes SOF/SRC/DST/TID/DATA substrings so the SRS-PY-05
    log line can be reconstructed from the human-readable form."""
    frame = LoraFrame(0x01, 0x00, FrameType.TELEMETRY, b"\x01\x1A\xCC\xFB")
    s = str(frame)
    assert "SOF=0xA5" in s
    assert "SRC=0x01" in s
    assert "DST=0x00" in s
    assert "TID=0x01" in s
    assert "DATA=[01 1A CC FB]" in s
