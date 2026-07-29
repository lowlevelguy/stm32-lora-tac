"""Unit tests for mqtt_client.MQTTGateway — the paho-mqtt wrapper.

Construction of a real ``MQTTGateway`` is safe (no socket is opened
until ``start()`` is called), so every test builds a fresh instance and
replaces ``gw._client`` with a MagicMock to bypass paho's network layer
entirely. Callbacks (``_on_connect``, ``_on_disconnect``, ``_on_message``)
are then driven directly with hand-rolled arguments.

The ``_reconnect_watchdog`` tests in particular pin the back-off
arithmetic that SRS-PY-06 calls out (5 s initial, exponential, cap at
60 s, reset on success). Running the watchdog on the calling thread —
no ``loop_start`` involved — keeps the timing deterministic.
"""

from unittest.mock import MagicMock

import paho.mqtt.client as mqtt
import pytest

from mqtt_client import MQTTGateway


def _make_gateway() -> MQTTGateway:
    """Build a MQTTGateway and replace the paho client with a MagicMock.

    Using MagicMock avoids any socket creation while preserving the
    attribute-call introspection paho-mqtt's API surface requires
    (``client.publish(topic, payload, qos=, retain=)`` in particular).
    """
    gw = MQTTGateway()
    gw._client = MagicMock()  # type: ignore[assignment]  # noqa: SLF001
    return gw


# ============================================================== publish
def test_publish_buffers_when_disconnected():
    """publish() while disconnected appends to ``_pending`` for later
    flush on connect — the buffer path that covers the broker outage
    window between disconnect and reconnect."""
    gw = _make_gateway()
    gw._connected = False  # noqa: SLF001
    gw.publish("enddev1/sensor", b"\x00\x1A")
    assert gw._pending == [("enddev1/sensor", b"\x00\x1A")]  # noqa: SLF001
    gw._client.publish.assert_not_called()  # noqa: SLF001
    assert gw.messages_published == 0


def test_publish_sends_when_connected():
    """publish() while connected calls paho publish() with the right
    kwargs (qos=0, retain=False per SRS Topic Map row 2)."""
    gw = _make_gateway()
    gw._connected = True  # noqa: SLF001
    gw._client.publish.return_value.rc = mqtt.MQTT_ERR_SUCCESS  # noqa: SLF001

    gw.publish("enddev1/sensor", b"\x00\x1A")

    gw._client.publish.assert_called_once_with(  # noqa: SLF001
        "enddev1/sensor", b"\x00\x1A", qos=0, retain=False,
    )
    assert gw.messages_published == 1
    assert gw.messages_dropped == 0


def test_publish_handles_str_payload():
    """publish() UTF-8-encodes str payloads so paho receives bytes — the
    coercive branch at mqtt_client.py:91-92."""
    gw = _make_gateway()
    gw._connected = True  # noqa: SLF001
    gw._client.publish.return_value.rc = mqtt.MQTT_ERR_SUCCESS  # noqa: SLF001

    gw.publish("enddev1/sensor", "hello")

    gw._client.publish.assert_called_once_with(  # noqa: SLF001
        "enddev1/sensor", b"hello", qos=0, retain=False,
    )


def test_publish_failure_increments_drops():
    """When paho's publish() returns a non-success rc, the message is
    counted as dropped rather than published — the path that surfaces
    broker-side failures in stats."""
    gw = _make_gateway()
    gw._connected = True  # noqa: SLF001
    gw._client.publish.return_value.rc = mqtt.MQTT_ERR_NO_CONN  # noqa: SLF001

    gw.publish("enddev1/sensor", b"\x00\x1A")

    assert gw.messages_dropped == 1
    assert gw.messages_published == 0


# ============================================================= _on_connect
def test_on_connect_flushes_pending_queue():
    """_on_connect(reason_code=0) flushes the disconnected-period buffer
    to paho, then empties _pending. Locks the queued-uplink contract at
    mqtt_client.py:127-135."""
    gw = _make_gateway()
    gw._pending = [  # noqa: SLF001
        ("enddev1/sensor", b"\x00\x1A"),
        ("enddev1/rssi", b"\x00\x04"),
    ]
    gw._client.publish.return_value.rc = mqtt.MQTT_ERR_SUCCESS  # noqa: SLF001

    gw._on_connect(gw._client, None, {}, 0, None)  # noqa: SLF001

    assert gw._connected is True  # noqa: SLF001
    assert gw._pending == []  # noqa: SLF001
    assert gw._client.publish.call_count == 2  # noqa: SLF001
    assert gw.messages_published == 2


