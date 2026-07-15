#!/usr/bin/env python3
"""UART ↔ MQTT Bridge — STM32WL LoRa Gateway companion application.

Wires three concerns together:
    1. UARTParser   — reads 8-byte frames from the STM32WL gateway VCP.
    2. topic_map    — translates each frame to MQTT (topic, payload) tuples.
    3. MQTTGateway  — publishes those tuples to the broker.

Uplink-only for now (SRS-PY-02). Downlink actuator dispatch
(SRS-PY-03, SRS-PY-04) and ACK handling (SRS-ED-05) will be added in
future iterations; the module seams in mqtt_client.py and topic_map.py
already expose subscribe() and a downlink-frame builder placeholder.

Logging format per SRS-PY-05:
    [HH:MM:SS.mmm] DIR SRC->DST TID=0xNN DATA=HH HH HH HH topic
"""

import logging
import signal
import sys
import threading
from datetime import datetime

from config import LOG_DATEFMT, LOG_FORMAT, MQTT_BROKER_HOST, MQTT_BROKER_PORT
from lora_frame import LoraFrame
from mqtt_client import MQTTGateway
from topic_map import uplink_publications
from uart_parser import UARTParser

# --------------------------------------------------------------------- logging

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=LOG_DATEFMT)
logger = logging.getLogger("bridge")


def _log_frame(direction: str, frame: LoraFrame, topic_summary: str) -> None:
    """Emit a one-line SRS-PY-05 frame log entry."""
    now = datetime.now()
    ts = now.strftime("%H:%M:%S.") + f"{now.microsecond // 1000:03d}"
    data_hex = " ".join(f"{b:02X}" for b in frame.data)
    logger.info(
        "[%s] %s 0x%02X->0x%02X TID=0x%02X DATA=%s %s",
        ts,
        direction,
        frame.source_addr,
        frame.dest_addr,
        frame.type_id,
        data_hex,
        topic_summary,
    )


# --------------------------------------------------------- frame dispatcher

def _make_on_frame(mqtt: MQTTGateway):
    """Build the callback invoked by UARTParser.on each complete frame."""

    def on_frame(raw: bytes) -> None:
        frame = LoraFrame.decode(raw)
        if frame is None:
            logger.warning("undecodable frame discarded: %s", raw.hex(" "))
            return

        pubs = uplink_publications(frame)
        if not pubs:
            logger.debug(
                "no uplink publications for TID=0x%02X (frame discarded)",
                frame.type_id,
            )
            return

        # SRS-PY-05: summarize topic (multiple pubs share a prefix, so join).
        topic_summary = ", ".join(p.label for p in pubs)
        _log_frame("UP", frame, topic_summary)

        for pub in pubs:
            mqtt.publish(pub.topic, pub.payload)
            logger.debug("published  %s (%d bytes)", pub.topic, len(pub.payload))

    return on_frame


# --------------------------------------------------------------------- shutdown

_shutdown = threading.Event()


def _handle_signal(signum, _frame) -> None:
    logger.info("shutdown signal %d received", signum)
    _shutdown.set()


def main() -> int:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("UART-MQTT bridge starting")
    logger.info("  broker: %s:%d", MQTT_BROKER_HOST, MQTT_BROKER_PORT)

    mqtt = MQTTGateway()
    mqtt.start()

    uart = UARTParser(frame_callback=_make_on_frame(mqtt))
    uart.start()

    logger.info("bridge running — UART -> MQTT uplink active")

    # Block on the shutdown event; periodic status ticks every few seconds
    # so the process visibly reports liveness in long-running deployments.
    while not _shutdown.wait(timeout=5.0):
        logger.debug(
            "stats: frames=%d invalid=%d published=%d dropped=%d",
            uart.frames_received,
            uart.invalid_frames,
            mqtt.messages_published,
            mqtt.messages_dropped,
        )

    logger.info("shutting down")
    uart.stop()
    mqtt.stop()
    logger.info(
        "stopped. final: frames=%d invalid=%d published=%d dropped=%d",
        uart.frames_received,
        uart.invalid_frames,
        mqtt.messages_published,
        mqtt.messages_dropped,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
