"""LoRa frame decode/encode — 8-byte fixed-length protocol.

Frame layout (per SRS-UART-01, LoRa Frame Format):
  Byte 0: SOF  (0xA5)
  Byte 1: SourceID
  Byte 2: DestID
  Byte 3: TypeID
  Byte 4: Data[0]
  Byte 5: Data[1]
  Byte 6: Data[2]
  Byte 7: Data[3]

TypeID encoding:
  0x01 = Telemetry  (Data[0]=SensorID, Data[1..3]=uint32 big-endian value)
  0x02 = Command    (Data[0]=ActuatorID, Data[1]=Cmd)
  0x03 = ACK        (Data[0]=ActuatorID, Data[1]=Status)

All multi-byte fields are big-endian.
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


@dataclass(slots=True)
class LoraFrame:
    sof: int
    source_addr: int
    dest_addr: int
    type_id: int
    data: bytes = field(default_factory=lambda: b"\x00\x00\x00\x00")

    def __post_init__(self):
        if self.sof != _SOF:
            raise ValueError(f"Invalid SOF: 0x{self.sof:02X}")
        if len(self.data) != 4:
            raise ValueError("data must be 4 bytes")

    @classmethod
    def decode(cls, raw: bytes) -> "LoraFrame":
        """Decode an 8-byte raw frame into a LoraFrame.

        Returns None if raw is not exactly 8 bytes or SOF != 0xA5.
        """
        if len(raw) != _FRAME_LENGTH or raw[0] != _SOF:
            return None
        sof, src, dst, tid, d0, d1, d2, d3 = struct.unpack(_FMT, raw)
        return cls(sof=sof, source_addr=src, dest_addr=dst, type_id=tid,
                   data=bytes((d0, d1, d2, d3)))

    def encode(self) -> bytes:
        """Encode this frame back to 8 raw bytes."""
        return struct.pack(_FMT, self.sof, self.source_addr, self.dest_addr,
                           self.type_id, *self.data)

    @property
    def telemetry_sensor_id(self) -> int | None:
        if self.type_id != FrameType.TELEMETRY:
            return None
        return self.data[0]

    @property
    def telemetry_value(self) -> int | None:
        if self.type_id != FrameType.TELEMETRY:
            return None
        return self.data[1]

    @property
    def command_actuator_id(self) -> int | None:
        if self.type_id != FrameType.COMMAND:
            return None
        return self.data[0]

    @property
    def command_value(self) -> int | None:
        if self.type_id != FrameType.COMMAND:
            return None
        return self.data[1]

    @property
    def ack_actuator_id(self) -> int | None:
        if self.type_id != FrameType.ACK:
            return None
        return self.data[0]

    @property
    def ack_status(self) -> int | None:
        if self.type_id != FrameType.ACK:
            return None
        return self.data[1]

    @property
    def rssi_value(self) -> int | None:
        return self.data[2]

    @property
    def snr_value(self) -> int | None:
        return self.data[3]

    def __str__(self) -> str:
        data_hex = " ".join(f"{b:02X}" for b in self.data)
        return (f"SOF=0xA5 SRC=0x{self.source_addr:02X} "
                f"DST=0x{self.dest_addr:02X} TID=0x{self.type_id:02X} "
                f"DATA=[{data_hex}]")