"""Small cross-platform console helpers used by command-line tools.

Windows terminals can use a legacy code page which cannot encode the check and
cross characters used by the setup/preflight output.  Configure stdout and
stderr once at startup so the tools remain readable instead of crashing with a
UnicodeEncodeError.  ``errors='replace'`` is intentional: a warning should
never prevent a deployment diagnostic from being printed.
"""
from __future__ import annotations

import os
import sys
from typing import TextIO


def _configure(stream: TextIO) -> None:
    """Prefer UTF-8 output without assuming ``stream`` is a real terminal."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        # pytest/captured streams and a closed pipe may reject reconfiguration.
        pass


def configure() -> None:
    """Make CLI output safe on Windows and other non-UTF-8 environments."""
    if os.name == "nt":
        _configure(sys.stdout)
        _configure(sys.stderr)


__all__ = ["configure"]
