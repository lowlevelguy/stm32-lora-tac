"""Shared EndDevice selector — QComboBox styled for empty/disabled state.

Change 1 (2026-07-31): both MonitorPage and ControlPage need an EndDevice
dropdown that updates when new ``+/sensor`` topics arrive and shows a
disabled placeholder when no devices have been heard from yet. The
two pages share this widget so the styling + state logic lives in one
place.

Behavior
--------
* No devices known: combo is disabled and shows the placeholder text
  ``"(No End Devices Detected)"`` (configurable via param).
* One+ devices known: combo is enabled, items are populated from the
  ``NameRegistry``'s ``known_devices()`` sorted ascending; each item's
  display text is ``registry.device_display(id)`` (alias-or-default),
  and the item's ``userData`` carries the integer enddev_id.
* Selection emits ``currentEndDevChanged(int)`` so pages can swap the
  active buffer / form / etc.

Observer wiring
---------------
The selector registers itself as an observer on the NameRegistry so
device alias edits (from the ALIASES tab) and new-device discoveries
(from the worker) both refresh the list. The window also calls
``refresh()`` directly when a /sensor message announces a new device.
"""
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QWidget

from name_registry import NameRegistry


_PLACEHOLDER_EMPTY = "(No End Devices Detected)"


class DeviceSelector(QWidget):
    """QComboBox wrapper that auto-refreshes from the NameRegistry.

    Exposes a flattened combo API (``currentData``, ``setCurrentData``,
    ``setEnabled``) so callers don't have to dig into the inner
    QComboBox. Signals are re-exposed for clean connect() calls.
    """

    # Emitted when the user (or a repopulation that preserves the
    # current selection) changes the active EndDevice ID.
    currentEndDevChanged = pyqtSignal(int)

    def __init__(
        self,
        registry: NameRegistry,
        parent: QWidget | None = None,
        placeholder_empty: str = _PLACEHOLDER_EMPTY,
    ) -> None:
        super().__init__(parent)
        self._registry = registry
        self._placeholder = placeholder_empty

        # Inner combo does the actual rendering; this widget is just
        # a thin behavior wrapper. Wrapping (rather than subclassing
        # QComboBox) lets us present a narrower API and avoids
        # QComboBox's many overloaded signals leaking out.
        self._combo = QComboBox(self)
        # Size policy: QComboBox's default sizeHint is driven by the
        # longest item text (e.g. "End Device 300 (address 0x12C)"),
        # which balloons the page's minimum size hint and forces the
        # window wider than requested. AdjustToMinimumContentsLength
        # makes the hint small; the stretch factor in the parent row
        # layout gives the combo real space at runtime.
        self._combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon,
        )
        self._combo.setMinimumContentsLength(0)
        layout = self._make_layout()
        layout.addWidget(self._combo)
        layout.setContentsMargins(0, 0, 0, 0)

        self._combo.currentIndexChanged.connect(self._on_index_changed)

        # Initial state: empty list -> disabled placeholder.
        self.refresh()

        # Subscribe to registry so future alias edits / discoveries
        # refresh this selector without the window forwarding manually.
        self._registry.add_observer(self._on_registry_changed)

    def _make_layout(self):
        # Local import avoids a circular dep at module load time.
        from PyQt6.QtWidgets import QHBoxLayout
        return QHBoxLayout(self)

    # ------------------------------------------------------- public surface

    def combo(self) -> QComboBox:
        """Access the inner QComboBox for direct layout placement.
        Callers that need to position the combo in their own layout
        should call this and add the returned widget (NOT this wrapper)
        to their layout — the wrapper is just a behaviour holder.
        """
        return self._combo

    def current_enddev_id(self) -> int | None:
        """Currently selected EndDevice ID, or None if the empty
        placeholder is showing (no devices known).
        """
        return self._combo.currentData() if self._combo.isEnabled() else None

    def set_current_enddev_id(self, enddev_id: int) -> bool:
        """Pick the matching entry. Returns True if the id was found
        and selected, False otherwise (selection unchanged on miss).
        """
        for i in range(self._combo.count()):
            if self._combo.itemData(i) == enddev_id:
                self._combo.setCurrentIndex(i)
                return True
        return False

    # ------------------------------------------------------- refresh logic

    def refresh(self) -> None:
        """Repopulate combo items from the registry. Preserves the
        current selection if the previously-selected EndDevice is
        still known; otherwise picks index 0 (smallest id).

        Toggling disabled/placeholder state is handled here so callers
        never see an enabled-but-empty combo.
        """
        devices = self._registry.known_devices()
        prev_id = self._combo.currentData()

        # Block signals during repopulation so we emit exactly one
        # currentEndDevChanged at the end (if the selection actually
        # changed), not one per addItem.
        self._combo.blockSignals(True)
        self._combo.clear()

        if not devices:
            self._combo.addItem(self._placeholder, userData=None)
            self._combo.setEnabled(False)
            self._combo.blockSignals(False)
            # Always emit None-equivalent: pass -1 to indicate "no
            # selection" to listeners. Pages handle this defensively
            # (MonitorPage reads buffers.get(None) -> None -> stale
            # readouts, ControlPage disables its form).
            self.currentEndDevChanged.emit(-1)
            return

        for did in devices:
            self._combo.addItem(self._registry.device_display(did), userData=did)
        self._combo.setEnabled(True)

        # Preserve prior selection if possible.
        new_index = 0
        if prev_id is not None:
            for i in range(self._combo.count()):
                if self._combo.itemData(i) == prev_id:
                    new_index = i
                    break
        self._combo.setCurrentIndex(new_index)
        self._combo.blockSignals(False)

        # If the selection actually changed (e.g. previous is gone
        # or this is the first population), notify subscribers.
        new_id = self._combo.currentData()
        if new_id != prev_id:
            self.currentEndDevChanged.emit(int(new_id) if new_id is not None else -1)

    # --------------------------------------------------------- signal glue

    def _on_index_changed(self, _index: int) -> None:
        """User picked a different row. Forward the new id."""
        if not self._combo.isEnabled():
            return
        did = self._combo.currentData()
        if did is not None:
            self.currentEndDevChanged.emit(int(did))

    def _on_registry_changed(self, kind: str, key: tuple) -> None:
        """NameRegistry observer callback. Refresh on device changes;
        ignore sensor/actuator-only changes (they don't affect this
        selector's contents).
        """
        if kind == "device":
            self.refresh()
