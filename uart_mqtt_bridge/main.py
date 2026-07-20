#!/usr/bin/env python3
"""UART <-> MQTT Bridge - STM32WL LoRa Gateway companion application.

Wires three concerns together:
    1. UARTParser   - reads 8-byte frames from the STM32WL gateway VCP
                      and writes 8-byte downlink frames to it.
    2. topic_map    - translates frames to MQTT (topic, payload) tuples
                      (uplink) and MQTT messages to UART frames (downlink).
    3. MQTTGateway  - publishes uplink tuples to the broker and dispatches
                      subscribed downlink messages to the UART.

Uplink (SRS-PY-02):
    UART telemetry frames -> enddev{N}/sensor, /rssi, /snr topics.
    UART ACK frames       -> enddev{N}/ack topic.  (SRS-ED-05)

Downlink (SRS-PY-03, SRS-PY-04):
    Subscribes to enddev+/actuator.  On message, builds an 8-byte Command
    UART frame and writes it to the serial port; the gateway firmware
    forwards it over LoRa to the addressed EndDevice.

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
from topic_map import (
    DOWNLINK_TOPIC_WILDCARD,
    build_downlink_frame,
    parse_downlink_payload,
    parse_downlink_topic,
    uplink_publications,
)
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


# ----------------------------------------------------- uplink (UART -> MQTT)

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


# --------------------------------------------------- downlink (MQTT -> UART)

def _make_on_actuator(uart: UARTParser):
    """Build the MQTT callback for enddev+/actuator messages (SRS-PY-03)."""

    def on_actuator(topic: str, payload: bytes) -> None:
        dest_addr = parse_downlink_topic(topic)
        if dest_addr is None:
            logger.warning("downlink: topic %r does not match enddev{N}/actuator",
                           topic)
            return

        parsed = parse_downlink_payload(payload)
        if parsed is None:
            logger.warning("downlink: malformed payload %r on %s",
                           payload.hex(" "), topic)
            return
        actuator_id, cmd = parsed

        raw = build_downlink_frame(
            dest_addr=dest_addr, actuator_id=actuator_id, cmd=cmd,
        )
        # Log the outgoing frame using the same SRS-PY-05 format.
        # We reconstruct a LoraFrame from the raw bytes purely for logging.
        logged = LoraFrame.decode(raw)
        if logged is not None:
            _log_frame("DOWN", logged, topic)

        ok = uart.send_frame(raw)
        if ok:
            logger.debug(
                "downlink: queued %d bytes to UART for enddev%d actuator=%d cmd=%d",
                len(raw), dest_addr, actuator_id, cmd,
            )
        else:
            logger.warning(
                "downlink: UART write failed; command dropped (enddev%d "
                "actuator=%d cmd=%d)",
                dest_addr, actuator_id, cmd,
            )

    return on_actuator


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

    # Subscribe to actuator commands and route them to the UART.
    mqtt.subscribe(DOWNLINK_TOPIC_WILDCARD, _make_on_actuator(uart))

    logger.info("bridge running - uplink + downlink active")

    # Block on the shutdown event; periodic status ticks every few seconds
    # so the process visibly reports liveness in long-running deployments.
    while not _shutdown.wait(timeout=5.0):
        logger.debug(
            "stats: rx_frames=%d invalid=%d tx_frames=%d tx_errors=%d "
            "published=%d dropped=%d",
            uart.frames_received,
            uart.invalid_frames,
            uart.frames_sent,
            uart.send_errors,
            mqtt.messages_published,
            mqtt.messages_dropped,
        )

    logger.info("shutting down")
    uart.stop()
    mqtt.stop()
    logger.info(
        "stopped. final: rx_frames=%d invalid=%d tx_frames=%d tx_errors=%d "
        "published=%d dropped=%d",
        uart.frames_received,
        uart.invalid_frames,
        uart.frames_sent,
        uart.send_errors,
        mqtt.messages_published,
        mqtt.messages_dropped,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
