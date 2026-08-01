"""MONITOR page — Tab 0: telemetry chart + readout bar + mini plot + ACK badge.

Handoff § Section rhythm / Tab 0. The page is a passive observer: it
reads the ``TelemetryBuffer`` owned by ``DashboardWindow`` and mutates
only its own widgets. Buffer storage lives on the window so all three
pages share one source of truth regardless of which is visible.

Rendering cadence: the window owns the 1 Hz ``QTimer`` and calls
``on_timer_tick()`` here, which reads the buffer's current window and
calls ``PlotWidget.plot().setData()``. No per-message repaint — a
burst of 3 pubs at the 5 s cadence each lands in the ring buffer and is
rendered once on the next tick. This is the handoff's callback-latency
discipline: ``on_message`` runs on the worker thread in <1 ms because
it never touches a QPainter.

Pen colors are QColor literals anchored to the ``tokens.qss`` hex
values; pyqtgraph's PlotWidget does not consume QSS so we keep a
single ``_PEN`` lookup and a comment cross-referencing the token name.
"""
import struct

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from device_selector import DeviceSelector
from name_registry import NameRegistry
from telemetry_buffer import TelemetryBuffer


# Pen colors — hex anchored to tokens.qss so the chart palette matches
# the QSS cascade without the chart itself reading QSS (pyqtgraph uses
# QPen/QColor directly). Edit tokens.qss first; update these to match.
_PEN_SENSOR = QColor("#3ba8b8")   # @accent-cyan — sensor trace, big chart
_PEN_RSSI = QColor("#3ba8b8")     # @accent-cyan — RSSI trace, mini chart
_PEN_SNR = QColor("#4a9a6a")      # @accent-green — SNR trace, mini chart
_PEN_STALE = QColor("#6a6c6e")    # @font-muted — faded curve when stale
_PEN_AXIS = QColor("#6a6c6e")    # @font-muted — axis ticks
_PEN_GRID = QColor("#2a2b2e")    # @border — grid lines


