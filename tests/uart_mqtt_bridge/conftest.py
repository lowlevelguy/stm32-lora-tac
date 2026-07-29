"""Shared pytest fixtures for the uart_mqtt_bridge test suite.

Layout decision: tests live under tests/uart_mqtt_bridge/ while the
production package lives under uart_mqtt_bridge/ (a sibling of tests/).
The bridge modules use flat imports like ``from lora_frame import ...``,
so for the imports to resolve under pytest the package directory must
appear on sys.path. The ``bridge_path`` fixture below inserts it once
per session (autouse) so individual test files need not repeat the
boilerplate.

The remaining fixtures are thin factories for the three frame types —
kept here because ``test_lora_frame.py`` and ``test_topic_map.py`` both
want well-formed telemetry / command / ack frames, and a single shared
construction site avoids drift in the default byte values.
"""

from collections.abc import Callable
from pathlib import Path
import sys

import pytest

# --------------------------------------------------------------------- sys.path
# Insert the bridge package directory into sys.path at conftest import
# time, before the ``from lora_frame import ...`` line below resolves.
# Doing this as a plain module-level side effect (rather than a fixture)
# is deliberate: pytest imports conftest.py before running any fixture,
# so a fixture-based sys.path shim would fire too late for the
# module-level imports the factory fixtures rely on. Locating the
# bridge dir from __file__ keeps the suite relocatable: from
# tests/uart_mqtt_bridge/conftest.py the bridge lives two directories
# up, then down into uart_mqtt_bridge/.
_BRIDGE_DIR = Path(__file__).resolve().parent.parent.parent / \
    "uart_mqtt_bridge"
_BRIDGE_DIR_STR = str(_BRIDGE_DIR)
if _BRIDGE_DIR_STR not in sys.path:
    sys.path.insert(0, _BRIDGE_DIR_STR)

from lora_frame import FrameType, LoraFrame  # noqa: E402


# ---------------------------------------------------------------- frame factories
@pytest.fixture
def frame_telemetry() -> Callable[..., LoraFrame]:
    """Build a telemetry frame (TypeID=0x01) with caller-overridable bytes.

    Defaults mirror the example used throughout the SRS ``LoRa Frame
    Format`` sheet rows: SourceID=1, DestID=0 (gateway), SensorID=0,
    SensorValue=0x1A, RSSI raw=0xCC (-> +4 dBm), SNR raw=0xFB (-> -5 dB).
    """
    def make(
        source: int = 1,
        dest: int = 0,
        sensor_id: int = 0x00,
        sensor_value: int = 0x1A,
        rssi_raw: int = 0xCC,
        snr_raw: int = 0xFB,
    ) -> LoraFrame:
        return LoraFrame(
            source_addr=source,
            dest_addr=dest,
            type_id=FrameType.TELEMETRY,
            data=bytes((sensor_id, sensor_value, rssi_raw, snr_raw)),
        )
    return make


@pytest.fixture
def frame_command() -> Callable[..., LoraFrame]:
    """Build a command frame via the production ``command_frame`` factory.

    Exercising the factory (not constructing a LoraFrame by hand) keeps
    the test honest about what the SUT actually produces, which is what
    caught the audit's silent ``source_addr`` disregard bug in the first
    place.
    """
    def make(dest: int = 1, actuator_id: int = 0, cmd: int = 1) -> LoraFrame:
        return LoraFrame.command_frame(
            dest_addr=dest, actuator_id=actuator_id, cmd=cmd,
        )
    return make


@pytest.fixture
def frame_ack() -> Callable[..., LoraFrame]:
    """Build an ACK frame (TypeID=0x03): ActuatorID | Status | 0x00 | 0x00."""
    def make(
        source: int = 1,
        dest: int = 0,
        actuator_id: int = 0,
        status: int = 0,
    ) -> LoraFrame:
        return LoraFrame(
            source_addr=source,
            dest_addr=dest,
            type_id=FrameType.ACK,
            data=bytes((actuator_id, status, 0x00, 0x00)),
        )
    return make
