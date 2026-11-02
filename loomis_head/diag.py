"""Small diagnostics helper for non-fatal persistence failures.

This module deliberately imports only the standard library so it can be used
from the Krita-free modules and tested with plain Python.
"""

import sys

def warn(msg: str) -> None:
    """Write a diagnostic line to stderr without interrupting the plugin."""
    print("[loomis] %s" % msg, file=sys.stderr)
