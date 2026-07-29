"""Unit tests for uart_parser.UARTParser — SOF state machine, port
discovery, and the downlink send_frame path.

The reader loop interleaves threading, serial I/O, and the byte-by-byte
SOF state machine; rather than refactor the production code we drive
``_reader_loop`` directly on the calling thread with a fake Serial that
returns scripted chunks. Each fake's ``read()`` sets ``parser._running
= False`` once its chunk queue drains, so the loop exits after the
scripted data is consumed and the test never spawns a real thread.

This keeps the SOF state-machine tests deterministic and faithful to
the real byte path (no extracted helper that might drift from the
production code).
"""

import serial
import serial.tools.list_ports

import pytest

from uart_parser import UARTParser


_FRAME = b"\xA5\x01\x00\x01\x01\x1A\xCC\xFB"


class _FakeSerial:
    """Minimal in-memory serial port double for driving _reader_loop.

    Maintains a FIFO of pending bytes. ``read(n)`` pops ``min(n,
    available)`` of them, mimicking pyserial's behaviour for short
    reads when fewer bytes are waiting than requested. When the buffer
    is exhausted, ``read`` returns ``b""`` AND flips the parser's
    ``_running`` flag to False so the loop exits promptly without the
    test waiting on a real timeout.
    """

    def __init__(self, parser: UARTParser) -> None:
        self._buf = bytearray()
        self._parser = parser
        self.is_open = True
        # Tracks write() calls for send_frame tests.
        self.written: list[bytes] = []

    @property
    def in_waiting(self) -> int:
        return len(self._buf)

    def feed(self, data: bytes) -> None:
        self._buf.extend(data)

    def read(self, n: int) -> bytes:
        if not self._buf:
            # Drain sentinel: signal the parser loop to exit.
            self._parser._running = False  # noqa: SLF001
            return b""
        take = min(n, len(self._buf))
        out = bytes(self._buf[:take])
        del self._buf[:take]
        return out

    def write(self, data: bytes) -> int:
        self.written.append(data)
        return len(data)

    @staticmethod
    def reset_input_buffer() -> None:
        pass

    def close(self) -> None:
        self.is_open = False


def _make_parser_with_port(chunks: list[bytes]) -> tuple[UARTParser, _FakeSerial]:
    """Build a UARTParser wired to a _FakeSerial pre-fed with ``chunks``.

    The callback collects complete frames into a list returned to the
    caller indirectly via the parser's ``frames_received`` counter; the
    raw bytes themselves are captured so byte-level assertions work too.
    """
    captured: list[bytes] = []
    parser = UARTParser(frame_callback=captured.append)
    fake = _FakeSerial(parser)
    for c in chunks:
        fake.feed(c)
    parser._port = fake  # type: ignore[assignment]  # noqa: SLF001
    parser._running = True  # type: ignore[assignment]  # noqa: SLF001
    return parser, fake


def _run_reader(parser: UARTParser) -> None:
    """Invoke _reader_loop synchronously; exits when the fake signals stop."""
    parser._reader_loop()  # noqa: SLF001


# ============================================================ SOF machine
def test_clean_frame_in_one_chunk():
    """A single 8-byte chunk decodes to exactly one frame.

    Locks the happy path used by every other SOF test as a foundation.
    """
    parser, _ = _make_parser_with_port([_FRAME])
    _run_reader(parser)
    assert parser.frames_received == 1
    assert parser.invalid_frames == 0


def test_frame_split_across_chunks():
    """A frame split across two reads must still assemble into one frame.

    The state machine retains HUNT<->FRAME state between reads, so a
    partial frame in one chunk and its tail in the next must NOT be
    rejected as malformed. This is the regression net for the
    inter-chunk state retention that broke in early prototypes.
    """
    parser, _ = _make_parser_with_port([
        _FRAME[:4],   # partial: SOF + 3 bytes
        _FRAME[4:],   # tail: 4 bytes
    ])
    _run_reader(parser)
    assert parser.frames_received == 1
    assert parser.invalid_frames == 0


def test_two_frames_in_one_chunk():
    """Two concatenated frames in a single read must dispatch twice."""
    parser, _ = _make_parser_with_port([_FRAME, _FRAME])
    _run_reader(parser)
    assert parser.frames_received == 2
    assert parser.invalid_frames == 0


def test_garbage_prefix_increments_invalid_counter():
    """Bytes seen in HUNT state that aren't SOF are discarded, each
    incrementing invalid_frames (SRS-UART-03)."""
    parser, _ = _make_parser_with_port([b"\xFF\xFE" + _FRAME])
    _run_reader(parser)
    assert parser.frames_received == 1
    assert parser.invalid_frames == 2


def test_garbage_after_frame_increments_invalid_counter():
    """A trailing garbage byte after a complete frame is consumed in
    HUNT state and increments invalid_frames."""
    parser, _ = _make_parser_with_port([_FRAME + b"\xFF"])
    _run_reader(parser)
    assert parser.frames_received == 1
    assert parser.invalid_frames == 1


