"""Dashboard main window — left rail + stacked pages + status strip.

Handoff § Macrostructure layout and § Class decomposition. The window
owns the per-device ``TelemetryBuffer`` map, the shared ``NameRegistry``,
the 1 Hz ``QTimer``, and the routing of cross-thread signals from
``MqttWorker`` to the four pages. It is the single shared state locus;
pages are stateless observers that read buffers + the registry the
window hands them.

Layout (changes 2026-07-31)
---------------------------
The rail is now a ``QSplitter`` (user requested resizable) — initial
width ``RAIL_WIDTH_PX`` (100 px), clamped to ``RAIL_MIN/MAX_WIDTH_PX``.
The handoff's "no QSplitter" rule yields here per user instruction.
The ALIASES page is the 4th nav entry.

Signal flow (handoff § Signal/slot contract)
`````````````````````````````````````````````
    MqttWorker ──message_received(id,channel,payload,raw)──> this
        └─> decode payload, append/record_ack to per-device buffer
        └─> register device + sensor in NameRegistry (Change 1 + Change 4)
        └─> forward to active page's handle_* slot
        └─> forward to debug page's log_rx
    MqttWorker ──broker_state_changed(state,rc)──> this
        └─> status strip broker label, debug page set_broker_state
    ControlPage ──publish_requested(topic,payload)──> this
        └─> MqttWorker.publish (cross-thread via Qt signal)
        └─> debug page log_tx
    1 Hz QTimer ──timeout──> MonitorPage.on_timer_tick
"""
import struct
import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QWidget,
)

from config import (
    MQTT_BROKER_HOST,
    MQTT_BROKER_PORT,
    RAIL_MAX_WIDTH_PX,
    RAIL_MIN_WIDTH_PX,
    RAIL_WIDTH_PX,
    TIMER_TICK_MS,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
)
from aliases_page import AliasesPage
from control_page import ControlPage
from debug_page import DebugPage
from monitor_page import MonitorPage
from mqtt_worker import MqttWorker
from name_registry import NameRegistry
from telemetry_buffer import TelemetryBuffer, TelemetrySample


# Nav rail entry text. The rail shows the four co-primary tasks as
# label-only items — no icons, no nested cards. The 4th entry
# (ALIASES) was added 2026-07-31 per user Change 4.
_NAV_ENTRIES = (
    ("MONITOR", 0),
    ("CONTROL", 1),
    ("DEBUG", 2),
    ("ALIASES", 3),
)


