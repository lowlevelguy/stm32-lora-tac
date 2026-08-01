# Hallmark - dashboard - genre: atmospheric - macrostructure: Workbench
# Theme: dark paper (oklch 16% 0.01 260) - accent-cyan oklch(72% 0.08 200)
#   - accent-amber oklch(65% 0.13 75)
# Font: FiraCode Nerd Font (user choice) - motion: data-opacity-only
"""Dashboard entry point — QApplication + style sheet + main window.

Run as a script from inside the ``dashboard`` dir (same convention the
bridge uses for ``uart_mqtt_bridge/main.py``):

    cd dashboard
    python3 main.py

The bridge-import shim runs FIRST (before any other dashboard module
gets imported) so ``lora_frame`` is primed in ``sys.modules`` for the
modules that need it (``control_page``), while keeping the bare
``from config import ...`` flat-import style authoritative for the
dashboard's own ``config.py``.
"""
import logging
import os
import sys

# ------------------------------------------------------------ path setup
# `main.py` may be launched as ``python3 main.py`` (cwd=dashboard,
# script-dir auto-prepended to sys.path) or ``python3 -m main`` from
# the dashboard dir. Either way, ensure the dashboard's own directory
# is on sys.path[0] so flat imports (``from config import...``) win.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ----------------------------------------------------- bridge shim first
from _bridge_import import install as _install_bridge  # noqa: E402

_install_bridge()  # primes lora_frame in sys.modules, pops bridge dir

# ---------------------------------------------------------- dashboard UI
from PyQt6.QtGui import QFont  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from config import (  # noqa: E402
    FONT_FALLBACK_CHAIN,
    PREFERRED_FONT,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
)
from dashboard_window import DashboardWindow  # noqa: E402
from styles import QssBuilder  # noqa: E402

logger = logging.getLogger("dashboard")


def _resolve_font() -> QFont:
    """Construct the QFont and verify the preferred face is installed.

    Per user decision 2026-07-30 the preferred font is FiraCode Nerd
    Font (installed in ``~/.fonts/``); the QSS tokens.qss already
    carries a multi-face fallback chain so the rendering engine walks
    Qt's substitution list at paint time. We additionally emit a
    runtime warning here when ``exactMatch()`` is False so a missing
    primary face is visible in logs (rather than silently substituted).
    """
    font = QFont(PREFERRED_FONT)
    if not font.exactMatch():
        logger.warning(
            "preferred font '%s' not found on this host; Qt will "
            "substitute from the fallback chain %r. Install %s "
            "(or update FONT_FALLBACK_CHAIN in config.py) for the "
            "intended visual fidelity.",
            PREFERRED_FONT,
            list(FONT_FALLBACK_CHAIN[1:]),
            PREFERRED_FONT,
        )
    return font


def main() -> int:
    """Application entry.

    1. Construct QApplication (single instance, single event loop).
    2. Resolve and attach the preferred font to the QApplication.
    3. Load + resolve tokens.qss via QssBuilder, apply app-wide.
    4. Construct DashboardWindow, show, exec the event loop.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    app = QApplication(sys.argv)
    app.setApplicationName("LoRa Dashboard")

    # Font: applied app-wide so QSS font-family declarations inherit
    # the resolved QFont instance as the default face.
    app.setFont(_resolve_font())

    # Style sheet: tokens.qss pre-processed by QssBuilder (the Python
    # substitution path; Qt's native @variable is fragile, use route
    # documented in handoff § Token system technical note).
    qss_path = os.path.join(_HERE, "tokens.qss")
    app.setStyleSheet(QssBuilder.from_file(qss_path))

    window = DashboardWindow()
    window.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
