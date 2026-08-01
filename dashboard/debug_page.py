"""DEBUG page — Tab 2: broker state + raw MQTT traffic log.

Handoff § Section rhythm / Tab 2. The page is a passive observer: it
formats incoming / outgoing MQTT messages as ``[HH:MM:SS] RX|TX /topic
[hex payload]`` lines and appends them to a read-only QPlainTextEdit.
The oldest lines roll off at ``DEBUG_LOG_MAX_LINES`` (handoff spec).

No interaction states — the log is read-only (``setReadOnly(True)``)
per the handoff § UI component inventory. Auto-scroll uses
``ensureCursorVisible`` (no animation, no smoothing).
"""

import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from config import DEBUG_LOG_MAX_LINES, MQTT_BROKER_HOST, MQTT_BROKER_PORT


class DebugPage(QWidget):
    """Tab 2 — broker state label + read-only MQTT traffic log."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    # ----------------------------------------------------------- UI build

    def _build_ui(self) -> None:
        """Vertical layout: section head, broker state label, log pane.
        """
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        head = QLabel("DEBUG & HEALTH LOG", self)
        head.setObjectName("sectionHead")
        root.addWidget(head)

        # Broker state label — text gets updated via set_broker_state;
        # QSS attribute selector ``[state="connected"]`` drives colour.
        self._broker_label = QLabel(
            f"Broker: {MQTT_BROKER_HOST}:{MQTT_BROKER_PORT} "
            f"\u25cf waiting...",
            self,
        )
        self._broker_label.setObjectName("readout")
        self._broker_label.setProperty("state", "disconnected")
        root.addWidget(self._broker_label)

        # Read-only log pane. QPlainTextEdit is the right surface for a
        # large body of monospace text — it doesn't incur QTextDocument
        # layout cost on every append the way QTextEdit does for rich
        # text. maxBlockCount caps the rolling history.
        self._log = QPlainTextEdit(self)
        self._log.setObjectName("debugLog")
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(DEBUG_LOG_MAX_LINES)
        # Wrap off — long hex payloads should overflow horizontally
        # off-screen rather than reflow into multi-line entries that
        # confuse the per-message line count.
        self._log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        root.addWidget(self._log, stretch=1)

    # ------------------------------------------------------- public slots

    def set_broker_state(self, state: str, reason_code: int | None) -> None:
        """Update the broker-state label after a connect/disconnect event.

        ``state`` is one of ``"connected"``, ``"disconnected"``,
        ``"failed"`` (matches MqttWorker.broker_state_changed). The
        QSS attribute selector ``[state="..."]`` in tokens.qss drives
        the colour swap (green / red / muted).
        """
        host = f"{MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}"
        if state == "connected":
            text = f"Broker: {host} \u25cf connected"
        elif state == "disconnected":
            text = f"Broker: {host} \u25cf disconnected"
        elif state == "failed":
            rc = reason_code if reason_code is not None else "?"
            text = f"Broker: {host} \u25cf failed (rc={rc})"
        else:
            text = f"Broker: {host} \u25cf {state}"
        self._broker_label.setText(text)
        self._broker_label.setProperty("state", state)
        # Re-apply QSS so the attribute-selector colour swap takes.
        style = self._broker_label.style()
        if style is not None:
            style.unpolish(self._broker_label)
            style.polish(self._broker_label)
        # Also log the state transition into the log pane.
        suffix = (
            f" (rc={reason_code})" if reason_code is not None else ""
        )
        self._append(f"-- broker {state}{suffix}")

    def log_rx(self, topic: str, payload: bytes) -> None:
        """Format an incoming MQTT message as one log line."""
        hex_str = payload.hex(" ")
        self._append(
            f"{self._ts()} RX / {topic} [{hex_str}]",
        )

    def log_tx(self, topic: str, payload: bytes) -> None:
        """Format an outgoing MQTT publish as one log line."""
        hex_str = payload.hex(" ")
        self._append(
            f"{self._ts()} TX / {topic} [{hex_str}]",
        )

    # ----------------------------------------------------------- helpers

    @staticmethod
    def _ts() -> str:
        """Wall-clock timestamp for log lines, HH:MM:SS."""
        return time.strftime("%H:%M:%S")

    def _append(self, line: str) -> None:
        """Append one prefixed line and auto-scroll to it. The
        maximum-block-count set in ``_build_ui`` rolls the oldest
        lines off automatically — no manual trimming needed.
        """
        self._log.appendPlainText(line)
        # ensureCursorVisible scrolls to the new cursor position
        # without animation (matches handoff § slop-test gate #12).
        self._log.ensureCursorVisible()