def test_on_connect_re_arms_subscriptions():
    """_on_connect(reason_code=0) re-subscribes every registered topic,
    covering the case where a subscription was added while disconnected
    and therefore deferred. Locks mqtt_client.py:129-131."""
    gw = _make_gateway()
    cb1, cb2 = MagicMock(), MagicMock()
    # Subscribe while disconnected — must NOT call _client.subscribe.
    gw.subscribe("enddev1/actuator", cb1)
    gw.subscribe("foo/bar", cb2)
    gw._client.subscribe.assert_not_called()  # noqa: SLF001

    # Now connect — all subs should be re-armed.
    gw._on_connect(gw._client, None, {}, 0, None)  # noqa: SLF001

    subscribed_topics = {
        call.args[0] for call in gw._client.subscribe.call_args_list  # noqa: SLF001
    }
    assert subscribed_topics == {"enddev1/actuator", "foo/bar"}


def test_on_connect_nonzero_rc_does_not_arm():
    """A refused connect (non-zero reason_code) leaves _connected False
    and does NOT re-arm subscriptions — the watchdog owns retry."""
    gw = _make_gateway()
    gw.subscribe("enddev1/actuator", MagicMock())

    gw._on_connect(gw._client, None, {}, 5, None)  # noqa: SLF001

    assert gw._connected is False  # noqa: SLF001
    gw._client.subscribe.assert_not_called()  # noqa: SLF001


# ============================================================ _on_disconnect
def test_on_disconnect_clears_connected_flag():
    """_on_disconnect sets _connected False so publish() routes into the
    buffer queue rather than calling paho on a dead socket."""
    gw = _make_gateway()
    gw._connected = True  # noqa: SLF001

    gw._on_disconnect(gw._client, None, {}, 0, None)  # noqa: SLF001

    assert gw._connected is False  # noqa: SLF001


# ============================================================== _on_message
def test_on_message_dispatches_to_matching_callback():
    """_on_message dispatches an inbound MQTT message to the callback
    whose subscription pattern matches the message's topic.

    Uses paho's REAL topic_matches_sub so the matcher itself is in
    scope: '+/actuator' must match 'enddev1/actuator'. Locks the
    production dispatch contract at mqtt_client.py:145-157.
    """
    gw = _make_gateway()
    cb = MagicMock()
    gw.subscribe("+/actuator", cb)
    gw._client.reset_mock()  # noqa: SLF001

    msg = MagicMock()
    msg.topic = "enddev1/actuator"
    msg.payload = b"\x00\x01"

    gw._on_message(gw._client, None, msg)  # noqa: SLF001

    cb.assert_called_once_with("enddev1/actuator", b"\x00\x01")


def test_on_message_swallows_callback_exception():
    """A callback raising must NOT propagate out of _on_message — keeps
    paho's loop alive even if a downlink handler has a bug. Locks the
    try/except at mqtt_client.py:154-157."""
    gw = _make_gateway()
    cb = MagicMock(side_effect=RuntimeError("boom"))
    gw.subscribe("+/actuator", cb)

    msg = MagicMock()
    msg.topic = "enddev1/actuator"
    msg.payload = b"\x00\x01"

    # Must not raise.
    gw._on_message(gw._client, None, msg)  # noqa: SLF001


def test_on_message_no_match_does_not_dispatch():
    """A message that matches no subscription pattern invokes no
    callback at all."""
    gw = _make_gateway()
    cb = MagicMock()
    gw.subscribe("enddev1/sensor", cb)

    msg = MagicMock()
    msg.topic = "enddev2/sensor"  # No match against 'enddev1/sensor'.
    msg.payload = b"\x00\x01"

    gw._on_message(gw._client, None, msg)  # noqa: SLF001
    cb.assert_not_called()


# ============================================================== subscribe
def test_subscribe_calls_client_subscribe_when_connected():
    """subscribe() while connected immediately calls paho subscribe —
    the live-registration path at mqtt_client.py:116-117."""
    gw = _make_gateway()
    gw._connected = True  # noqa: SLF001

    gw.subscribe("enddev1/actuator", MagicMock())

    gw._client.subscribe.assert_called_once_with(  # noqa: SLF001
        "enddev1/actuator", qos=0,
    )


# ==================================================== _reconnect_watchdog
class _ScriptedStopEvent:
    """Stand-in for threading.Event whose ``wait`` returns scripted values.

    Each call returns False (signalling "keep looping") for the first
    N-1 calls and True ("stop") on the Nth, mirroring how a real
    Event.set() would terminate the watchdog. Crucially, ``wait``
    records the delay it was asked to sleep for so the back-off
    arithmetic can be asserted exactly.
    """

    def __init__(self, iterations: int) -> None:
        # iterations = how many times the loop body runs before stopping.
        self._iterations = iterations
        self._calls = 0
        self.delays: list[float] = []

    def wait(self, delay: float) -> bool:
        self.delays.append(delay)
        self._calls += 1
        if self._calls >= self._iterations:
            return True  # stop the watchdog
        return False

    def is_set(self) -> bool:
        return self._calls >= self._iterations


