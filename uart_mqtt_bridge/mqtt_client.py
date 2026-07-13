"""MQTT Gateway client — uplink publisher with exponential back-off reconnect.

Current scope: uplink only — publish telemetry frames to
enddev{SourceID}/sensor/{TypeID} (SRS-PY-02).

Designed for extension: downlink subscriptions will be added later
(SRS-PY-03, SRS-PY-04).
"""

import logging
import threading
import time
from typing import Callable

import paho.mqtt.client as mqtt

from config import (
    MQTT_BROKER_HOST, MQTT_BROKER_PORT,
    MQTT_QOS, MQTT_RETAIN,
    MQTT_RECONNECT_MIN_S, MQTT_RECONNECT_MAX_S,
)

logger = logging.getLogger("mqtt")


# Callback signature for downlink (future)
DownlinkCallback = Callable[[str, bytes], None]


class MQTTGateway:
    """Manage connection to MQTT broker; publish uplink frames.

    Reconnection uses exponential back-off starting at
    MQTT_RECONNECT_MIN_S, capped at MQTT_RECONNECT_MAX_S (SRS-PY-06).

    The publish() method is thread-safe and non-blocking.
    """

    def __init__(self) -> None:
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,  # paho-mqtt v2 API
            protocol=mqtt.MQTTv311,
        )
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

        self._lock = threading.Lock()
        self._connected = False
        self._pending: list[tuple[str, str]] = []  # buffer while disconnected

        # Publishing stats
        self.messages_published: int = 0
        self.messages_dropped: int = 0

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        """Connect to the broker (non-blocking; launches network loop)."""
        self._do_connect()
        self._client.loop_start()

    def stop(self) -> None:
        """Disconnect and stop the network loop."""
        self._client.loop_stop()
        with self._lock:
            try:
                self._client.disconnect()
            except Exception:
                pass

    def publish(self, topic: str, payload: str) -> None:
        """Publish a message. Buffers if currently disconnected."""
        with self._lock:
            if self._connected:
                info = self._client.publish(topic, payload,
                                            qos=MQTT_QOS, retain=MQTT_RETAIN)
                if info.rc == mqtt.MQTT_ERR_SUCCESS:
                    self.messages_published += 1
                else:
                    self.messages_dropped += 1
                    logger.warning("publish failed: rc=%s topic=%s",
                                   info.rc, topic)
            else:
                self._pending.append((topic, payload))

    # -------------------------------------------------------- MQTT callbacks

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        logger.info("connected to %s:%d (rc=%s)",
                     MQTT_BROKER_HOST, MQTT_BROKER_PORT, reason_code)
        with self._lock:
            self._connected = True
            # Flush buffered messages
            pending, self._pending = self._pending, []
        for topic, payload in pending:
            self.publish(topic, payload)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        logger.warning("disconnected (rc=%s)", reason_code)
        with self._lock:
            self._connected = False
        # Reconnect is handled by _do_connect in a dedicated thread

    # -------------------------------------------------------------- internal

    def _do_connect(self) -> None:
        """Background reconnect loop with exponential back-off."""

        def _reconnect_loop():
            delay = MQTT_RECONNECT_MIN_S
            while True:
                with self._lock:
                    if self._connected:
                        return
                try:
                    self._client.connect(MQTT_BROKER_HOST,
                                         MQTT_BROKER_PORT, keepalive=60)
                    return
                except OSError:
                    logger.info("reconnect in %ds", delay)
                    time.sleep(delay)
                    delay = min(delay * 2, MQTT_RECONNECT_MAX_S)

        t = threading.Thread(target=_reconnect_loop,
                             name="mqtt-reconnect", daemon=True)
        t.start()

    # ------------------------------------------------------- future downlink
    #
    # def subscribe(self, topic: str, callback: DownlinkCallback) -> None:
    #     """Subscribe to a topic and register a callback (SRS-PY-03)."""
    #     ...