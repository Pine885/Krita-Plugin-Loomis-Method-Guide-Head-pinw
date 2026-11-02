"""Opt-in timing for Krita-side redraw calls.

Krita's Python API gives no profiler. Enable this to see which part of a live
redraw actually costs time:

    LOOMIS_TIMING=1 krita

Append to a file instead of stderr:

    LOOMIS_TIMING=1 LOOMIS_TIMING_LOG=/tmp/loomis-timing.log krita

Timing is disabled by default and adds only timestamp calls when off.
"""

import os
import sys
import time

ENV_ENABLE = "LOOMIS_TIMING"
ENV_LOG = "LOOMIS_TIMING_LOG"

def enabled() -> bool:
    value = os.environ.get(ENV_ENABLE, "").strip().lower()
    return value in {"1", "true", "yes", "on"}

def now() -> float:
    return time.perf_counter()

def milliseconds(seconds: float) -> float:
    return seconds * 1000.0

def emit(message: str) -> None:
    if not enabled():
        return
    line = "[loomis-timing] %s\n" % message
    path = os.environ.get(ENV_LOG, "").strip()
    if path:
        try:
            with open(os.path.expanduser(path), "a", encoding="utf-8") as handle:
                handle.write(line)
            return
        except OSError:
            pass
    sys.stderr.write(line)
    sys.stderr.flush()

def format_redraw(data: dict) -> str:
    """Format the dict returned by HeadInstance.redraw()."""
    remove_ms = "[%s]" % ",".join(
        "%.2f" % milliseconds(value) for value in data.get("remove_each", ())
    )
    return (
        "redraw total=%.2fms build_svg=%.2f shapes_call=%.2f "
        "remove=%.2f add=%.2f states=%.2f bounds=%.2f "
        "shape_count=%d samples=%d remove_ms=%s"
        % (
            milliseconds(data.get("total", 0.0)),
            milliseconds(data.get("build_svg", 0.0)),
            milliseconds(data.get("shapes_call", 0.0)),
            milliseconds(data.get("remove", 0.0)),
            milliseconds(data.get("add", 0.0)),
            milliseconds(data.get("states", 0.0)),
            milliseconds(data.get("bounds", 0.0)),
            data.get("shape_count", 0),
            data.get("samples", 0),
            remove_ms,
        )
    )
