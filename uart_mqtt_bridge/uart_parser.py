"""UART reader with SOF (0xA5) synchronisation and automatic port discovery.

SRS-UART-03: synchronise on SOF (0xA5), read exactly the next 7 bytes,
discard frames with missing SOF/incorrect length, increment invalid counter.
SRS-PY-01:   auto-detect STM32WL Nucleo VCP by VID/PID, fall back to scanning
             available COM ports; connect within 3 s.
SRS-PY-06:   on UART disconnection, retry every UART_RECONNECT_INTERVAL_S.

The reader runs on its own thread. A registered FrameCallback is invoked on
that thread for each complete 8-byte frame; keep the callback short.
"""

import collections
import logging
import threading
import time
from typing import Callable

import serial
from serial.tools import list_ports

from config import (
    UART_BAUDRATE,
    UART_PORT,
    UART_RECONNECT_INTERVAL_S,
    UART_VID_PID_CANDIDATES,
)
from lora_frame import FRAME_LENGTH, SOF

logger = logging.getLogger("uart")

FrameCallback = Callable[[bytes], None]


class UARTParser:
    """Owns the serial port, reads raw bytes, synchronises on SOF.

    State machine (per byte):

        HUNT  : discard bytes until SOF (0xA5), then -> FRAME
        FRAME : collect next 7 bytes; on completion dispatch -> HUNT
                non-SOF bytes during HUNT increment invalid_frames counter.
    """

    _HUNT = 0
    _FRAME = 1

    def __init__(self, frame_callback: FrameCallback) -> None:
        self._callback = frame_callback
        self._port: serial.Serial | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()

        # Observable counters
        self.frames_received: int = 0
        self.invalid_frames: int = 0
        self.reconnect_count: int = 0

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        """Open the serial port and launch the reader thread."""
        self._running = True
        self._thread = threading.Thread(
            target=self._reader_loop, name="uart-reader", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the reader thread to exit and close the port."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._close_port()

    # ----------------------------------------------------- port discovery

    @staticmethod
    def _discover_port() -> str | None:
        """Find the STM32WL Nucleo VCP (SRS-PY-01).

        Strategy:
          1. Match by VID:PID against UART_VID_PID_CANDIDATES.
          2. Fallback: if a single /dev/ttyACM* or /dev/ttyUSB* exists,
             return it (common on Linux with only one device plugged in).
          3. Otherwise return None.
        """
        ports = list(list_ports.comports())
        if not ports:
            return None

        # 1. VID/PID match (preferred — most reliable across platforms).
        for p in ports:
            if p.vid is None or p.pid is None:
                continue
            vid_str = f"{p.vid:04X}"
            pid_str = f"{p.pid:04X}"
            for cand_vid, cand_pid in UART_VID_PID_CANDIDATES:
                if vid_str.lower() == cand_vid.lower() and \
                   pid_str.lower() == cand_pid.lower():
                    return p.device

        # 2. Single ACM device heuristic (Linux convenience).
        acms = [p.device for p in ports if p.device.startswith("/dev/ttyACM")]
        if len(acms) == 1:
            return acms[0]

        # 3. Single USB-serial heuristic (FTDI cables etc.).
        usbs = [p.device for p in ports if p.device.startswith("/dev/ttyUSB")]
        if len(usbs) == 1:
            return usbs[0]

        return None

    def _open_port(self) -> serial.Serial | None:
        """Open a serial port. Returns an open Serial or None on failure."""
        port_name = UART_PORT or self._discover_port()
        if port_name is None:
            logger.warning("no STM32WL VCP port detected")
            return None
        try:
            port = serial.Serial(
                port_name,
                baudrate=UART_BAUDRATE,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.05,
            )
            # Drain any pending bytes so the SOF state machine starts clean.
            port.reset_input_buffer()
            if self.reconnect_count == 0:
                logger.info("opened %s @ %d baud (8N1)", port_name, UART_BAUDRATE)
            else:
                logger.info(
                    "reopened %s @ %d baud (attempt %d)",
                    port_name, UART_BAUDRATE, self.reconnect_count + 1,
                )
            return port
        except serial.SerialException as exc:
            logger.warning("could not open %s: %s", port_name, exc)
            return None

    def _close_port(self) -> None:
        with self._lock:
            if self._port is not None:
                try:
                    self._port.close()
                except serial.SerialException:
                    pass
                self._port = None

    # ----------------------------------------------------- reader thread

    def _reader_loop(self) -> None:
        """Reconnect loop + byte-level SOF synchronisation.

        Reads in chunks (in_waiting) to avoid timeout granularity issues,
        then runs a byte-by-byte SOF state machine against each chunk.
        """
        state = self._HUNT
        payload: list[int] = []
        payload_needed = 0

        while self._running:
            # ----------------------------------------------------- connect
            with self._lock:
                port = self._port
            if port is None or not port.is_open:
                port = self._open_port()
                if port is not None:
                    with self._lock:
                        self._port = port
                    # Reset SOF state on every fresh connection.
                    state = self._HUNT
                    payload.clear()
                    self.reconnect_count += 1
                else:
                    time.sleep(UART_RECONNECT_INTERVAL_S)
                    continue

            # ---------------------------------------------------------- read
            try:
                n = port.in_waiting or 1
                chunk = port.read(n)
            except serial.SerialException:
                logger.warning("serial read error; closing port")
                with self._lock:
                    if self._port is port:
                        try:
                            port.close()
                        except serial.SerialException:
                            pass
                        self._port = None
                state = self._HUNT
                payload.clear()
                continue

            if not chunk:
                continue

            # ------------------------------------------- byte state machine
            for val in chunk:
                if state == self._HUNT:
                    if val == SOF:
                        state = self._FRAME
                        payload_needed = FRAME_LENGTH - 1
                        payload.clear()
                    else:
                        self.invalid_frames += 1
                    continue

                # state == _FRAME
                payload.append(val)
                payload_needed -= 1
                if payload_needed == 0:
                    full = bytes([SOF, *payload])
                    self.frames_received += 1
                    try:
                        self._callback(full)
                    except Exception:
                        logger.exception("frame callback raised")
                    state = self._HUNT
                    payload.clear()
        # ------------------------------------------------------ shutdown
        self._close_port()