class MonitorPage(QWidget):
    """Tab 0 — real-time telemetry display + ACK status badge.

    Holds a *reference* to the window's per-device buffer map (not a
    copy) so plotting always reflects the latest ring-buffer state.
    The active device starts at -1 (no device selected — the selector
    is disabled until a /sensor topic announces one); the window's
    discovery flow updates it via set_active_device().
    """

    # Emitted when an /ack message arrives on this page so the window
    # can cancel ControlPage's 2 s verify timer. Carries (actuator_id,
    # status, enddev_id) so the window can match against the last sent
    # command. Only emitted for the active device's buffer to keep the
    # signal bus narrow.
    ack_received = pyqtSignal(int, int, int)

    def __init__(
        self,
        buffers: dict[int, TelemetryBuffer],
        active_enddev: int,
        registry: "NameRegistry",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._buffers = buffers
        self._active_enddev = active_enddev
        self._registry = registry

        self._build_ui()
        self._init_plots()

    # ----------------------------------------------------------- UI build

    def _build_ui(self) -> None:
        """Construct the vertical layout: section head, EndDevice
        selector row, telemetry chart, live readout bar, bottom row
        (mini chart + ACK badge).
        """
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        # Section head — handoff § Font pairing: 11pt bold tracked.
        head = QLabel("TELEMETRY MONITOR", self)
        head.setObjectName("sectionHead")
        root.addWidget(head)

        # EndDevice selector row (Change 1, 2026-07-31). Placeholder
        # shows until a real +/sensor topic announces a device.
        dev_row = QHBoxLayout()
        dev_row.setSpacing(12)
        dev_lbl = QLabel("EndDevice:", self)
        dev_lbl.setObjectName("readout")
        self._device_selector = DeviceSelector(self._registry, self)
        self._device_selector.currentEndDevChanged.connect(
            self.set_active_device,
        )
        dev_row.addWidget(dev_lbl)
        dev_row.addWidget(self._device_selector.combo(), stretch=1)
        dev_row.addStretch()
        root.addLayout(dev_row)

        # Big telemetry chart (Region C: 648 x ~260px).
        self._telemetry_plot = pg.PlotWidget(self)
        self._telemetry_plot.setObjectName("telemetryPlot")
        self._telemetry_plot.setMinimumHeight(260)
        self._telemetry_plot.setLabel("left", "Sensor value")
        self._telemetry_plot.setLabel("bottom", "Seconds (rolling 120 s)")
        self._telemetry_plot.showGrid(x=True, y=True, alpha=0.15)
        self._telemetry_plot.setToolTipDuration(800)
        root.addWidget(self._telemetry_plot, stretch=1)

        # Live readout bar (Region D: 4 x QLabel pairs).
        self._readout_layout = QHBoxLayout()
        self._readout_layout.setSpacing(24)
        self._lbl_sensor = self._make_readout_pair("Sensor", "—")
        self._lbl_rssi = self._make_readout_pair("RSSI", "—")
        self._lbl_snr = self._make_readout_pair("SNR", "—")
        self._lbl_last = self._make_readout_pair("Last", "—")
        root.addLayout(self._readout_layout)

        # Bottom row: mini signal-integrity chart + ACK badge side by side.
        bottom = QHBoxLayout()
        bottom.setSpacing(16)

        self._mini_plot = pg.PlotWidget(self)
        self._mini_plot.setObjectName("miniPlot")
        self._mini_plot.setMinimumHeight(120)
        self._mini_plot.setLabel("left", "RSSI dBm")
        self._mini_plot.setLabel("right", "SNR dB")
        self._mini_plot.showGrid(x=True, y=True, alpha=0.15)
        bottom.addWidget(self._mini_plot, stretch=2)

        self._ack_label = QLabel("ACK: —", self)
        self._ack_label.setObjectName("ackBadge")
        # QSS attribute selector drives state-driven styling (state="ok"
        # etc.); the property swap is what animates the badge color.
        self._ack_label.setProperty("state", "none")
        self._ack_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ack_label.setMinimumWidth(160)
        bottom.addWidget(self._ack_label, stretch=1)

        root.addLayout(bottom)

    def _make_readout_pair(self, label: str, value: str) -> QLabel:
        """Build a (label / value) pair in the readout bar; return the
        value QLabel so the tick handler can update it.
        """
        cell = QVBoxLayout()
        cell.setSpacing(2)
        lbl = QLabel(label, self)
        lbl.setObjectName("readout")
        val = QLabel(value, self)
        val.setObjectName("readoutValue")
        cell.addWidget(lbl)
        cell.addWidget(val)
        self._readout_layout.addLayout(cell)
        return val

    def _init_plots(self) -> None:
        """Configure axes, pens, and the empty initial curves.

        ``PlotWidget.plot()`` returns the live ``PlotDataItem`` (the
        curve). We hold it on the instance so the 1 Hz tick can call
        ``curve.setData()`` directly — no reaching into the plot
        item's dataItems list by index (that path is fragile across
        pyqtgraph revisions and obscures which curve we touch).
        """
        # Big chart: sensor value, accent-cyan 2px solid.
        self._sensor_curve = self._telemetry_plot.plot(
            [], [], pen=pg.mkPen(color=_PEN_SENSOR, width=2),
            name="sensor",
        )
        self._telemetry_plot.setYRange(0, 255)
        self._telemetry_plot.setXRange(0, 120)
        self._style_plot(self._telemetry_plot)

        # Mini chart: two independent y-axes (RSSI left, SNR right).
        self._rssi_curve = self._mini_plot.plot(
            [], [], pen=pg.mkPen(color=_PEN_RSSI, width=2),
            name="rssi",
        )
        self._snr_axis = pg.AxisItem("right")
        self._mini_plot.setAxisItems({"right": self._snr_axis})
        self._snr_curve = self._mini_plot.plot(
            [], [], pen=pg.mkPen(color=_PEN_SNR, width=2),
            name="snr",
        )
        self._style_plot(self._mini_plot)

    @staticmethod
    def _style_plot(plot: pg.PlotWidget) -> None:
        """Apply shared axis/grid palette so the QSS-dependent border +
        bg sit alongside muted axis ticks per tokens.qss.
        """
        for axis_name in ("left", "bottom"):
            axis = plot.getAxis(axis_name)
            axis.setPen(pg.mkPen(color=_PEN_AXIS, width=1))
            axis.setTextPen(pg.mkPen(color=_PEN_AXIS))
        plot.setBackground(None)
        plot.getViewBox().setBackgroundColor(QColor(0, 0, 0, 0))

    # ------------------------------------------------------- public slots

    def set_active_device(self, enddev_id: int) -> None:
        """Switch which device's buffer drives the MONITOR readouts.

        ``enddev_id == -1`` is the DeviceSelector's "no devices
        available" sentinel; in that case we don't update ``_active_enddev``
        (no real device to point at), but still trigger a tick so the
        readout bar shows the stale markers.
        """
        if enddev_id == -1:
            self.on_timer_tick()
            return
        self._active_enddev = enddev_id
        # Immediate refresh so the readout bar reflects the new device
        # before the next 1 Hz tick fires.
        self.on_timer_tick()

    def on_timer_tick(self) -> None:
        """1 Hz sweep: read the active buffer's current window, push to
        pyqtgraph setData, refresh the readout bar, refresh mini chart.
        No exception wrapper per user decision 2026-07-30 — if
        pyqtgraph raises, it surfaces as a real bug rather than
        silently degrading to a text-only mode.
        """
        buf = self._buffers.get(self._active_enddev)
        if buf is None:
            # No telemetry from this device yet — keep curves empty and
            # show muted "—" on every readout.
            self._sensor_curve.setData([], [])
            self._rssi_curve.setData([], [])
            self._snr_curve.setData([], [])
            self._set_readouts_stale()
            return

        t, sensor, rssi, snr = buf.get_window()
        stale = buf.stale()

        pen_sensor = pg.mkPen(
            color=_PEN_STALE if stale else _PEN_SENSOR, width=2,
        )
        pen_rssi = pg.mkPen(
            color=_PEN_STALE if stale else _PEN_RSSI, width=2,
        )
        pen_snr = pg.mkPen(
            color=_PEN_STALE if stale else _PEN_SNR, width=2,
        )

        # Big chart: sensor trace over seconds-since-buffer-epoch.
        self._sensor_curve.setData(t, sensor, pen=pen_sensor)

        # Mini chart: RSSI (left axis), SNR (right axis). pyqtgraph plots
        # share one x; the right AxisItem was wired in _init_plots.
        self._rssi_curve.setData(t, rssi, pen=pen_rssi)
        self._snr_curve.setData(t, snr, pen=pen_snr)

        if stale:
            self._set_readouts_stale()
        else:
            last = buf.last()
            if last is None:
                # Defensive: stale() returned False but the buffer has
                # no samples — shouldn't happen, but don't crash the
                # readout bar over a state-derivation edge case.
                self._set_readouts_stale()
            else:
                self._lbl_sensor.setText(f"0x{last.sensor:02X}")
                self._lbl_rssi.setText(f" {last.rssi} dBm")
                self._lbl_snr.setText(f" {last.snr} dB")
                self._lbl_last.setText("")
        # Stale markers via QSS class swap on the value labels.
        for lbl in (
            self._lbl_sensor, self._lbl_rssi,
            self._lbl_snr, self._lbl_last,
        ):
            lbl.setProperty("stale", stale)
            self._polish(lbl)

    # ---------------------------------------------------- ACK badge update

    def update_ack(self, status: int, actuator_id: int) -> None:
        """Push a fresh ACK record into the badge. status: 0=OK, 1=ERR.

        Called by the window when an /ack message arrives for the
        active device's buffer. Swaps the QSS attribute selector
        (``state="ok"`` / ``state="err"``) and reapplies the stylesheet
        so the visual state changes live without a repaint hack.
        """
        if status == 0x00:
            self._ack_label.setText("ACK: OK")
            self._ack_label.setProperty("state", "ok")
        elif status == 0x01:
            self._ack_label.setText("ACK: ERR")
            self._ack_label.setProperty("state", "err")
        else:
            self._ack_label.setText(f"ACK: ?0x{status:02X}")
            self._ack_label.setProperty("state", "err")
        self._polish(self._ack_label)

    # ----------------------------------------------------------- helpers

    @staticmethod
    def _polish(widget: QWidget) -> None:
        """Re-apply QSS after a dynamic property change (state="ok",
        stale=True, etc.). ``QWidget.style()`` is non-null inside a
        QApplication; the guard satisfies the type-check and is a
        no-op at runtime.
        """
        style = widget.style()
        if style is None:
            return
        style.unpolish(widget)
        style.polish(widget)

    def _set_readouts_stale(self) -> None:
        """Set every readout value to the stale marker "—"."""
        for lbl in (
            self._lbl_sensor, self._lbl_rssi,
            self._lbl_snr, self._lbl_last,
        ):
            lbl.setText("—")
            lbl.setProperty("stale", True)
            self._polish(lbl)
