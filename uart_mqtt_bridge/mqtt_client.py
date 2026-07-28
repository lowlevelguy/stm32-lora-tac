"""MQTT gateway client — paho-mqtt wrapper with back-off reconnection.

Scope:
    - SRS-PY-02:       publish telemetry uplink frames to
                       test.mosquitto.org:1883.
    - SRS-PY-03/04:    subscribe(topic, callback) API used by main.py to
                       receive actuator-command downlink messages.
    - SRS-PY-06:       on MQTT disconnection, automatic reconnection every
                       5 s with exponential back-off capped at 60 s.
"""

import logging
import threading
import time
from typing import Callable

import paho.mqtt.client as mqtt

from config import (
    MQTT_BROKER_HOST,
    MQTT_BROKER_PORT,
    MQTT_KEEPALIVE_S,
    MQTT_QOS,
    MQTT_RECONNECT_MAX_S,
    MQTT_RECONNECT_MIN_S,
    MQTT_RETAIN,
)

logger = logging.getLogger("mqtt")

# Subscription callback signature (future downlink):
#   callback(topic: str, payload: bytes) -> None
DownlinkCallback = Callable[[str, bytes], None]


class MQTTGateway:
    """Manages the MQTT connection and publishes uplink frames.

    Thread-safety: publish() can be called from any thread.
    """

    def __init__(self) -> None:
        # paho-mqtt v2 API.
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            protocol=mqtt.MQTTv311,
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        self._lock = threading.Lock()
        self._connected = False
        self._pending: list[tuple[str, bytes]] = []  # buffered while disconnected
        self._stop_event = threading.Event()
        self._reconnect_thread: threading.Thread | None = None

        # Registered downlink callbacks — populated by subscribe() and
        # dispatched by _on_message when matching MQTT messages arrive.
        self._subscriptions: dict[str, DownlinkCallback] = {}

        # Observable stats.
        self.messages_published: int = 0
        self.messages_dropped: int = 0

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        """Connect to the broker and start the network loop."""
        self._do_connect()
        self._client.loop_start()
        # Spawn a watchdog that reconnects if the connection drops.
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_watchdog, name="mqtt-reconnect", daemon=True
        )
        self._reconnect_thread.start()

    def stop(self) -> None:
        """Signal shutdown, stop threads, disconnect cleanly."""
        self._stop_event.set()
        self._client.loop_stop()
        if self._reconnect_thread is not None:
            self._reconnect_thread.join(timeout=2.0)
        try:
            self._client.disconnect()
        except Exception:
            pass

    def publish(self, topic: str, payload: bytes | str) -> None:
        """Publish a message. If disconnected, buffer for later flush."""
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        with self._lock:
            if self._connected:
                info = self._client.publish(
                    topic, payload, qos=MQTT_QOS, retain=MQTT_RETAIN
                )
                if info.rc == mqtt.MQTT_ERR_SUCCESS:
                    self.messages_published += 1
                else:
                    self.messages_dropped += 1
                    logger.warning("publish failed (rc=%s) topic=%s", info.rc, topic)
            else:
                self._pending.append((topic, payload))
                logger.debug("buffered (disconnected) topic=%s", topic)

    def subscribe(self, topic: str, callback: DownlinkCallback) -> None:
        """Register a downlink callback and subscribe to a topic.

        Used by main.py to wire SRS-PY-03 actuator-command downlink
        handling. Subscriptions are re-armed automatically on every
        reconnect via _on_connect.
        """
        with self._lock:
            self._subscriptions[topic] = callback
            if self._connected:
                self._client.subscribe(topic, qos=MQTT_QOS)
        logger.info("subscribed to %s", topic)

    # -------------------------------------------------------- MQTT callbacks

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            logger.info(
                "connected to %s:%d", MQTT_BROKER_HOST, MQTT_BROKER_PORT
            )
            with self._lock:
                self._connected = True
                # Re-arm any downlink subscriptions on the new session.
                for topic in self._subscriptions:
                    client.subscribe(topic, qos=MQTT_QOS)
                # Flush buffered uplink messages.
                pending, self._pending = self._pending, []
            for topic, payload in pending:
                self.publish(topic, payload)
        else:
            logger.error("connect refused (rc=%s)", reason_code)
            # The watchdog will retry.

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        logger.warning("disconnected (rc=%s)", reason_code)
        with self._lock:
            self._connected = False

    def _on_message(self, client, userdata, msg):
        """Dispatch incoming MQTT messages to registered downlink callbacks."""
        with self._lock:
            callback = None
            for pattern, cb in self._subscriptions.items():
                if mqtt.topic_matches_sub(pattern, msg.topic):
                    callback = cb
                    break
        if callback is not None:
            try:
                callback(msg.topic, msg.payload)
            except Exception:
                logger.exception("downlink callback raised for topic %s", msg.topic)

    # ----------------------------------------------------- reconnect logic

    def _do_connect(self) -> None:
        """Initiate a single connect attempt (non-blocking)."""
        try:
            self._client.connect(
                MQTT_BROKER_HOST, MQTT_BROKER_PORT, keepalive=MQTT_KEEPALIVE_S
            )
        except OSError as exc:
            logger.warning(
                "initial connect to %s:%d failed: %s",
                MQTT_BROKER_HOST, MQTT_BROKER_PORT, exc,
            )
            # The watchdog will retry.

    def _reconnect_watchdog(self) -> None:
        """Background loop: detect drops and reconnect with exponential back-off.

        paho's loop_start() already attempts reconnection when the socket
        closes, but it is not always reliable across versions. This watchdog
        provides a deterministic back-off independently of paho internals.
        """
        delay = MQTT_RECONNECT_MIN_S
        while not self._stop_event.is_set():
            if self._stop_event.wait(delay):
                break
            with self._lock:
                connected = self._connected
            if connected:
                delay = MQTT_RECONNECT_MIN_S  # reset back-off
                continue
            # Try to reconnect.
            try:
                logger.info(
                    "reconnect attempt to %s:%d (delay=%ds)",
                    MQTT_BROKER_HOST, MQTT_BROKER_PORT, delay,
                )
                self._client.reconnect()
                delay = MQTT_RECONNECT_MIN_S
            except Exception as exc:
                logger.warning("reconnect failed: %s", exc)
                delay = min(delay * 2, MQTT_RECONNECT_MAX_S)
