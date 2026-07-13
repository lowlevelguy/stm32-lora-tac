"""UART frame parser with SOF (0xA5) synchronization.

Receives raw bytes from a serial port, synchronises on the 0xA5 start-of-frame
delimiter, reads the next 7 bytes to assemble an 8-byte frame, and hands the
complete raw frame to a registered callback.  Invalid frames (missing SOF,
wrong length) are counted and discarded (SRS-UART-03).
"""

import collections
import threading
import time
from typing import Callable

import serial

from config import UART_PORT, UART_BAUDRATE, UART_RECONNECT_INTERVAL_S
from lora_frame import LoraFrame, _SOF, _FRAME_LENGTH


FrameCallback = Callable[[bytes], None]


class UARTParser:
    """Owns the serial port, reads raw bytes, synchronises on SOF.

    Runs a reader thread that feeds a synchronisation state machine:
      - HUNT:  discard bytes until SOF (0xA5)
      - FRAME: read the next 7 bytes to complete an 8-byte frame
      - DONE:  hand off the raw frame via callback, return to HUNT

    The callback is invoked on the reader thread; keep it short.
    """

    def __init__(self, frame_callback: FrameCallback) -> None:
        self._callback = frame_callback
        self._port: serial.Serial | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()

        # Counters
        self.frames_received: int = 0
        self.invalid_frames: int = 0

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        """Open the serial port and launch the reader thread."""
        self._running = True
        self._thread = threading.Thread(target=self._reader_loop,
                                        name="uart-reader")
        self._thread.start()

    def stop(self) -> None:
        """Close the port and signal the reader thread to exit."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        with self._lock:
            if self._port is not None:
                try:
                    self._port.close()
                except serial.SerialException:
                    pass
                self._port = None

    # --------------------------------------------------------------- internal

    def _open_port(self) -> serial.Serial | None:
        try:
            return serial.Serial(UART_PORT, baudrate=UART_BAUDRATE,
                                 timeout=0.05)
        except serial.SerialException:
            return None

    def _reader_loop(self) -> None:
        """Reconnect loop + byte-level SOF synchronisation.

        Reads in chunks (in_waiting) to avoid timeout granularity issues;
        then runs a byte-by-byte SOF state machine against the chunk.
        """

        HUNT, FRAME = 0, 1

        buf: collections.deque[int] = collections.deque(maxlen=_FRAME_LENGTH)

        state = HUNT
        payload: list[int] = []
        payload_needed = 0

        while self._running:
            # ------------------------------------------------------------ connect
            with self._lock:
                port = self._port
            if port is None or not port.is_open:
                port = self._open_port()
                if port is not None:
                    with self._lock:
                        self._port = port
                else:
                    time.sleep(UART_RECONNECT_INTERVAL_S)
                    continue

            # -------------------------------------------------------------- read
            try:
                n = port.in_waiting or 1
                chunk = port.read(n)
            except serial.SerialException:
                with self._lock:
                    if self._port is port:
                        try:
                            port.close()
                        except serial.SerialException:
                            pass
                        self._port = None
                state = HUNT
                payload.clear()
                continue

            if not chunk:
                continue

            for val in chunk:
                if state == HUNT:
                    if val == _SOF:
                        state = FRAME
                        payload_needed = _FRAME_LENGTH - 1
                        payload.clear()
                    else:
                        self.invalid_frames += 1
                    continue

                # state == FRAME
                payload.append(val)
                payload_needed -= 1
                if payload_needed == 0:
                    full = bytes([_SOF, *payload])
                    self.frames_received += 1
                    try:
                        self._callback(full)
                    except Exception:
                        pass
                    state = HUNT
                    payload.clear()
        # ------------------------------------------------------------ shutdown

    # State machine constants (for tests/debug access)
    _HUNT, _FRAME = 0, 1