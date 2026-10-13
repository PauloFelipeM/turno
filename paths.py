"""Pasta de dados do Turno: configuração, estado, logs e execuções, fora da pasta do código."""

import os
from pathlib import Path


def home():
    """$TURNO_HOME, ou ~/.turno."""
    return Path(os.environ.get("TURNO_HOME") or Path.home() / ".turno").expanduser()
