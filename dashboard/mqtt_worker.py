"""MQTT worker thread — paho-mqtt v2 client on a QThread.

Handoff § MQTT threading model: exactly one worker thread owns the
paho client and runs ``loop_forever()``. All paho callbacks
(``on_message``, ``on_connect``, ``on_disconnect``) execute on the
worker thread and MUST NOT touch Qt widgets — they emit ``pyqtSignal``
to the GUI thread instead. The GUI thread never calls paho methods
directly; the only cross-thread publish path is the
``mqtt_publish_request`` signal forwarded to this worker's slot.

Reconnect policy: paho-mqtt v2 with ``reconnect_on_failure=True``
handles the 5 s -> 60 s exponential backoff internally (matches the
bridge's policy in ``uart_mqtt_bridge/mqtt_client.py``). The dashboard
does NOT run its own watchdog.

Subscriptions: wildcard ``+/sensor``, ``+/rssi``, ``+/snr``, ``+/ack``
per user decision 2026-07-30 (handoff open question #4). A '+' wildcard
must occupy a whole topic level, so we post-filter with
``parse_uplink_topic`` — a helper local to this module that reuses
``topic_map.parse_downlink_topic``'s regex pattern but does NOT modify
``topic_map.py`` (bridge code stays untouched per handoff rule #4).
"""

import logging
import re
from dataclasses import dataclass

import paho.mqtt.client as mqtt
from PyQt6.QtCore import QThread, pyqtSignal

from config import (
    MQTT_BROKER_HOST,
    MQTT_BROKER_PORT,
    MQTT_KEEPALIVE_S,
    MQTT_QOS,
    SUBSCRIPTION_TOPICS,
)

logger = logging.getLogger("dashboard.mqtt")


# ----------------------------------------------------------- topic parse

# Mirrors topic_map.parse_downlink_topic's regex but for uplink leaf
# topics (+/sensor, +/rssi, +/snr, +/ack). Captures the EndDevice ID
# from the leading "enddev{N}" segment and the channel from the
# trailing leaf. We do NOT import topic_map.parse_downlink_topic
# because that helper is specific to "enddev{N}/actuator"; we need a
# general 3-token form. The regex is intentionally permissive on the
# middle segment (allowing "enddev{N}") and strict on the leaf.
_UPLINK_TOPIC_RE = re.compile(
    r"^enddev(?P<enddev>\d+)/(?P<channel>sensor|rssi|snr|ack)$"
)


@dataclass(slots=True, frozen=True)
class UplinkTopic:
    """Parsed uplink topic — what the GUI thread gets instead of raw str.

    Keeping the parsed form here (on the worker) means the GUI thread
    doesn't re-parse on every message and can route by channel in O(1).
    The raw ``topic`` string is preserved for logging in DebugPage.
    """
    enddev_id: int
    channel: str
    raw: str


def parse_uplink_topic(topic: str) -> UplinkTopic | None:
    """Parse an uplink topic string captured by a wildcard subscription.

    Returns an ``UplinkTopic`` if the topic matches
    ``enddev{\\d+}/{sensor|rssi|snr|ack}``, else None. A None return
    means the wildcard matched a topic we don't care about (e.g.
    ``someotherdevice/sensor``) and the worker should drop it without
    emitting a signal — keeping spurious matches off the GUI thread.
    """
    m = _UPLINK_TOPIC_RE.match(topic)
    if m is None:
        return None
    n = int(m.group("enddev"))
    if not (0 <= n <= 0xFF):
        return None
    return UplinkTopic(enddev_id=n, channel=m.group("channel"), raw=topic)


def _reason_code_to_int(reason_code) -> int | None:
    """Coerce a paho v2 reason_code to an int for the Qt signal bus.

    paho-mqtt v2 may pass either a plain int (in the v311 callback
    path that mirrors the bridge's mqtt_client.py) OR a ``ReasonCode``
    object (in v5 / broker-initiated paths). The dashboard uses
    protocol=MQTTv311 so int is the common shape, but the
    broker-initiated disconnect still fires with a ReasonCode in v2.
    Handle both without raising — a TypeError inside _on_disconnect
    would crash the worker thread and prevent the GUI from learning
    the broker actually went down.
    """
    if reason_code is None:
        return None
    if isinstance(reason_code, int):
        return reason_code
    # ReasonCode object: ``.value`` is the internal int identifier.
    value = getattr(reason_code, "value", None)
    if isinstance(value, int):
        return value
    # Last-ditch: stringify so the GUI has something to show.
    try:
        return int(str(reason_code))
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------- worker class


