"""Runtime constants and opt-in diagnostic overrides.

Keep this module free of Krita and PyQt imports so it can be used by the
geometry modules and by the test suite.
"""

import os


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


# Live redraws rebuild every vector shape, so keep the curve resolution only as
# high as the screen can show. 128 segments stay sub-pixel at normal zoom and
# cut the SVG/vector work roughly in half versus 256 and by 3x versus 512.
# Env overrides are diagnostic knobs for A/B tests, not normal settings.
LIVE_SAMPLES = _env_int("LOOMIS_LIVE_SAMPLES", 128, 16, 1024)
RECONCILE_INTERVAL = 1000
SYNC_INTERVAL = 100
REDRAW_DEBOUNCE = _env_int("LOOMIS_REDRAW_MS", 40, 0, 1000)
LOCAL_SAVE_DEBOUNCE = 150
