"""CONTROL page — Tab 1: actuator command console.

Handoff § Section rhythm / Tab 1 + user changes 2026-07-31 (#1, #2,
#3): the page is the only downlink origin in the dashboard. On click
of ON/OFF (or Send for integer types) it pack a 2-byte MQTT payload
(``ActuatorID | Cmd``) — the same shape the bridge's
``topic_map.parse_downlink_payload`` expects on the broker — and emits
``publish_requested`` up to the window, which forwards it to the MQTT
worker. The window routes incoming ``+/ack`` messages back here via
``handle_ack()`` so the page can cancel its 2 s verify timer.

Form layout
-----------
The page renders three rows: EndDevice selector (shared widget),
Actuator selector, and a Type-aware command form that swaps between
ON/OFF buttons ( BOOLEAN ) and a bounds-checked number field ( S8 / U8
). The Type is a per-actuator property stored in the NameRegistry;
selecting a different actuator refreshes the Type dropdown, and
selecting a different Type (only editable from the ALIASES tab)
refreshes the form.

State machine
-------------
    idle -- click --> sending -- ack rx --> acked (brief green flash)
                    |          '-- 2 s timeout --> timeout (red border)
                    v
                  form disabled, "SENDING..."
                  (QSS :disabled style is the loading state)
"""
import time

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Sanctioned import via _bridge_import.py shim.
from lora_frame import LoraFrame  # type: ignore[import]

from config import (
    ACK_TIMEOUT_S,
    CMD_BYTE_MAX_S8,
    CMD_BYTE_MAX_U8,
    CMD_BYTE_MIN_S8,
    CMD_BYTE_MIN_U8,
    CMD_OFF,
    CMD_ON,
    DEFAULT_ENDDEV_ID,
    DOWNLINK_TOPIC_TEMPLATE,
)
from device_selector import DeviceSelector
from name_registry import ActuatorType, NameRegistry


