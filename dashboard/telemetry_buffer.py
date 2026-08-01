"""Telemetry ring buffer — per-device 120 s sliding window.

Per handoff § Class decomposition: the dashboard keeps a rolling
window of the last ``RING_BUFFER_CAPACITY`` samples (24 @ 5 s cadence
= 120 s). The storage mechanic is a fixed-capacity ``deque`` so each
append is O(1) with zero reallocation after warmup — a ring buffer in
the deque sense. ``get_window()`` returns time-ordered numpy arrays for
``PlotWidget.plot().setData()``.

Threading: instances are owned by the GUI thread (DashboardWindow).
The MQTT worker thread NEVER touches a buffer; it emits a signal and
the GUI thread routes the payload through ``append()``. This keeps the
ring free of locks — only one thread mutates it.

ACK status is NOT part of the 24-sample telemetry channel. ACK is an
event-driven uplink (one frame per downlink command), not cadence-
driven, so it lives in a separate ``last_ack`` field updated by the
window when an ``+/ack`` message arrives.
"""

import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from config import RING_BUFFER_CAPACITY, TELEMETRY_STALE_S


@dataclass(slots=True, frozen=True)
class TelemetrySample:
    """One uplink snapshot, pushed through ``TelemetryBuffer.append``.

    ``timestamp`` is monotonic seconds from ``time.monotonic()`` so the
    x-axis is seconds-since-buffer-epoch and immune to wall-clock
    skew. ``sensor`` is the raw uint8 SensorValue (0..255). ``rssi`` is
    dBm (already unpacked from int16 BE). ``snr`` is dB (already
    unpacked from int8).
    """
    timestamp: float
    sensor: int
    rssi: int
    snr: int


@dataclass(slots=True)
class AckRecord:
    """Last-seen ACK for this device.

    ``actuator_id`` is the ActuatorID from the /ack payload (0x00 LED
    for this project). ``status`` is 0 (OK) or 1 (ERR) per
    lora_frame.ACK. ``timestamp`` is ``time.monotonic()`` so the
    ControlPage can decide whether a late ACK is still relevant.
    """
    actuator_id: int
    status: int
    timestamp: float


class TelemetryBuffer:
    """Per-device ring buffer: 120 s of sensor/rssi/snr + last ACK.

    Capacity is fixed at construction (config.RING_BUFFER_CAPACITY).
    Once full, each ``append`` evicts the oldest sample in O(1) — this
    is what "ring buffer" means in the deque sense. ``get_window``
    returns numpy arrays in the order samples were appended (oldest
    first), so pyqtgraph can plot them directly.
    """

    def __init__(self, capacity: int = RING_BUFFER_CAPACITY) -> None:
        self._capacity = capacity
        # Three deques + one timestamp deque — independent channels so
        # the plotter can pull just sensor (big chart) or just rssi/snr
        # (mini chart) without slicing a combined array.
        self._t: deque[float] = deque(maxlen=capacity)
        self._sensor: deque[int] = deque(maxlen=capacity)
        self._rssi: deque[int] = deque(maxlen=capacity)
        self._snr: deque[int] = deque(maxlen=capacity)
        # ACK is event-driven — separate field, not a deque channel.
        self._last_ack: AckRecord | None = None

    # ---------------------------------------------------------- ingest

    def append(self, sample: TelemetrySample) -> None:
        """Push one uplink snapshot. O(1); oldest auto-evicts when full."""
        self._t.append(sample.timestamp)
        self._sensor.append(sample.sensor)
        self._rssi.append(sample.rssi)
        self._snr.append(sample.snr)

    def record_ack(self, actuator_id: int, status: int) -> None:
        """Record the most recent ACK for this device. Replaces any
        prior ACK record — only the latest is relevant for the badge.
        """
        self._last_ack = AckRecord(
            actuator_id=actuator_id,
            status=status,
            timestamp=time.monotonic(),
        )

    @property
    def last_ack(self) -> AckRecord | None:
        return self._last_ack

    # ---------------------------------------------------------- readout

    def last(self) -> TelemetrySample | None:
        """Most recent sample, or None if the buffer is empty. Used by
        the MONITOR live-readout bar.
        """
        if not self._t:
            return None
        return TelemetrySample(
            timestamp=self._t[-1],
            sensor=self._sensor[-1],
            rssi=self._rssi[-1],
            snr=self._snr[-1],
        )

    def get_window(
        self,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Return time-ordered arrays for pyqtgraph setData().

        Returns ``(t, sensor, rssi, snr)`` as float32/int16/int8/int8
        numpy arrays. ``t`` is rebased to seconds-since-first-sample so
        the x-axis starts at 0. Empty buffer -> four empty arrays
        (pyqtgraph handles this via setData returning an empty curve).
        """
        if not self._t:
            return (
                np.empty(0, dtype=np.float32),
                np.empty(0, dtype=np.int16),
                np.empty(0, dtype=np.int16),
                np.empty(0, dtype=np.int8),
            )
        t0 = self._t[0]
        t_arr = np.fromiter(
            (t - t0 for t in self._t), dtype=np.float32, count=len(self._t),
        )
        # sensor and rssi share int16 width (sensor is 0..255 so int16
        # suffices and avoids a per-channel dtype dance).
        sensor_arr = np.fromiter(
            self._sensor, dtype=np.int16, count=len(self._sensor),
        )
        rssi_arr = np.fromiter(
            self._rssi, dtype=np.int16, count=len(self._rssi),
        )
        snr_arr = np.fromiter(
            self._snr, dtype=np.int8, count=len(self._snr),
        )
        return t_arr, sensor_arr, rssi_arr, snr_arr

    # ---------------------------------------------------------- staleness

    def stale(
        self, now: float | None = None,
        threshold_s: float = TELEMETRY_STALE_S,
    ) -> bool:
        """True if no sample arrived in the last ``threshold_s`` seconds.

        Per handoff § Wire-level event map: when stale, MONITOR fades
        the curve to @font-muted and the readout bar shows "—".
        ``now`` defaults to ``time.monotonic()`` so callers don't have
        to thread a clock through.
        """
        if not self._t:
            return True
        if now is None:
            now = time.monotonic()
        return (now - self._t[-1]) >= threshold_s

    # ---------------------------------------------------------- introspection

    def __len__(self) -> int:
        return len(self._t)

    @property
    def capacity(self) -> int:
        return self._capacity
