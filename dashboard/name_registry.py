"""In-memory name registry — central alias + per-actuator type store.

Holds human-readable aliases for EndDevices, Sensors, and Actuators, plus
the per-actuator Type (Boolean / Signed Int / Unsigned Int). All
mutations happen on the GUI thread; observer callbacks fire on the
same thread, so subscribers (device selectors, the ALIASES page, the
Control form) can update their widgets directly.

Default display strings
-----------------------
When a device / sensor / actuator has no alias set, the UI renders a
default identifier (e.g. ``"End Device 1 (address 0x01)"``). With an
alias set, the address is preserved in the label:
``"{alias} (address 0x01)"`` (per user change 2026-07-31 — the
address stays discoverable in dropdowns). The registry exposes
``device_display(id)``, ``sensor_display(id)``,
``actuator_display(id)`` that return the user-facing label; UI code
calls these helpers and never has to decide alias-vs-default itself.

Actuator type
-------------
Per user decision 2026-07-31, an actuator's Type is a fixed
property: ``BOOL``, ``S8`` (signed 8-bit), or ``U8`` (unsigned 8-bit).
ControlPage reads the type to decide which form (ON/OFF buttons vs.
number field) to render. The default for any new actuator is ``BOOL``
(matches the existing LED actuator 0x00).
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class ActuatorType(Enum):
    """Wire-format type for an actuator's command value.

    The 2-byte MQTT payload (``ActuatorID | Cmd``) is preserved across
    all three types — the type only changes how the Cmd byte is
    interpreted: BOOL sends 0/1, U8 sends 0..255, S8 sends -128..127
    (packed two's-complement). The bridge's parse_downlink_payload
    sees the same 2 bytes regardless of type.
    """
    BOOL = "bool"
    S8 = "s8"
    U8 = "u8"

    @property
    def label(self) -> str:
        """UI label for the Type dropdown."""
        return {
            ActuatorType.BOOL: "Boolean",
            ActuatorType.S8: "Signed Integer",
            ActuatorType.U8: "Unsigned Integer",
        }[self]


@dataclass(slots=True)
class _ActuatorEntry:
    """One actuator's alias + type. Keyed by (enddev_id, actuator_id)."""
    alias: str | None = None
    type: ActuatorType = ActuatorType.BOOL


# Observer callback signature: (kind: str, key: tuple) -> None.
# kind is "device" | "sensor" | "actuator" so subscribers can decide
# whether the change affects them. key is (enddev_id,) for devices,
# (enddev_id, sensor_id) for sensors, (enddev_id, actuator_id) for
# actuators. Pass key=() on a wholesale rebuild (e.g. when a new
# device is discovered and selectors need to repopulate fully).
Observer = Callable[[str, tuple], None]


class NameRegistry:
    """Central alias + type store. Thread-owned by the GUI thread.

    All mutators call the registered observers after the change so
    subscribers refresh their widgets without each one polling.
    """

    def __init__(self) -> None:
        # id -> alias (None = use default)
        self._devices: dict[int, str | None] = {}
        self._sensors: dict[tuple[int, int], str | None] = {}
        # (enddev_id, actuator_id) -> entry
        self._actuators: dict[tuple[int, int], _ActuatorEntry] = {}
        # Known actuator IDs per device — discovered via /sensor (the
        # bridge enumerates sensor IDs, but actuator IDs are configured
        # by the user in the ALIASES tab; default 0x00 for any device).
        self._known_actuators: dict[int, list[int]] = {}

        self._observers: list[Observer] = []

    # ----------------------------------------------------------- observers

    def add_observer(self, callback: Observer) -> None:
        """Register a subscriber. Called after every mutation."""
        self._observers.append(callback)

    def _notify(self, kind: str, key: tuple) -> None:
        """Fire observers. GUI-thread-only — no Qt signals to keep
        this class Qt-independent and unit-testable.
        """
        for cb in self._observers:
            cb(kind, key)

    # ----------------------------------------------------- device discovery

    def register_device(self, enddev_id: int) -> None:
        """Record that an EndDevice has been heard from. Idempotent:
        re-registering an existing device is a no-op (does NOT fire
        an observer — nothing changed). New device -> observer fires
        with key=() so selectors can repopulate.
        """
        if enddev_id not in self._devices:
            self._devices[enddev_id] = None
            # Bootstrap a default actuator 0x00 for the new device so
            # the Control page has something to show immediately.
            self._actuators.setdefault(
                (enddev_id, 0x00), _ActuatorEntry(),
            )
            self._known_actuators.setdefault(enddev_id, [0x00])
            self._notify("device", ())

    def known_devices(self) -> tuple[int, ...]:
        """Known EndDevice IDs in ascending order."""
        return tuple(sorted(self._devices.keys()))

    def has_devices(self) -> bool:
        return bool(self._devices)

    # ----------------------------------------------------- device aliases

    def device_alias(self, enddev_id: int) -> str | None:
        return self._devices.get(enddev_id)

    def set_device_alias(self, enddev_id: int, alias: str | None) -> None:
        """Set (or clear on None) the alias for a device. Fires observer."""
        if enddev_id not in self._devices:
            # Be permissive: setting an alias on an unknown device
            # registers it first. Lets the ALIASES tab seed a device
            # before any telemetry arrives.
            self.register_device(enddev_id)
        if alias is not None and alias.strip() == "":
            alias = None
        self._devices[enddev_id] = alias
        self._notify("device", (enddev_id,))

    def device_display(self, enddev_id: int) -> str:
        """User-facing label for a device. Alias if set, else the
        default ``"End Device {n} (address {hex(n)})"``. An aliased
        device keeps its address visible in the dropdown:
        ``"{alias} (address {hex(n)})"`` (Change 6, 2026-07-31).
        """
        alias = self._devices.get(enddev_id)
        if alias:
            return f"{alias} (address 0x{enddev_id:02X})"
        return f"End Device {enddev_id} (address 0x{enddev_id:02X})"

    # ----------------------------------------------------- sensor aliases

    def sensor_alias(self, enddev_id: int, sensor_id: int) -> str | None:
        return self._sensors.get((enddev_id, sensor_id))

    def set_sensor_alias(
        self, enddev_id: int, sensor_id: int, alias: str | None,
    ) -> None:
        """Set/clear a sensor's alias. Auto-registers the owning
        device if it's not known yet.
        """
        if enddev_id not in self._devices:
            self.register_device(enddev_id)
        if alias is not None and alias.strip() == "":
            alias = None
        self._sensors[(enddev_id, sensor_id)] = alias
        self._notify("sensor", (enddev_id, sensor_id))

    def sensor_display(self, enddev_id: int, sensor_id: int) -> str:
        """User-facing label: alias if set, else
        ``"Sensor {n} (address {hex(n)})"``.
        """
        alias = self._sensors.get((enddev_id, sensor_id))
        if alias:
            return alias
        return f"Sensor {sensor_id} (address 0x{sensor_id:02X})"

    # ----------------------------------------------------- actuator aliases

    def actuator_alias(
        self, enddev_id: int, actuator_id: int,
    ) -> str | None:
        entry = self._actuators.get((enddev_id, actuator_id))
        return entry.alias if entry is not None else None

    def set_actuator_alias(
        self, enddev_id: int, actuator_id: int, alias: str | None,
    ) -> None:
        """Set/clear an actuator's alias. Auto-registers device + the
        actuator's entry if needed.
        """
        if enddev_id not in self._devices:
            self.register_device(enddev_id)
        entry = self._actuators.setdefault(
            (enddev_id, actuator_id), _ActuatorEntry(),
        )
        if alias is not None and alias.strip() == "":
            alias = None
        entry.alias = alias
        self._notify("actuator", (enddev_id, actuator_id))

    def actuator_display(
        self, enddev_id: int, actuator_id: int,
    ) -> str:
        """User-facing label: alias if set, else
        ``"Actuator {n} (address {hex(n)})"``. An aliased actuator
        keeps its address visible in the dropdown:
        ``"{alias} (address {hex(n)})"`` (Change 6, 2026-07-31).
        """
        entry = self._actuators.get((enddev_id, actuator_id))
        alias = entry.alias if entry is not None else None
        if alias:
            return f"{alias} (address 0x{actuator_id:02X})"
        return f"Actuator {actuator_id} (address 0x{actuator_id:02X})"

    # --------------------------------------------------- actuator type

    def actuator_type(
        self, enddev_id: int, actuator_id: int,
    ) -> ActuatorType:
        """Type for an actuator. Defaults to BOOL if unknown."""
        entry = self._actuators.get((enddev_id, actuator_id))
        return entry.type if entry is not None else ActuatorType.BOOL

    def set_actuator_type(
        self, enddev_id: int, actuator_id: int, type_: ActuatorType,
    ) -> None:
        """Set the actuator's wire-format Type. Fires observer so
        ControlPage can re-render its form if this actuator is
        currently selected.
        """
        if enddev_id not in self._devices:
            self.register_device(enddev_id)
        entry = self._actuators.setdefault(
            (enddev_id, actuator_id), _ActuatorEntry(),
        )
        entry.type = type_
        self._notify("actuator", (enddev_id, actuator_id))

    # --------------------------------------------------- actuator enumeration

    def known_actuators(self, enddev_id: int) -> tuple[int, ...]:
        """Actuator IDs known for a device, ascending. Defaults to
        (0x00,) for any device (the project's LED actuator).
        """
        return tuple(sorted(self._known_actuators.get(enddev_id, [0x00])))

    def add_actuator(self, enddev_id: int, actuator_id: int) -> None:
        """Add an actuator ID to a device's known set. Idempotent.
        Lets the ALIASES tab enumerate a new actuator beyond 0x00.
        """
        if enddev_id not in self._devices:
            self.register_device(enddev_id)
        lst = self._known_actuators.setdefault(enddev_id, [])
        if actuator_id not in lst:
            lst.append(actuator_id)
            lst.sort()
            self._actuators.setdefault(
                (enddev_id, actuator_id), _ActuatorEntry(),
            )
            self._notify("actuator", ())