def test_a5_in_frame_body_is_data_not_new_sof():
    """If 0xA5 appears inside the frame body (Data[3] in this case), it
    must be consumed as data, NOT re-trigger a new SOF hunt. Locks the
    FRAME-state length discipline: once 7 bytes are needed, exactly 7
    bytes are read before returning to HUNT.
    """
    body_a5 = b"\xA5\x01\x00\x01\x00\x00\x00\xA5"
    parser, _ = _make_parser_with_port([body_a5])
    _run_reader(parser)
    assert parser.frames_received == 1
    assert parser.invalid_frames == 0


# ============================================================ reconnect
def test_reconnect_pacing_when_port_open_fails(monkeypatch):
    """When _open_port returns None, the loop sleeps
    UART_RECONNECT_INTERVAL_S before retrying (SRS-PY-06: 2 s).

    Drives the loop with parser._port = None and asserts time.sleep was
    called with the configured interval exactly once before we flip
    _running = False to exit.
    """
    from config import UART_RECONNECT_INTERVAL_S
    from uart_parser import time as parser_time

    sleep_calls: list[float] = []
    monkeypatch.setattr(parser_time, "sleep", lambda d: sleep_calls.append(d))

    parser = UARTParser(frame_callback=lambda _: None)
    parser._port = None  # type: ignore[assignment]  # noqa: SLF001
    parser._running = True  # type: ignore[assignment]  # noqa: SLF001

    # Patch _open_port to return None the first call, then flip _running
    # the second time so the loop exits after exactly one pacing sleep.
    call_count = {"n": 0}

    def fake_open() -> None:
        call_count["n"] += 1
        if call_count["n"] >= 2:
            parser._running = False  # noqa: SLF001
        return None

    monkeypatch.setattr(parser, "_open_port", fake_open)
    _run_reader(parser)

    assert UART_RECONNECT_INTERVAL_S in sleep_calls
    assert parser.reconnect_count == 0


# ============================================================ send_frame
def test_send_frame_rejects_wrong_length():
    """send_frame drops frames whose length is not 8 bytes (the FRAME_LENGTH
    contract) — the early-return guard at uart_parser.py:89-92."""
    parser = UARTParser(frame_callback=lambda _: None)
    assert parser.send_frame(b"\x00") is False
    assert parser.send_frame(b"\xA5" * 7) is False
    assert parser.frames_sent == 0


def test_send_frame_drops_when_port_closed():
    """send_frame returns False and bumps send_errors when no port is
    open — the downlink drop path that main.py logs as a warning."""
    parser = UARTParser(frame_callback=lambda _: None)
    parser._port = None  # type: ignore[assignment]  # noqa: SLF001
    assert parser.send_frame(b"\xA5" * 8) is False
    assert parser.send_errors == 1
    assert parser.frames_sent == 0


def test_send_frame_writes_on_open_port():
    """send_frame writes the full 8 bytes to the open port and increments
    frames_sent. Positive path."""
    parser = UARTParser(frame_callback=lambda _: None)
    fake = _FakeSerial(parser)
    parser._port = fake  # type: ignore[assignment]  # noqa: SLF001
    raw = b"\xA5\x00\x01\x02\x00\x01\x00\x00"
    assert parser.send_frame(raw) is True
    assert fake.written == [raw]
    assert parser.frames_sent == 1
    assert parser.send_errors == 0


# ============================================================ discovery
class _FakePortInfo:
    """Minimal stand-in for serial.tools.list_ports.ListPortInfo."""

    def __init__(
        self, device: str, vid: int | None, pid: int | None,
    ) -> None:
        self.device = device
        self.vid = vid
        self.pid = pid


def test_discover_port_matches_vid_pid(monkeypatch):
    """_discover_port returns the device when a VID/PID match exists
    (SRS-PY-01) — the preferred discovery path."""
    fake_ports = [
        _FakePortInfo("/dev/ttyACM0", vid=0x0483, pid=0x5740),
    ]
    monkeypatch.setattr(
        serial.tools.list_ports, "comports", lambda: fake_ports,
    )
    assert UARTParser._discover_port() == "/dev/ttyACM0"


def test_discover_port_single_acm_fallback(monkeypatch):
    """When no VID/PID matches, a single /dev/ttyACM* device is returned
    (Linux convenience heuristic). Returns None when ambiguous or absent."""
    # No VID/PID match (vid/pid None), but a single ACM device present.
    fake_ports = [_FakePortInfo("/dev/ttyACM0", vid=None, pid=None)]
    monkeypatch.setattr(
        serial.tools.list_ports, "comports", lambda: fake_ports,
    )
    assert UARTParser._discover_port() == "/dev/ttyACM0"


def test_discover_port_returns_none_when_ambiguous(monkeypatch):
    """Two ACM devices with no VID/PID match => None (the heuristic only
    fires for a single candidate to avoid guessing)."""
    fake_ports = [
        _FakePortInfo("/dev/ttyACM0", vid=None, pid=None),
        _FakePortInfo("/dev/ttyACM1", vid=None, pid=None),
    ]
    monkeypatch.setattr(
        serial.tools.list_ports, "comports", lambda: fake_ports,
    )
    assert UARTParser._discover_port() is None


def test_discover_port_returns_none_when_empty(monkeypatch):
    """No ports at all => None."""
    monkeypatch.setattr(serial.tools.list_ports, "comports", lambda: [])
    assert UARTParser._discover_port() is None
