#!/usr/bin/env python3
"""UART ↔ MQTT Bridge — STM32WL LoRa Gateway companion.

Periodically:
  LoRa uplink telemetry (TypeID=0x01, 8-byte frames via UART)
    → decoded → published to topic enddev{src}/sensor/{type}

Designed for extension: MQTT downlink → UART TX (SRS-PY-03, SRS-PY-04)
will be added in a future iteration.
"""

import logging
import signal
import threading
import time

from lora_frame import LoraFrame, FrameType
from mqtt_client import MQTTGateway
from topic_map import uplink_topic, uplink_payload
from uart_parser import UARTParser

# ------------------------------------------------------------------- logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bridge")


# --------------------------------------------------------- frame dispatcher

def _on_frame(raw: bytes) -> None:
    """Callback from UARTParser: decode frame, route to MQTT uplink."""
    frame = LoraFrame.decode(raw)
    if frame is None:
        logger.warning("undecodable frame discarded: %s", raw.hex(" "))
        return

    logger.info("UP  SRC=0x%02X DST=0x%02X TID=0x%02X DATA=%s",
                frame.source_addr, frame.dest_addr, frame.type_id,
                " ".join(f"{b:02X}" for b in frame.data))

    if frame.type_id != FrameType.TELEMETRY:
        logger.debug("non-telemetry frame ignored (TID=0x%02X)", frame.type_id)
        return

    topic = uplink_topic(frame)
    payload = uplink_payload(frame)
    if topic is None or payload is None:
        return

    mqtt.publish(topic, payload)
    logger.debug("published  %s = %s", topic, payload)


# --------------------------------------------------------------------- main

_shutdown_flag = threading.Event()


def _handle_signal(signum, frame):
    logger.info("shutting down (signal %d)", signum)
    _shutdown_flag.set()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    mqtt = MQTTGateway()
    mqtt.start()

    uart = UARTParser(frame_callback=_on_frame)
    uart.start()

    logger.info("bridge running — UART → MQTT uplink active")
    logger.info("  broker:  %s:%d", "test.mosquitto.org", 1883)

    while not _shutdown_flag.wait(timeout=1):
        pass

    uart.stop()
    mqtt.stop()
    logger.info("bridge stopped")
