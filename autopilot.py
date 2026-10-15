#!/usr/bin/env python3
"""Compatibility shim: `python3 <checkout>/autopilot.py ...` runs the `turno` command.

Loops started this way before the code moved into the `turno/` package, and any alias that still
calls this file, keep working.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from turno.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