class ControlPage(QWidget):
    """Tab 1 — EndDevice + Actuator + Type dropdowns + form + history."""

    publish_requested = pyqtSignal(str, bytes)

    def __init__(
        self,
        registry: NameRegistry,
        enddev_id: int = DEFAULT_ENDDEV_ID,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._registry = registry
        self._enddev_id = enddev_id
        self._verify_timer: QTimer | None = None
        self._last_command_ts: float | None = None
        # No devices known until a /sensor topic announces one. Set
        # BEFORE _build_ui: DeviceSelector emits -1 during construction
        # and _on_enddev_changed consults this flag.
        self._no_devices = True

        self._build_ui()
        # Initial form sync: pull Type for (enddev, default actuator 0x00)
        # and render the matching form. run after _build_ui so widgets exist.
        self._repopulate_actuator_combo()
        self._refresh_type_from_actuator()
        self._render_form_for_current_type()
        # The initial -1 emission during _build_ui fired before the
        # actuator/type combos existed, so apply the empty state now
        # (clears the stale "Actuator 0" entry + disables the combos).
        if self._no_devices:
            self._apply_no_devices_state()

        # Subscribe to registry so actuator alias / type edits (from the
        # ALIASES tab) refresh the form live.
        self._registry.add_observer(self._on_registry_changed)

    # ----------------------------------------------------------- UI build

    def _build_ui(self) -> None:
        """Vertical layout: section head, EndDevice row, Actuator+Type
        row, dynamic form row, status line, history block.
        """
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        head = QLabel("ACTUATOR COMMAND CONSOLE", self)
        head.setObjectName("sectionHead")
        root.addWidget(head)

        # EndDevice selector row (Change 1, 2026-07-31).
        dev_row = QHBoxLayout()
        dev_row.setSpacing(12)
        dev_lbl = QLabel("EndDevice:", self)
        dev_lbl.setObjectName("readout")
        self._device_selector = DeviceSelector(self._registry, self)
        self._device_selector.currentEndDevChanged.connect(
            self._on_enddev_changed,
        )
        dev_row.addWidget(dev_lbl)
        dev_row.addWidget(self._device_selector.combo(), stretch=1)
        dev_row.addStretch()
        root.addLayout(dev_row)

        # Actuator + Type row (Changes 2, 3, 2026-07-31).
        act_row = QHBoxLayout()
        act_row.setSpacing(12)

        act_lbl = QLabel("Actuator:", self)
        act_lbl.setObjectName("readout")
        self._actuator_combo = QComboBox(self)
        # Small minimum size hint; the stretch factor in the row
        # layout grants real width at runtime. Without this the combo
        # sizeHint (driven by longest item text) balloons the page's
        # minimum width and Qt refuses to shrink the window to 720.
        self._actuator_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon,
        )
        self._actuator_combo.setMinimumContentsLength(0)
        self._actuator_combo.currentIndexChanged.connect(
            self._on_actuator_changed,
        )
        act_row.addWidget(act_lbl)
        act_row.addWidget(self._actuator_combo, stretch=2)

        type_lbl = QLabel("Type:", self)
        type_lbl.setObjectName("readout")
        self._type_combo = QComboBox(self)
        self._type_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon,
        )
        self._type_combo.setMinimumContentsLength(0)
        self._type_combo.addItem(
            ActuatorType.BOOL.label, userData=ActuatorType.BOOL,
        )
        self._type_combo.addItem(
            ActuatorType.S8.label, userData=ActuatorType.S8,
        )
        self._type_combo.addItem(
            ActuatorType.U8.label, userData=ActuatorType.U8,
        )
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        act_row.addWidget(type_lbl)
        act_row.addWidget(self._type_combo, stretch=2)

        act_row.addStretch()
        root.addLayout(act_row)

        # Dynamic form row. Both the BOOL (ON/OFF buttons) and the
        # INTEGER (label + line edit + Send button) forms are
        # pre-built and shown/hidden based on the current Type. This
        # avoids widget churn and the attribute-staleness bugs a clear
        # + re-render path would bring (deleting widgets leaves their
        # Python attribute reference dangling until garbage collected).
        form_row = QHBoxLayout()
        form_row.setSpacing(12)

        # BOOL form: ON + OFF buttons.
        self._btn_on = QPushButton("ON", self)
        self._btn_on.setProperty("role", "on")
        self._btn_on.clicked.connect(self._on_on_clicked)
        form_row.addWidget(self._btn_on)

        self._btn_off = QPushButton("OFF", self)
        self._btn_off.setProperty("role", "off")
        self._btn_off.clicked.connect(self._on_off_clicked)
        form_row.addWidget(self._btn_off)

        # INTEGER form: label + line edit + Send button.
        self._int_label = QLabel("Value:", self)
        self._int_label.setObjectName("readout")
        form_row.addWidget(self._int_label)

        self._int_field = QLineEdit(self)
        self._int_field.setPlaceholderText("0..0")
        # QIntValidator is recreated per Type to set correct bounds.
        self._int_validator = QIntValidator(0, 1, self)
        self._int_field.setValidator(self._int_validator)
        self._int_field.returnPressed.connect(self._on_send_int)
        form_row.addWidget(self._int_field, stretch=1)

        self._btn_send = QPushButton("Send", self)
        self._btn_send.setProperty("role", "on")
        self._btn_send.clicked.connect(self._on_send_int)
        form_row.addWidget(self._btn_send)

        form_row.addStretch()
        root.addLayout(form_row)

        # Initial form mask: BOOL active, INTEGER hidden.
        # _active_buttons is updated by _render_form_for_current_type.
        self._active_buttons: tuple[QPushButton, ...] = (
            self._btn_on, self._btn_off,
        )
        self._render_form_for_current_type()

        # Sending-status line — updated by the state-machine functions.
        self._status_line = QLabel("Idle.", self)
        self._status_line.setObjectName("readout")
        root.addWidget(self._status_line)

        # Command-history block.
        self._history_label = QLabel("History:", self)
        self._history_label.setObjectName("readout")
        root.addWidget(self._history_label)

        self._history_text = QLabel("No commands sent.", self)
        self._history_text.setObjectName("readoutValue")
        self._history_text.setWordWrap(True)
        root.addWidget(self._history_text)

        root.addStretch()

    def _render_form_for_current_type(self) -> None:
        """Show/hide the pre-built form widgets based on the current
        Type. Both the BOOL (ON/OFF buttons) and INTEGER (label +
        line edit + Send button) forms exist permanently; this method
        just toggles which pair is visible. Avoids widget churn and
        the attribute-staleness bugs a delete/re-create path would
        bring (Python-side references outlive the deleted C++ widget).
        """
        type_ = self._current_type()

        if type_ is ActuatorType.BOOL:
            # Show buttons, hide integer field group.
            self._btn_on.show()
            self._btn_off.show()
            self._int_label.hide()
            self._int_field.hide()
            self._btn_send.hide()
            self._active_buttons = (self._btn_on, self._btn_off)
        else:
            # S8 or U8 — show integer field, hide bool buttons.
            self._btn_on.hide()
            self._btn_off.hide()
            self._int_label.show()
            self._int_field.show()
            self._btn_send.show()

            # Refresh bounds on the validator + placeholder + default text.
            lo, hi = self._int_bounds_for(type_)
            self._int_validator.setRange(lo, hi)
            self._int_field.setPlaceholderText(f"{lo}..{hi}")
            # Reset to 0 on type switch so the field never holds an
            # out-of-range stale value from the previous Type; 0 is a
            # valid value for both S8 and U8 (per user change
            # 2026-07-31: S8 must not default to its minimum).
            self._int_field.setText("0")
            self._active_buttons = (self._btn_send,)

        # Apply current enabled/disabled state to the newly visible pair.
        # If a send is in flight, or no EndDevice is selected, the
        # form should stay disabled.
        sending = self._verify_timer is not None
        locked = sending or self._no_devices
        for btn in self._active_buttons:
            btn.setEnabled(not locked)
        self._int_field.setEnabled(not locked)

    # ----------------------------------------------------- state helpers

    def _current_type(self) -> ActuatorType:
        """Type currently shown in the Type combo (may differ from the
        registry's stored type while the user is mid-selection)."""
        return self._type_combo.currentData()

    def _current_actuator_id(self) -> int | None:
        data = self._actuator_combo.currentData()
        return int(data) if data is not None else None

    @staticmethod
    def _int_bounds_for(type_: ActuatorType) -> tuple[int, int]:
        """(min, max) for a QIntValidator given the wire type."""
        if type_ is ActuatorType.U8:
            return CMD_BYTE_MIN_U8, CMD_BYTE_MAX_U8
        if type_ is ActuatorType.S8:
            return CMD_BYTE_MIN_S8, CMD_BYTE_MAX_S8
        # Should not be called for BOOL; return safe default.
        return 0, 1

    # ----------------------------------------------- registry-driven refresh

    def _repopulate_actuator_combo(self) -> None:
        """Rebuild the Actuator dropdown from the registry's known
        actuators for the current EndDevice (Change 3, 2026-07-31).
        Preserves selection if the previously-selected actuator is
        still in the list.
        """
        prev = self._actuator_combo.currentData()
        self._actuator_combo.blockSignals(True)
        self._actuator_combo.clear()
        for aid in self._registry.known_actuators(self._enddev_id):
            self._actuator_combo.addItem(
                self._registry.actuator_display(self._enddev_id, aid),
                userData=aid,
            )
        # Re-select previous if possible, else index 0.
        new_index = 0
        if prev is not None:
            for i in range(self._actuator_combo.count()):
                if self._actuator_combo.itemData(i) == prev:
                    new_index = i
                    break
        if self._actuator_combo.count() > 0:
            self._actuator_combo.setCurrentIndex(new_index)
        self._actuator_combo.blockSignals(False)

    def _refresh_type_from_actuator(self) -> None:
        """Set the Type combo to match the registry's stored Type for
        the currently-selected actuator. Does not emit (signals
        blocked) so we don't recurse into form re-render.
        """
        aid = self._current_actuator_id()
        if aid is None:
            return
        type_ = self._registry.actuator_type(self._enddev_id, aid)
        for i in range(self._type_combo.count()):
            if self._type_combo.itemData(i) is type_:
                self._type_combo.blockSignals(True)
                self._type_combo.setCurrentIndex(i)
                self._type_combo.blockSignals(False)
                return

    # ----------------------------------------------------- form callbacks

    def _on_enddev_changed(self, enddev_id: int) -> None:
        """EndDevice dropdown selection changed (Change 1). Update the
        Actuator dropdown for the new device + re-render the form.
        """
        if enddev_id == -1:
            # No devices available — disable the form. Clearing _enddev_id
            # to None would break payload logic; we leave it stale and
            # rely on _apply_no_devices_state clearing the actuator
            # combo so no command can be sent.
            self._no_devices = True
            if hasattr(self, "_actuator_combo"):
                # Guard: the -1 emission from DeviceSelector.__init__
                # fires during _build_ui, before the combos exist.
                self._apply_no_devices_state()
            return
        self._no_devices = False
        self._enddev_id = enddev_id
        self._actuator_combo.setEnabled(True)
        self._type_combo.setEnabled(True)
        self._repopulate_actuator_combo()
        self._refresh_type_from_actuator()
        self._render_form_for_current_type()
        # New target — cancel any in-flight verify timer; its ACK would
        # arrive for the old device + actuator.
        self._cancel_in_flight()

    def _apply_no_devices_state(self) -> None:
        """Disable + clear the actuator/type combos. Called when no
        EndDevice is known: the stale "Actuator 0" entry for the
        default device must not be sendable.
        """
        self._actuator_combo.clear()
        self._actuator_combo.setEnabled(False)
        self._type_combo.setEnabled(False)

    def _on_actuator_changed(self, _index: int) -> None:
        """Actuator dropdown selection changed (Change 3). Refresh the
        Type combo from the registry and re-render the form.
        """
        self._refresh_type_from_actuator()
        self._render_form_for_current_type()

    def _on_type_changed(self, _index: int) -> None:
        """Type dropdown changed. Persist the choice to the registry
        (per user decision: Type is a per-actuator property) and
        re-render the form for the new type.
        """
        aid = self._current_actuator_id()
        if aid is None:
            return
        new_type = self._current_type()
        # Avoid recursion: set_actuator_type fires the observer which
        # would call _refresh_type_from_actuator. We're already in the
        # user-driven path, so just persist.
        self._registry.set_actuator_type(
            self._enddev_id, aid, new_type,
        )
        self._render_form_for_current_type()

    def _on_registry_changed(self, kind: str, key: tuple) -> None:
        """NameRegistry observer. Refreshes on device + actuator
        changes (alias edits in the ALIASES tab should reflect here).
        """
        if kind == "device":
            # EndDevice selector handles its own refresh; we may need
            # to repopulate actuators if a device alias changed.
            self._repopulate_actuator_combo()
        elif kind == "actuator":
            # An alias or Type for our current actuator changed (or a
            # new actuator was added). Re-render.
            self._repopulate_actuator_combo()
            self._refresh_type_from_actuator()
            self._render_form_for_current_type()

    # -------------------------------------------------------- send path

    def _downlink_topic(self) -> str:
        return DOWNLINK_TOPIC_TEMPLATE.format(enddev_id=self._enddev_id)

    def _send(self, cmd: int) -> None:
        """Build the 2-byte payload, validate via LoraFrame, emit
        publish_requested, start the 2 s verify timer. All form
        widgets disable while timer is active.
        """
        if self._verify_timer is not None:
            self._status_line.setText(
                "Wait: previous command still awaiting ACK.",
            )
            return

        aid = self._current_actuator_id()
        if aid is None:
            self._status_line.setText("Select an actuator first.")
            return

        # Validate via LoraFrame (sanctioned import). We extract .data
        # to keep the same 2-byte payload bridge's parse_downlink_payload
        # expects, regardless of actuator type (BOOL/S8/U8 all flow the
        # int cmd value through the single Cmd byte).
        frame = LoraFrame.command_frame(
            dest_addr=self._enddev_id, actuator_id=aid, cmd=cmd & 0xFF,
        )
        payload = bytes((frame.data[0], frame.data[1]))

        topic = self._downlink_topic()
        self.publish_requested.emit(topic, payload)
        self._last_command_ts = time.monotonic()

        self._enter_sending_state()
        self._status_line.setText(
            f"Sending: {topic} -> [{payload[0]:02X} {payload[1]:02X}] "
            f"ACK: awaiting...",
        )

        self._verify_timer = QTimer(self)
        self._verify_timer.setSingleShot(True)
        self._verify_timer.timeout.connect(self._on_verify_timeout)
        self._verify_timer.start(int(ACK_TIMEOUT_S * 1000))

    def _enter_sending_state(self) -> None:
        """Disable every active button; use :disabled QSS as loading."""
        for btn in self._active_buttons:
            btn.setEnabled(False)
            btn.setProperty("state", "sending")
            self._polish(btn)

    def _exit_sending_state(self, outcome: str) -> None:
        """Re-enable buttons, paint transient success/error border."""
        for btn in self._active_buttons:
            btn.setEnabled(True)
            btn.setProperty("state", outcome)
            self._polish(btn)
        QTimer.singleShot(800, self._clear_transient_state)
        if self._verify_timer is not None:
            self._verify_timer.stop()
            self._verify_timer.deleteLater()
            self._verify_timer = None

    def _clear_transient_state(self) -> None:
        for btn in self._active_buttons:
            btn.setProperty("state", None)
            self._polish(btn)

    def _cancel_in_flight(self) -> None:
        """Kill any active verify timer + reset form to idle."""
        if self._verify_timer is not None:
            self._verify_timer.stop()
            self._verify_timer.deleteLater()
            self._verify_timer = None
        for btn in getattr(self, "_active_buttons", ()):
            btn.setEnabled(True)
            btn.setProperty("state", None)
            self._polish(btn)
        self._status_line.setText("Idle.")

    @staticmethod
    def _polish(widget: QWidget) -> None:
        style = widget.style()
        if style is None:
            return
        style.unpolish(widget)
        style.polish(widget)

    def _append_history(self, line: str) -> None:
        current = self._history_text.text()
        lines = current.split("\n")
        if lines and lines[0] == "No commands sent.":
            lines = []
        lines.insert(0, line)
        del lines[10:]
        self._history_text.setText("\n".join(lines))

    # ----------------------------------------------------- click handlers

    def _on_on_clicked(self) -> None:
        self._send(CMD_ON)

    def _on_off_clicked(self) -> None:
        self._send(CMD_OFF)

    def _on_send_int(self) -> None:
        """Send button / Enter key from the integer field. Bounds are
        enforced by the QIntValidator; clamping defensively anyway.
        """
        type_ = self._current_type()
        lo, hi = self._int_bounds_for(type_)
        try:
            value = int(self._int_field.text())
        except ValueError:
            self._status_line.setText("Enter an integer value first.")
            return
        # Clamp in case the validator was bypassed (paste, etc.).
        value = max(lo, min(hi, value))
        # S8 encodes negatives as two's-complement in the Cmd byte.
        cmd_byte = value & 0xFF
        self._send(cmd_byte)

    # --------------------------------------------------- verify resolution

    def _on_verify_timeout(self) -> None:
        self._exit_sending_state("error")
        self._status_line.setText("ACK: timeout - no response in 2 s.")
        aid = self._current_actuator_id() or 0
        self._append_history(
            f"{self._fmt_ts()} TX {self._downlink_topic()} "
            f"[{aid:02X} ..] ACK timeout",
        )

    def handle_ack(self, actuator_id: int, status: int) -> None:
        """Called by the window when an /ack arrives for this device.
        Cancel the verify timer if the actuator_id matches the command
        currently in flight.
        """
        if self._verify_timer is None:
            return
        if actuator_id != self._current_actuator_id():
            return
        outcome = "success" if status == 0x00 else "error"
        self._exit_sending_state(outcome)
        status_text = "OK" if status == 0x00 else f"ERR 0x{status:02X}"
        self._status_line.setText(f"ACK: {status_text}")
        self._append_history(
            f"{self._fmt_ts()} RX {self._downlink_topic()} "
            f"[{actuator_id:02X} {status:02X}] ACK {status_text}",
        )

    @staticmethod
    def _fmt_ts() -> str:
        return time.strftime("%H:%M:%S")

    # ----------------------------------------------------------- public

    def set_enddev(self, enddev_id: int) -> None:
        """Public hook for the window to force the EndDevice selection
        (used at startup if the device is known before the page is
        shown). Most flows go through the DeviceSelector signal.
        """
        self._device_selector.set_current_enddev_id(enddev_id)