def test_reconnect_watchdog_doubles_delay_on_failure(monkeypatch):
    """On each failed reconnect the watchdog doubles ``delay`` (capped at
    MAX). After 3 iterations with persistent failure the delays recorded
    are [5, 10, 20] — the SRS-PY-06 exponential schedule.

    Mocks ``time.sleep`` to a no-op so the loop runs in zero wall-time.
    """
    from mqtt_client import time as mqtt_time
    monkeypatch.setattr(mqtt_time, "sleep", lambda d: None)

    gw = _make_gateway()
    gw._client.reconnect.side_effect = OSError("broker down")  # noqa: SLF001
    gw._stop_event = _ScriptedStopEvent(iterations=4)  # noqa: SLF001

    gw._reconnect_watchdog()  # noqa: SLF001

    # 4 wait() calls because the loop structure is:
    #   wait(delay) -> if stop: break
    #   check connected -> reconnect() -> update delay
    # Iteration 1 uses default delay=5, then 10, then 20, then 40 capped...;
    # but on iteration 4 wait() returns True, so we recorded 4 delays.
    # Expected: [5, 10, 20, 40] but only first 3 reconnect attempts run.
    assert gw._stop_event.delays == [5, 10, 20, 40]


def test_reconnect_watchdog_caps_at_max(monkeypatch):
    """The exponential delay is capped at MQTT_RECONNECT_MAX_S (60 s).

    With 6 iterations of persistent failure the recorded delays are
    [5, 10, 20, 40, 60, 60] — the naive doubling would produce 80 at
    step 5, the cap pulls it back to 60 and pins it there.
    """
    from config import MQTT_RECONNECT_MAX_S
    from mqtt_client import time as mqtt_time
    monkeypatch.setattr(mqtt_time, "sleep", lambda d: None)

    gw = _make_gateway()
    gw._client.reconnect.side_effect = OSError("broker down")  # noqa: SLF001
    gw._stop_event = _ScriptedStopEvent(iterations=6)  # noqa: SLF001

    gw._reconnect_watchdog()  # noqa: SLF001

    assert gw._stop_event.delays == [5, 10, 20, 40, 60, 60]
    assert MQTT_RECONNECT_MAX_S == 60


def test_reconnect_watchdog_resets_on_success(monkeypatch):
    """A successful reconnect resets ``delay`` back to MIN_S (5). The
    back-off is per-outage, not per-process: once reconnected, the next
    outage starts from 5 s again. Locks mqtt_client.py:197.

    Strategy: reconnect() fails twice then succeeds; the 4th wait is
    the one that stops the loop.
    """
    from config import MQTT_RECONNECT_MIN_S
    from mqtt_client import time as mqtt_time
    monkeypatch.setattr(mqtt_time, "sleep", lambda d: None)

    gw = _make_gateway()
    # First two reconnect attempts fail, the third succeeds.
    gw._client.reconnect.side_effect = [  # noqa: SLF001
        OSError("first fail"),
        OSError("second fail"),
        None,  # success
        OSError("third fail"),
        OSError("fourth fail"),
    ]
    gw._stop_event = _ScriptedStopEvent(iterations=6)  # noqa: SLF001

    gw._reconnect_watchdog()  # noqa: SLF001

    # Iteration 1: delay=5, fail -> delay=10
    # Iteration 2: delay=10, fail -> delay=20
    # Iteration 3: delay=20, success -> delay=5
    # Iteration 4: delay=5, fake _connected is False (we didn't set it),
    #              success path again requires _connected to be False for
    #              reconnect() to even fire. We expect the next reconnect
    #              raise -> delay=10.
    # Iteration 5: delay=10, fail -> delay=20
    # Iteration 6: wait True -> stop.
    assert gw._stop_event.delays == [5, 10, 20, 5, 10, 20]
    assert MQTT_RECONNECT_MIN_S == 5


# ============================================================ _do_connect
def test_do_connect_swallows_oserror():
    """_do_connect catches OSError (e.g. DNS failure, connection refused)
    and returns silently so the watchdog can retry — the try/except at
    mqtt_client.py:163-172. Without this catch the initial connect
    failure would crash the bridge."""
    gw = _make_gateway()
    gw._client.connect.side_effect = OSError("refused")  # noqa: SLF001

    # Must not raise.
    gw._do_connect()  # noqa: SLF001