class DashboardWindow(QMainWindow):
    """Main window — owns buffers, pages, worker, status strip, timer.

    Cross-thread signal contract honored explicitly: a single
    ``MqttWorker`` instance emits ``message_received`` and
    ``broker_state_changed``; this window routes them. ControlPage's
    ``publish_requested`` is forwarded to the worker via the worker's
    ``publish`` slot. No widget method is ever called from the worker
    thread (handoff § MQTT threading model).
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LoRa Dashboard")
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)

        # Shared name registry. Mutations here fire observers that
        # refresh the device selectors on Monitor/Control pages + the
        # whole ALIASES page (Change 4, 2026-07-31). Intentionally NOT
        # seeded: per user change 2026-07-31 the EndDevice dropdowns
        # start empty + disabled ("(No End Devices Detected)") until a
        # real +/sensor topic announces a device.
        self._registry = NameRegistry()

        # Per-device buffers. Lazily created on first frame from each
        # device id. Owned by this thread (GUI) only.
        self._buffers: dict[int, TelemetryBuffer] = {}
        # -1 = no device selected yet (selectors are disabled until
        # the first /sensor arrives). Kept in sync with the MONITOR
        # page's selector via _on_active_enddev_changed.
        self._active_enddev: int = -1

        # Pages constructed with shared buffers + registry; they hold
        # references, not copies, so the 1 Hz sweep always reads the
        # latest state.
        self._monitor_page = MonitorPage(
            self._buffers,
            active_enddev=self._active_enddev,
            registry=self._registry,
            parent=self,
        )
        self._control_page = ControlPage(
            registry=self._registry,
            enddev_id=self._active_enddev,
            parent=self,
        )
        self._debug_page = DebugPage(parent=self)
        self._aliases_page = AliasesPage(self._registry, parent=self)

        self._build_layout()

        # Frames-per-minute counter for the status strip.
        self._frames_received: int = 0
        self._frames_per_min: int = 0
        self._frames_window_start: float = time.monotonic()
        self._last_lag_ms: int | None = None

        # 1 Hz tick for page refresh + frames/min bookkeeping.
        self._timer = QTimer(self)
        self._timer.setInterval(TIMER_TICK_MS)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

        # MQTT worker (QThread). Constructed late so a constructor
        # failure during UI build does not strand a started thread.
        self._worker = MqttWorker(self)
        self._wire_signals()
        self._worker.start()

    # ----------------------------------------------------------- UI build

    def _build_layout(self) -> None:
        """Horizontal layout: resizable nav rail + QStackedWidget.

        The rail is wrapped in a QSplitter (Change 5, 2026-07-31) so the
        user can widen/narrow it. Initial size is RAIL_WIDTH_PX; the
        splitter clamps width to RAIL_MIN/MAX_WIDTH_PX via child
        ``setMinimumWidth`` + the splitter's ``setStretchFactor`` keeps
        the stack the dominant panel.
        """
        central = QWidget(self)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Splitter hosts: nav rail (index 0) + content stack (index 1).
        splitter = QSplitter(Qt.Orientation.Horizontal, central)
        splitter.setHandleWidth(4)
        splitter.setChildrenCollapsible(False)
        outer.addWidget(splitter)

        # Left rail — handoff § Region A. No longer fixed: the splitter
        # owns its width but we set min/max so resize stays sane.
        self._nav = QListWidget(splitter)
        self._nav.setObjectName("navRail")
        self._nav.setMinimumWidth(RAIL_MIN_WIDTH_PX)
        self._nav.setMaximumWidth(RAIL_MAX_WIDTH_PX)
        for label, _index in _NAV_ENTRIES:
            item = QListWidgetItem(label, self._nav)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._nav.addItem(item)
        splitter.addWidget(self._nav)

        # Stacked content — index 0=monitor, 1=control, 2=debug,
        # 3=aliases.
        self._stack = QStackedWidget(splitter)
        self._stack.addWidget(self._monitor_page)   # index 0
        self._stack.addWidget(self._control_page)   # index 1
        self._stack.addWidget(self._debug_page)     # index 2
        self._stack.addWidget(self._aliases_page)   # index 3
        splitter.addWidget(self._stack)

        # MONITOR is the default landing screen. Wire + select AFTER
        # the stack exists: setCurrentRow fires currentRowChanged
        # synchronously, and _on_nav_changed reads self._stack.
        self._nav.currentRowChanged.connect(self._on_nav_changed)
        self._nav.setCurrentRow(0)

        # Force the initial rail size after widgets are added (the
        # splitter's default is to share sizes evenly, which would
        # give the rail half the window).
        splitter.setSizes([RAIL_WIDTH_PX, WINDOW_WIDTH - RAIL_WIDTH_PX])
        splitter.setStretchFactor(0, 0)  # rail doesn't stretch
        splitter.setStretchFactor(1, 1)  # content absorbs idle space
        self._splitter = splitter

        self.setCentralWidget(central)

        # Status strip — handoff § Region H: broker state + lag + fpm +
        # wall clock. QStatusBar shows messages but we install it as a
        # permanent widget row via addPermanentWidget so each cell is
        # independently addressable.
        self._status = QStatusBar(self)
        self.setStatusBar(self._status)
        self._status_broker = QLabel(
            f"{MQTT_BROKER_HOST}:{MQTT_BROKER_PORT} \u25cf waiting", self,
        )
        self._status_broker.setObjectName("brokerState")
        self._status_broker.setProperty("state", "disconnected")
        self._status_lag = QLabel("lag: \u2014", self)
        self._status_frames = QLabel("frames: 0/min", self)
        self._status_clock = QLabel("", self)
        for w in (
            self._status_broker, self._status_lag,
            self._status_frames, self._status_clock,
        ):
            self._status.addPermanentWidget(w)

    # ----------------------------------------------------- signal wiring

    def _wire_signals(self) -> None:
        """Connect cross-thread + page-level signals. Done once after
        both worker and pages exist.
        """
        # Worker -> window (GUI thread slots).
        self._worker.message_received.connect(self._on_message_received)
        self._worker.broker_state_changed.connect(self._on_broker_state)

        # ControlPage -> window -> worker. The slot lives on
        # MqttWorker (called via signal-link), so the publish() call
        # drives on the worker thread when the GUI thread emits here.
        self._control_page.publish_requested.connect(self._on_publish_request)

        # MONITOR page selector -> window. Keeps _active_enddev in
        # sync with what the user is watching so ACK routing
        # (line ~315) targets the right device's badge + control form.
        self._monitor_page._device_selector.currentEndDevChanged.connect(
            self._on_active_enddev_changed,
        )

    def _on_active_enddev_changed(self, enddev_id: int) -> None:
        """Track the MONITOR page's active EndDevice for ACK routing.
        Accepts -1 (empty selector) — the -1 never equals a real
        enddev_id, so ACKs are simply not routed while nothing is
        selected.
        """
        self._active_enddev = enddev_id

    # ------------------------------------------------------- worker slots

    def _on_message_received(
        self, enddev_id: int, channel: str, payload: bytes, topic_raw: str,
    ) -> None:
        """GUI-thread slot: decode payload, route to buffer + pages.

        Per handoff § Wire-level event map: all ``payload`` parsing
        happens HERE (on the GUI thread) — the worker hands us raw
        bytes. We unconditionally push to the per-device buffer so the
        MONITOR reads the right data even if it was the CONTROL tab
        the user had visible when the message arrived.
        """
        # Log every RX into the debug page regardless of current tab.
        self._debug_page.log_rx(topic_raw, payload)

        # Lazily create the per-device buffer; future-proofing for the
        # wildcard subscription decision (handoff Q4).
        buf = self._buffers.get(enddev_id)
        if buf is None:
            buf = TelemetryBuffer()
            self._buffers[enddev_id] = buf

        if channel == "sensor":
            # 2 bytes: SensorID | SensorValue. Per SRS Topic Map row;
            # SensorID discarded (only one sensor in this project,
            # but the registry now keys names by (dev, sensor) tuple
            # so future multi-sensor EndDevices are supported).
            if len(payload) >= 2:
                sensor_id = payload[0]
                # Change 1 + Change 4 (2026-07-31): register the device
                # + learn the sensor in the registry so the dropdowns
                # + ALIASES tab reflect the newly-seen equipment.
                self._registry.register_device(enddev_id)
                # set_sensor_alias with None registers the (dev, sid)
                # key so the ALIASES page's sensor list shows it; the
                # actual alias stays None (default display applies).
                self._registry.set_sensor_alias(enddev_id, sensor_id, None)
                # Carry the most recent rssi/snr into the new sample so
                # the ring does not regress to 0 when rssi/snr arrive
                # later in the cycle (handoff § timestamp coalescing).
                prev = buf.last()
                prev_rssi = prev.rssi if prev is not None else 0
                prev_snr = prev.snr if prev is not None else 0
                buf.append(TelemetrySample(
                    timestamp=time.monotonic(),
                    sensor=payload[1],
                    rssi=prev_rssi,
                    snr=prev_snr,
                ))
                # Lag bookkeeping: when the frame arrived vs now is
                # trivially small (asynchronous), so we use monotonic
                # delta as processed latency. The "lag" display is
                # informational, not a true NTP-style estimator.
                self._frames_received += 1

        elif channel == "rssi":
            # 2 bytes int16 signed big-endian. Update the most-recent
            # telemetry sample's rssi field by re-appending a corrected
            # sample so the ring always reflects the freshest state.
            if len(payload) >= 2:
                rssi = struct.unpack(">h", payload[:2])[0]
                self._merge_into_latest(enddev_id, rssi=rssi)

        elif channel == "snr":
            # 1 byte int8 signed. Same merge trick as rssi.
            if len(payload) >= 1:
                snr = struct.unpack("b", payload[:1])[0]
                self._merge_into_latest(enddev_id, snr=snr)

        elif channel == "ack":
            # 2 bytes ActuatorID | Status. Event-driven — does NOT go
            # through the telemetry ring. Routes to MonitorPage badge
            # + (if it matches an in-flight command) ControlPage.
            if len(payload) >= 2:
                actuator_id = payload[0]
                status = payload[1]
                buf.record_ack(actuator_id=actuator_id, status=status)
                if enddev_id == self._active_enddev:
                    self._monitor_page.update_ack(
                        status=status, actuator_id=actuator_id,
                    )
                    # Forward to control page so it can cancel its
                    # verify timer if the actuator matches the command
                    # currently in flight.
                    self._control_page.handle_ack(
                        actuator_id=actuator_id, status=status,
                    )

    def _merge_into_latest(
        self, enddev_id: int,
        rssi: int | None = None, snr: int | None = None,
    ) -> None:
        """Apply a late-arriving rssi/snr value to the most recent
        sample. Telemetry arrives as 3 separate MQTT messages within
        the same 5 s cycle; we coalesce them into one TelemetrySample
        by patching the most recent entry. If no sample exists yet
        (rssi arrived before sensor — edge case), synthesise a 0-sensor
        sample to anchor the cycle. This avoids three independent
        deques that would re-align out of phase under packet loss.
        """
        buf = self._buffers[enddev_id]
        last = buf.last()
        if last is None:
            # Synthesise an anchor sample with sensor=0 so the buffer
            # has SOMETHING to plot until the sensor message lands.
            buf.append(TelemetrySample(
                timestamp=time.monotonic(),
                sensor=0,
                rssi=rssi if rssi is not None else 0,
                snr=snr if snr is not None else 0,
            ))
            return
        # Re-add a corrected sample. The deque is ring-buffered; the
        # replaced sample will age out as a normal sample would.
        buf.append(TelemetrySample(
            timestamp=last.timestamp,
            sensor=last.sensor,
            rssi=rssi if rssi is not None else last.rssi,
            snr=snr if snr is not None else last.snr,
        ))

    def _on_broker_state(self, state: str, reason_code: int | None) -> None:
        """GUI-thread slot: mirror broker state into the status strip
        + debug page label. MqttWorker coerces paho's ReasonCode to an
        int before emitting (see _reason_code_to_int); for the
        "connected" state it emits None (no rc to report).
        """
        # Belt-and-braces: if a future regression ever leaks a
        # ReasonCode here, log it rather than crash the strip update.
        rc: int | None
        if reason_code is None:
            rc = None
        elif isinstance(reason_code, int):
            rc = reason_code
        else:
            rc = None
        # Status strip broker label.
        host = f"{MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}"
        dot = {
            "connected": "\u25cf connected",
            "disconnected": "\u25cf disconnected",
            "failed": f"\u25cf failed (rc={rc})",
        }.get(state, state)
        self._status_broker.setText(f"{host} {dot}")
        self._status_broker.setProperty("state", state)
        style = self._status_broker.style()
        if style is not None:
            style.unpolish(self._status_broker)
            style.polish(self._status_broker)
        # Debug page.
        self._debug_page.set_broker_state(state, rc)

    def _on_publish_request(self, topic: str, payload: bytes) -> None:
        """ControlPage wants to publish — forward to the worker's slot
        (cross-thread via Qt signal) and log the TX into the debug page.
        """
        self._debug_page.log_tx(topic, payload)
        # The worker's publish slot runs on the worker thread (handoff
        # § MQTT threading model). Qt marshals the call across thread
        # boundaries for connected signal/slot pairs.
        self._worker.publish(topic, payload)

    # --------------------------------------------------------- nav rail

    def _on_nav_changed(self, row: int) -> None:
        """Swap the QStackedWidget page on rail-item change."""
        if 0 <= row < self._stack.count():
            self._stack.setCurrentIndex(row)

    # ------------------------------------------------------------- tick

    def _on_tick(self) -> None:
        """1 Hz: refresh the active page's plots + readouts + bookkeeping.

        Only the MONITOR page currently consumes ticks; CONTROL and
        DEBUG are event-driven. The tick also recomputes frames/min
        (rolling 60-second average via count-since-last-reset) and
        updates the wall-clock label.
        """
        self._monitor_page.on_timer_tick()

        # Frames-per-minute rolling count: at each tick, scale the count
        # since this tick-window started. Reset every 60 s.
        now = time.monotonic()
        elapsed = now - self._frames_window_start
        if elapsed >= 60.0:
            # Window closed — move the window forward by 60 s and
            # reset the count.
            self._frames_per_min = self._frames_received
            self._frames_received = 0
            self._frames_window_start = now
        self._status_frames.setText(f"frames: {self._frames_per_min}/min")
        self._status_clock.setText(time.strftime("%H:%M:%S"))

    # ---------------------------------------------------------- show

    def showEvent(self, a0) -> None:
        """First-time show: enforce the intended splitter sizes.

        ``QSplitter.setSizes`` called in ``_build_layout`` (before the
        splitter has real geometry) gets overridden once the window
        lays out. Defer the setSizes call to the next event-loop spin
        (singleShot 0 ms) so it lands after Qt has computed the actual
        splitter geometry; gives the user the documented initial 100 px
        rail + content stack takes the rest. The user can still drag the
        splitter afterward (clamped to RAIL_MIN/MAX_WIDTH_PX).
        """
        super().showEvent(a0)
        if self._splitter is None:
            return
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._enforce_initial_rail_size)

    def _enforce_initial_rail_size(self) -> None:
        """Deferred splitter sizing; avoids the pre-layout race."""
        if self._splitter is None or self._splitter.count() < 2:
            return
        content_width = max(self.width() - RAIL_WIDTH_PX, 200)
        self._splitter.setSizes([RAIL_WIDTH_PX, content_width])

    # ---------------------------------------------------------- shutdown

    def closeEvent(self, a0: QCloseEvent | None) -> None:
        """Stop the worker thread cleanly before the Qt app exits. paho
        loop_forever() returns on disconnect(); QThread.wait() joins.
        """
        self._timer.stop()
        self._worker.stop()
        self._worker.wait(2000)  # 2 s grace before hard abandon
        super().closeEvent(a0)
