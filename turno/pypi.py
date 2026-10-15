"""Whether PyPI has a newer turno: at most one GET a day, remembered in `shared/update-check.json`.

Only reads `https://pypi.org/pypi/turno/json`; never installs anything. Off with
`"update_check": false` in `config.json`. A network failure is silent: the panel just shows no notice.
"""

import json
import os
import re
import time
import urllib.request
from pathlib import Path

from . import __version__
from . import config

URL = "https://pypi.org/pypi/turno/json"
CACHE = "update-check.json"
DAY = 24 * 3600
TIMEOUT = 10


def parse_version(text):
    """`"1.2.3"` -> (1, 2, 3); None for anything that is not plain dotted numbers (pre-releases included)."""
    if not isinstance(text, str) or not re.fullmatch(r"\d+(\.\d+)*", text.strip()):
        return None
    return tuple(int(x) for x in text.strip().split("."))


def newer(latest, current=__version__):
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def enabled(home):
    """`update_check` in the config; a missing key is on, an unreadable config is off (no network then)."""
    try:
        return config.load(home).get("update_check", True) is not False
    except (config.ConfigError, OSError):
        return False


def _cache_path(home):
    return Path(home) / "shared" / CACHE


def read_cache(home):
    try:
        data = json.loads(_cache_path(home).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(home, data):
    path = _cache_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{CACHE}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": f"turno/{__version__}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def latest_from(body):
    """The version in a PyPI JSON answer; None when the answer does not have one."""
    data = json.loads(body)
    version = data.get("info", {}).get("version") if isinstance(data, dict) else None
    return version if parse_version(version) else None


def check(home, now=None):
    """Asks PyPI when the last check is a day old or more. Returns the cache: {checked_at, latest}."""
    if not enabled(home):
        return {}
    now = time.time() if now is None else now
    cache = read_cache(home)
    checked = cache.get("checked_at")
    if isinstance(checked, (int, float)) and 0 <= now - checked < DAY:
        return cache
    try:
        latest = latest_from(_fetch(URL))
    except Exception:  # network, HTTP, JSON: silent; the next try is a day later
        latest = cache.get("latest")
    cache = {"checked_at": now, "latest": latest}
    try:
        _write_cache(home, cache)
    except OSError:
        pass
    return cache


def notice(home):
    """What the panel shows: the running version and, when PyPI has a newer one, that version."""
    latest = read_cache(home).get("latest") if enabled(home) else None
    is_newer = newer(latest)
    return {"current": __version__, "latest": latest if is_newer else None, "newer": is_newer}


def watch(home, stopped, interval=3600):
    """The panel's background thread: `check` every hour (it asks PyPI at most once a day)."""
    while not stopped.is_set():
        try:
            check(home)
        except Exception:
            pass
        stopped.wait(interval)
