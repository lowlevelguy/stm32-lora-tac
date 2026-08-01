"""ALIASES page — Tab 3: rename EndDevices / Sensors / Actuators.

Change 4 (2026-07-31). The page is a pure editor over the NameRegistry:
the user picks an EndDevice (or discovers one via /sensor arrivals),
edits an alias for it, then drills into its sensors and actuators to
rename each and (for actuators) pick a wire-format Type. Per user
decision the registry is in-memory only; renaming is reset on app
close, no JSON file is written.

Layout
------
Three sub-sections stacked vertically:
1. EndDevice alias field + (optional) "Add new device by ID" button.
2. Sensor list (one editable QLineEdit per known sensor — known
   sensors are populated as /sensor messages are received).
3. Actuator list (one alias QLineEdit + one Type QComboBox per known
   actuator) + "Add actuator by ID" button at the bottom.

Registry synchronization
-------------------------
This page subscribes to registry observers so a new device discovered
while the user is on another tab shows up here when they switch to
ALIASES. All edits flow back through the registry via
``set_device_alias`` etc., which fires observers and updates the rest
of the UI live (Monitor/Control device selectors, Control actuator
combo).
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from name_registry import ActuatorType, NameRegistry


class AliasesPage(QWidget):
    """Tab 3 — alias + Type editor for EndDevices, Sensors, Actuators."""

    def __init__(self, registry: NameRegistry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._registry = registry

        self._build_ui()
        # Populates the device combo from the registry (placeholder +
        # disabled state for the empty case), then re-renders all the
        # sub-sections for the currently-selected device (or the
        # placeholder text fields if no devices are known yet).
        self._rebuild_device_combo()
        self._registry.add_observer(self._on_registry_changed)

    # ----------------------------------------------------------- UI build

    def _build_ui(self) -> None:
        """Vertical layout: section head, EndDevice row + alias field,
        Sensor group, Actuator group + add-actuator button.
        """
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        head = QLabel("NAME ALIASES", self)
        head.setObjectName("sectionHead")
        root.addWidget(head)

        # EndDevice selection + alias.
        dev_row = QHBoxLayout()
        dev_row.setSpacing(12)
        dev_lbl = QLabel("EndDevice:", self)
        dev_lbl.setObjectName("readout")
        self._device_combo = QComboBox(self)
        self._device_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon,
        )
        self._device_combo.setMinimumContentsLength(0)
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        dev_row.addWidget(dev_lbl)
        dev_row.addWidget(self._device_combo, stretch=1)

        self._btn_add_device = QPushButton("Add device by ID...", self)
        self._btn_add_device.clicked.connect(self._prompt_add_device)
        dev_row.addWidget(self._btn_add_device)

        root.addLayout(dev_row)

        # EndDevice alias form.
        dev_form = QFormLayout()
        self._device_alias_field = QLineEdit(self)
        self._device_alias_field.setPlaceholderText(
            "e.g. \"2nd Floor, IT Room\" (leave blank -> default)",
        )
        self._device_alias_field.editingFinished.connect(
            self._on_device_alias_edited,
        )
        dev_form.addRow("Alias:", self._device_alias_field)
        root.addLayout(dev_form)

        # Sensor group — dynamically populated on device change.
        self._sensor_group = QGroupBox("Sensors", self)
        self._sensor_group_layout = QVBoxLayout(self._sensor_group)
        root.addWidget(self._sensor_group)

        # Actuator group — alias + Type per row, add button at the end.
        self._actuator_group = QGroupBox("Actuators", self)
        self._actuator_group_layout = QVBoxLayout(self._actuator_group)
        self._btn_add_actuator = QPushButton("Add actuator by ID...", self)
        self._btn_add_actuator.setObjectName("_add_actuator_persistent")
        self._btn_add_actuator.clicked.connect(self._prompt_add_actuator)
        self._actuator_group_layout.addWidget(self._btn_add_actuator)
        root.addWidget(self._actuator_group)

        root.addStretch()

    # ----------------------------------------------------- device section

    def _current_device_id(self) -> int | None:
        data = self._device_combo.currentData()
        return int(data) if data is not None else None

    def _rebuild_device_combo(self) -> None:
        """Repopulate the EndDevice combo from the registry. Preserves
        selection if possible.
        """
        prev = self._device_combo.currentData()
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        devices = self._registry.known_devices()
        if not devices:
            self._device_combo.addItem("(No End Devices Detected)")
            self._device_combo.setEnabled(False)
        else:
            for did in devices:
                self._device_combo.addItem(
                    self._registry.device_display(did), userData=did,
                )
            self._device_combo.setEnabled(True)
            if prev is not None:
                for i in range(self._device_combo.count()):
                    if self._device_combo.itemData(i) == prev:
                        self._device_combo.setCurrentIndex(i)
                        break
        self._device_combo.blockSignals(False)
        # Manually fire _on_device_changed for the initial population.
        self._on_device_changed(self._device_combo.currentIndex())

    def _on_device_changed(self, _index: int) -> None:
        """Called when the EndDevice combo selection changes. Re-renders
        all sub-sections: device alias field + sensor group + actuator group.
        """
        did = self._current_device_id()
        # Device alias field.
        if did is None:
            self._device_alias_field.setEnabled(False)
            self._device_alias_field.setText("")
        else:
            self._device_alias_field.setEnabled(True)
            alias = self._registry.device_alias(did)
            self._device_alias_field.setText(alias or "")

        self._rebuild_sensor_group(did)
        self._rebuild_actuator_group(did)

    def _on_device_alias_edited(self) -> None:
        """User pressed Enter / tabbed away from the device alias field."""
        did = self._current_device_id()
        if did is None:
            return
        self._registry.set_device_alias(did, self._device_alias_field.text())

    def _prompt_add_device(self) -> None:
        """Ask the user for a numeric EndDevice ID via a dialog. Add
        the device to the registry if the ID is valid (0..255).
        """
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self,
            "Add EndDevice by ID",
            "EndDevice ID (0..255):",
        )
        if not ok or not text.strip():
            return
        try:
            did = int(text.strip())
        except ValueError:
            QMessageBox.warning(self, "Invalid ID", "Enter a number 0..255.")
            return
        if not (0 <= did <= 0xFF):
            QMessageBox.warning(self, "Out of range", "ID must be 0..255.")
            return
        self._registry.register_device(did)
        # Select the newly added device.
        self._device_combo.blockSignals(False)
        for i in range(self._device_combo.count()):
            if self._device_combo.itemData(i) == did:
                self._device_combo.setCurrentIndex(i)
                break

    # ----------------------------------------------------- sensor section

    def _rebuild_sensor_group(self, did: int | None) -> None:
        """Clear + re-populate the sensor group box for the current
        device. Sensors are removed-only — they're discovered via
        /sensor messages (the user can't add them; the device itself
        defines which sensors it has).
        """
        self._clear_layout(self._sensor_group_layout)
        # Re-add the empty-state placeholder if needed.
        if did is None or not self._known_sensors_for(did):
            placeholder = QLabel("(no sensors seen yet for this device)")
            placeholder.setObjectName("readout")
            self._sensor_group_layout.addWidget(placeholder)
            return
        for sid in self._known_sensors_for(did):
            row = QHBoxLayout()
            row.setSpacing(12)
            lbl_text = QLabel(
                f"Sensor {sid} (address 0x{sid:02X})", self,
            )
            lbl_text.setObjectName("readout")
            row.addWidget(lbl_text)
            field = QLineEdit(self)
            field.setPlaceholderText("e.g. \"Temperature\"")
            cur = self._registry.sensor_alias(did, sid)
            field.setText(cur or "")
            field.editingFinished.connect(
                lambda _=False, d=did, s=sid, f=field: self._registry.set_sensor_alias(
                    d, s, f.text(),
                ),
            )
            row.addWidget(field, stretch=1)
            self._sensor_group_layout.addLayout(row)

    def _known_sensors_for(self, did: int) -> tuple[int, ...]:
        """Sensors seen for the device — pulled from the registry. The
        registry currently learns sensors via the dashboard_window
        whenever a /sensor message arrives from a new (device, sensor)
        tuple. This helper is a thin accessor giving a sorted tuple for
        stable UI rendering.
        """
        # The registry doesn't expose known_sensors directly today, but
        # we can scan its internal _sensors dict — the page is part of
        # the same package and trusts the registry's invariants.
        keys = [k for k in self._registry._sensors.keys()
                if isinstance(k, tuple) and len(k) == 2 and k[0] == did]
        return tuple(sorted(sid for (_did, sid) in keys))

    # --------------------------------------------------- actuator section

    def _rebuild_actuator_group(self, did: int | None) -> None:
        """Clear + re-populate the actuator group box. Includes the
        "Add actuator by ID" button at the bottom.
        """
        self._clear_layout(self._actuator_group_layout)
        if did is None or not self._registry.known_actuators(did):
            placeholder = QLabel("(no actuators configured for this device)")
            placeholder.setObjectName("readout")
            self._actuator_group_layout.addWidget(placeholder)
        else:
            for aid in self._registry.known_actuators(did):
                self._actuator_group_layout.addLayout(
                    self._make_actuator_row(did, aid),
                )
        # Re-add the add-actuator button last so it's always available.
        self._actuator_group_layout.addWidget(self._btn_add_actuator)

    def _make_actuator_row(self, did: int, aid: int) -> QHBoxLayout:
        """One alias + Type row for an actuator."""
        row = QHBoxLayout()
        row.setSpacing(12)

        # Label + ID display.
        lbl_text = QLabel(
            f"Actuator {aid} (address 0x{aid:02X})", self,
        )
        lbl_text.setObjectName("readout")
        row.addWidget(lbl_text)

        # Alias field.
        alias_field = QLineEdit(self)
        alias_field.setPlaceholderText("e.g. \"Ventilator\"")
        cur = self._registry.actuator_alias(did, aid)
        alias_field.setText(cur or "")
        alias_field.editingFinished.connect(
            lambda _=False, d=did, a=aid, f=alias_field: self._registry.set_actuator_alias(
                d, a, f.text(),
            ),
        )
        row.addWidget(alias_field, stretch=1)

        # Type combo.
        type_combo = QComboBox(self)
        type_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon,
        )
        type_combo.setMinimumContentsLength(0)
        for t in (ActuatorType.BOOL, ActuatorType.S8, ActuatorType.U8):
            type_combo.addItem(t.label, userData=t)
        cur_type = self._registry.actuator_type(did, aid)
        for i in range(type_combo.count()):
            if type_combo.itemData(i) is cur_type:
                type_combo.setCurrentIndex(i)
                break
        type_combo.currentIndexChanged.connect(
            lambda _i=0, d=did, a=aid, c=type_combo: self._registry.set_actuator_type(
                d, a, c.currentData(),
            ),
        )
        row.addWidget(type_combo)
        return row

    def _prompt_add_actuator(self) -> None:
        """Ask for an actuator ID (0..255) and add it to the current
        device's known actuators.
        """
        from PyQt6.QtWidgets import QInputDialog
        did = self._current_device_id()
        if did is None:
            QMessageBox.information(
                self, "No device", "Select an EndDevice first.",
            )
            return
        text, ok = QInputDialog.getText(
            self,
            f"Add actuator for EndDevice {did}",
            "Actuator ID (0..255):",
        )
        if not ok or not text.strip():
            return
        try:
            aid = int(text.strip())
        except ValueError:
            QMessageBox.warning(self, "Invalid ID", "Enter a number 0..255.")
            return
        if not (0 <= aid <= 0xFF):
            QMessageBox.warning(self, "Out of range", "ID must be 0..255.")
            return
        self._registry.add_actuator(did, aid)

    # ----------------------------------------------------- observer wiring

    def _on_registry_changed(self, kind: str, key: tuple) -> None:
        """Registry observer — refresh the device combo on device
        changes, and the active section on sensor/actuator changes.
        """
        if kind == "device":
            self._rebuild_device_combo()
            return
        # For sensor / actuator changes, refresh only the visible
        # section so we don't lose focus on the field the user is
        # currently typing in.
        did = self._current_device_id()
        if did is None:
            return
        if kind == "sensor" and key and key[0] == did:
            # Don't rebuild the field the user is editing (that'd lose
            # focus mid-typing). EditingFinished already routed the
            # change. Skip rebuild on alias edits.
            return
        if kind == "actuator":
            # Rebuild the whole actuator group: a new actuator was
            # likely added via add_actuator. Alias/Type edits already
            # routed through editingFinished without rebuild.
            if key == ():
                self._rebuild_actuator_group(did)
            # Per-actuator edits (Type change in Control page) don't
            # need a rebuild here — the user isn't editing this row
            # when the change came from elsewhere.

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _clear_layout(layout) -> None:
        """Clear all widgets + sub-layouts from a layout, deleting each."""
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                # Don't delete the persistent add-actuator button — it
                # belongs to self and is reused across rebuilds.
                if w.objectName() != "_add_actuator_persistent":
                    w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                AliasesPage._clear_layout(sub)
                sub.deleteLater()
