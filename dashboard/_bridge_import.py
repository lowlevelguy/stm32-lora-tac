"""Bridge import shim — runs once at launch to expose ``lora_frame``.

The handoff document sanctions importing ``lora_frame`` from the
sibling ``uart_mqtt_bridge`` package (for downlink command validation
only — see ``control_page.py``). The bridge has no ``__init__.py``
(and the handoff forbids modifying bridge code), so we add the bridge
dir to ``sys.path`` *transiently*: push, import, pop. This prevents
the bridge's own ``config.py`` from shadowing the dashboard's
``config.py`` (which would break ``from config import ...`` everywhere
in this package, since both siblings define ``config.py``).

``lora_frame.py`` is stdlib-only (dataclasses, enum, struct) so the
transient path exposure has no hidden transitive imports. Verified
2026-07-30: ``'config' not in sys.modules`` after this block runs,
and ``uart_mqtt_bridge`` is NOT left on ``sys.path``.

Launch path
-----------
``main.py`` imports this module FIRST, before any other dashboard
module. Once ``lora_frame`` is in ``sys.modules`` (because Python
caches imports), every subsequent ``import lora_frame`` or
``from lora_frame import ...`` in this package resolves from the cache
without needing the bridge dir on the path again.
"""

import os
import sys


_BRIDGE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, "uart_mqtt_bridge"),
)


def install() -> None:
    """Push the bridge dir, import ``lora_frame`` to prime the module
    cache, then pop the bridge dir so dashboard's ``config.py`` wins.
    Safe to call multiple times — caches via ``sys.modules``.
    """
    if "lora_frame" in sys.modules:
        return  # already primed — nothing to do

    if _BRIDGE_DIR not in sys.path:
        sys.path.insert(0, _BRIDGE_DIR)
    try:
        import lora_frame  # type: ignore[import]  # noqa: F401
    finally:
        try:
            sys.path.remove(_BRIDGE_DIR)
        except ValueError:
            pass
