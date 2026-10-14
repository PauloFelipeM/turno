"""turno's data folder: config, state, logs and runs, outside the code folder."""

import os
import tomllib
from pathlib import Path


def home():
    """$TURNO_HOME, ou ~/.turno."""
    return Path(os.environ.get("TURNO_HOME") or Path.home() / ".turno").expanduser()


def has_ntfy_topic(home_dir=None):
    """Whether `secrets.toml` has a non-empty `[notify].topic`.

    Only a yes or no: the topic is a secret, so its value is never returned, logged or kept. A
    missing, unreadable or malformed file counts as no topic.
    """
    try:
        with open(Path(home_dir or home()) / "secrets.toml", "rb") as f:
            notify = tomllib.load(f).get("notify")
    except (OSError, ValueError):  # tomllib.TOMLDecodeError and UnicodeDecodeError are ValueErrors
        return False
    topic = notify.get("topic") if isinstance(notify, dict) else None
    return isinstance(topic, str) and bool(topic.strip())
