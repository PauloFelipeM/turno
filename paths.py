"""turno's data folder: config, state, logs and runs, outside the code folder."""

import os
from pathlib import Path


def home():
    """$TURNO_HOME, ou ~/.turno."""
    return Path(os.environ.get("TURNO_HOME") or Path.home() / ".turno").expanduser()
