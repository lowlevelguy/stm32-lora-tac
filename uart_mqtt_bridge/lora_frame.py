"""LoRa frame codec — 8-byte fixed-length protocol.

Frame layout (SRS-UART-01, "LoRa Frame Format" sheet):

    Byte 0  SOF         0xA5 (fixed)
    Byte 1  SourceID    sender ID (0x01 = EndDev1 / Gateway)
    Byte 2  DestID      recipient ID
    Byte 3  TypeID      0x01 = Telemetry, 0x02 = Command, 0x03 = ACK
    Byte 4  Data[0]
    Byte 5  Data[1]
    Byte 6  Data[2]
    Byte 7  Data[3]

Telemetry (TypeID=0x01) — per MQTT Topic Map sheet (latest revision):

    Data[0]   SensorID      (1 byte; 0x00 for this project)
    Data[1]   SensorValue   (1 byte; e.g. button press count 0..255)
    Data[2]   RSSI          (gateway-injected, SRS-GW-05: RSSI_dBm + 200)
    Data[3]   SNR           (gateway-injected, int8 signed)

This definition removes the earlier ambiguity between sensor-value LSBs and
RSSI/SNR: they are independent bytes, not overlapping.

Command (TypeID=0x02):
    Data[0]   ActuatorID
    Data[1]   Cmd (0x00 = OFF, 0x01 = ON)
    Data[2..3]= 0x00 reserved

ACK (TypeID=0x03):
    Data[0]   ActuatorID
    Data[1]   Status (0x00 = OK, 0x01 = ERR)
    Data[2..3]= 0x00 reserved
"""

from dataclasses import dataclass, field
from enum import IntEnum
import struct

_FRAME_LENGTH = 8
_SOF = 0xA5
_FMT = ">BBBBBBBB"


class FrameType(IntEnum):
    TELEMETRY = 0x01
    COMMAND = 0x02
    ACK = 0x03


# Re-exports for callers that want the raw constants.
FRAME_LENGTH = _FRAME_LENGTH
SOF = _SOF


@dataclass(slots=True)
class LoraFrame:
    """Strongly-typed 8-byte LoRa/UART frame."""

    source_addr: int
    dest_addr: int
    type_id: int
    data: bytes = field(default_factory=lambda: b"\x00\x00\x00\x00")

    def __post_init__(self):
        if len(self.data) != 4:
            raise ValueError("data must be 4 bytes")
        if not (0 <= self.source_addr <= 0xFF):
            raise ValueError("source_addr out of range")
        if not (0 <= self.dest_addr <= 0xFF):
            raise ValueError("dest_addr out of range")
        if not (0 <= self.type_id <= 0xFF):
            raise ValueError("type_id out of range")

    # ----------------------------------------------------- construction

    @classmethod
    def decode(cls, raw: bytes) -> "LoraFrame | None":
        """Decode 8 raw bytes into a LoraFrame.

        Returns None if raw is not exactly 8 bytes or SOF != 0xA5.
        """
        if len(raw) != _FRAME_LENGTH or raw[0] != _SOF:
            return None
        _sof, src, dst, tid, d0, d1, d2, d3 = struct.unpack(_FMT, raw)
        return cls(
            source_addr=src,
            dest_addr=dst,
            type_id=tid,
            data=bytes((d0, d1, d2, d3)),
        )

    def encode(self) -> bytes:
        """Encode this frame back to 8 raw bytes (including SOF)."""
        return struct.pack(
            _FMT, _SOF, self.source_addr, self.dest_addr, self.type_id, *self.data
        )

    # ------------------------------------------- telemetry typed views

    @property
    def telemetry_sensor_id(self) -> int | None:
        """Telemetry Data[0] — SensorID (0x00 for this project)."""
        if self.type_id != FrameType.TELEMETRY:
            return None
        return self.data[0]

    @property
    def telemetry_sensor_value(self) -> int | None:
        """Telemetry Data[1] — SensorValue as unsigned uint8 (0..255)."""
        if self.type_id != FrameType.TELEMETRY:
            return None
        return self.data[1]

    @property
    def telemetry_sensor_payload(self) -> bytes | None:
        """2-byte MQTT payload: SensorID || SensorValue (Data[0..1])."""
        if self.type_id != FrameType.TELEMETRY:
            return None
        return bytes((self.data[0], self.data[1]))

    @property
    def telemetry_rssi_raw(self) -> int | None:
        """Raw RSSI byte (Data[2]) as unsigned 0..255."""
        if self.type_id != FrameType.TELEMETRY:
            return None
        return self.data[2]

    @property
    def telemetry_rssi_dbm(self) -> int | None:
        """RSSI in dBm per SRS-GW-05 offset convention (raw - 200)."""
        raw = self.telemetry_rssi_raw
        if raw is None:
            return None
        return raw - 200

    @property
    def telemetry_snr_raw(self) -> int | None:
        """Raw SNR byte (Data[3]) as unsigned 0..255."""
        if self.type_id != FrameType.TELEMETRY:
            return None
        return self.data[3]

    @property
    def telemetry_snr_db(self) -> int | None:
        """SNR in dB interpreted as signed int8 (LoRa typical range -20..+12)."""
        raw = self.telemetry_snr_raw
        if raw is None:
            return None
        return raw - 256 if raw >= 128 else raw

    # Future: command and ack typed properties will be added when
    # downlink and ACK handling are implemented.

    # ----------------------------------------------------- debug

    def __str__(self) -> str:
        data_hex = " ".join(f"{b:02X}" for b in self.data)
        return (
            f"SOF=0xA5 SRC=0x{self.source_addr:02X} DST=0x{self.dest_addr:02X} "
            f"TID=0x{self.type_id:02X} DATA=[{data_hex}]"
        )