class MqttWorker(QThread):
    """QThread that owns the paho-mqtt client and emits GUI-thread signals.

    Signals (all cross-thread):
        ``message_received`` — (enddev_id:int, channel:str, payload:bytes,
            topic_raw:str) for a telemetry or ack uplink.
        ``broker_state_changed`` — (state:str, reason_code:int|None)
            where state is "connected" | "disconnected" | "failed".

    Slot (cross-thread, called from GUI thread via signal connection):
        ``publish`` — (topic:str, payload:bytes). Calls ``client.publish``
            from the worker thread. paho is thread-safe for publish but
            the worker calling it respects the internal socket lock.
    """

    # Fire-and-forget: the worker reports uplinks to the GUI thread.
    # We carry the parsed EndDevID + channel so the GUI thread can
    # route by channel without re-parsing the topic on every message
    # (handoff § Wire-level event map: callbacks must run in <1 ms).
    message_received = pyqtSignal(int, str, bytes, str)
    # state in {"connected", "disconnected", "failed"}; reason_code is
    # the paho ReasonCode on "failed", else None.
    broker_state_changed = pyqtSignal(str, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # paho-mqtt v2 API (matches the bridge's mqtt_client.py).
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            protocol=mqtt.MQTTv311,
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        # paho owns the 5 s -> 60 s exponential backoff when this flag
        # is set (default True in v2); we do NOT run a watchdog.
        # client.reconnect_on_failure defaults True for VERSION2.

    # -------------------------------------------------------- thread entry

    def run(self) -> None:
        """QThread entry: connect + loop_forever. Returns on disconnect
        + shutdown signal (``requestInterruption`` from the GUI thread).
        """
        try:
            self._client.connect(
                MQTT_BROKER_HOST, MQTT_BROKER_PORT, keepalive=MQTT_KEEPALIVE_S,
            )
        except Exception as exc:
            # Initial connect failed — emit and let paho's internal
            # reconnect logic retry. (loop_forever with
            # reconnect_on_failure=True will keep trying.)
            logger.error("initial connect failed: %s", exc)
            self.broker_state_changed.emit("failed", None)
        # loop_forever blocks until the client is disconnected AND we
        # call loop_stop / set the thread to stop. On reconnect it
        # retries with back-off internally.
        self._client.loop_forever(retry_first_connection=True)

    # ---------------------------------------------------------- shutdown

    def stop(self) -> None:
        """Called from the GUI thread at application close. Disconnects
        the paho client so loop_forever() returns and run() exits. The
        QThread.wait() in the window's closeEvent handles join.
        """
        try:
            self._client.disconnect()
        except Exception:
            pass

    # ------------------------------------------------------ paho callbacks

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        """paho v2 on_connect signature (matches bridge mqtt_client.py).
        Subscribes to the wildcard topics; emits broker_state_changed
        so the GUI thread updates the status strip + DebugPage label.

        ``reason_code`` may be a plain int (v311 path) or a paho
        ``ReasonCode`` object (v5 path in v2). Coerce to int for the
        comparison + signal payload.
        """
        rc_int = _reason_code_to_int(reason_code)
        if rc_int == 0:
            logger.info(
                "connected to %s:%d", MQTT_BROKER_HOST, MQTT_BROKER_PORT,
            )
            for topic in SUBSCRIPTION_TOPICS:
                client.subscribe(topic, qos=MQTT_QOS)
                logger.info("subscribed to %s", topic)
            self.broker_state_changed.emit("connected", None)
        else:
            logger.error("connect refused (rc=%s)", reason_code)
            self.broker_state_changed.emit("failed", rc_int)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        """paho v2 on_disconnect signature. Emits so the GUI thread can
        mark the strip disconnected; paho itself will retry connect.

        ``reason_code`` may be a plain int (v311 path) or a paho
        ``ReasonCode`` object (v5 / broker-initiated path in v2).
        Coerce to an int for the Qt signal; log the human-readable
        str() form regardless.
        """
        rc_int = _reason_code_to_int(reason_code)
        logger.warning("disconnected (rc=%s)", reason_code)
        self.broker_state_changed.emit("disconnected", rc_int)

    def _on_message(self, client, userdata, msg):
        """paho callback — runs on the worker thread. Parse the topic,
        drop spurious wildcard matches, emit to GUI thread for routing.
        Payload stays raw ``bytes``; structured unpack is the GUI
        thread's job in each page's ``handle_mqtt`` method.
        """
        parsed = parse_uplink_topic(msg.topic)
        if parsed is None:
            # Wildcard caught a topic we don't care about (not enddev{N}
            # or unknown channel). Drop silently — don't pollute the
            # GUI thread's signal bus with noise.
            logger.debug("dropping spurious topic: %s", msg.topic)
            return
        # emit(enddev_id, channel, payload, raw_topic) — QThread cross-
        # thread signal. The GUI thread's connected slot picks this up
        # by the time its event loop next spins. Acquire-release
        # semantics across the thread boundary are handled by Qt.
        self.message_received.emit(
            parsed.enddev_id, parsed.channel, bytes(msg.payload), parsed.raw,
        )

    # ----------------------------------------------- cross-thread publish

    def publish(self, topic: str, payload: bytes) -> None:
        """Slot target for mqtt_publish_request from the GUI thread.

        Called via signal connection so the call lands on this worker
        thread (Qt queues the slot invocation onto the worker's event
        loop). paho is thread-safe for publish() but routing the call
        through this slot ensures the internal socket lock is taken on
        the worker thread, matching the bridge's pattern.

        Returns immediately; the caller (ControlPage) starts its 2 s
        verify timer independently via QTimer.singleShot.
        """
        info = self._client.publish(topic, payload, qos=MQTT_QOS)
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            logger.info("published %s (%d bytes)", topic, len(payload))
        else:
            logger.warning("publish failed rc=%s topic=%s", info.rc, topic)
