"""Unit history: one line per finished run, in ~/.turno/history-<engine>.jsonl."""

import datetime
import json
from pathlib import Path

OUTCOMES = ("worked", "waiting", "idle", "blocked", "usage_limit", "error", "cut", "unfinished", "stopped")


def iso(ts):
    """Timestamp -> ISO 8601 in the local time zone, with the offset."""
    return datetime.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def ts(value):
    """ISO 8601 -> timestamp; 0 if empty or invalid."""
    try:
        return datetime.datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return 0.0


def append(path, entry):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab+") as f:
        # A crash mid-write leaves the last line without a newline; without this the next one would stick to it.
        f.seek(0, 2)
        if f.tell():
            f.seek(-1, 2)
            if f.read(1) != b"\n":
                f.write(b"\n")
        f.write((json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))


def read(path):
    """The valid history lines; a broken line or one without unit_id is skipped."""
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and entry.get("unit_id"):
                    out.append(entry)
    except OSError:
        pass
    return out


def group(entries):
    """Groups runs by unit; the newest (by its first run's start) comes first."""
    units = {}
    for e in sorted(entries, key=lambda e: ts(e.get("started_at"))):
        u = units.setdefault(e["unit_id"], {"unit_id": e["unit_id"], "project": e.get("project"), "runs": []})
        u["runs"].append(e)
    out = []
    for u in units.values():
        first, last = u["runs"][0], u["runs"][-1]
        closing = next((r for r in u["runs"] if r.get("closed")), None) or {}
        u.update(started_at=first.get("started_at"), ended_at=last.get("ended_at"),
                 engines=sorted({r["engine"] for r in u["runs"] if r.get("engine")}),
                 outcome=last.get("outcome"), closed=bool(closing),
                 unit=closing.get("unit"), summary=closing.get("summary"),
                 milestone=closing.get("milestone"), cost=closing.get("cost"),
                 duration=sum(max(0.0, ts(r.get("ended_at")) - ts(r.get("started_at"))) for r in u["runs"]))
        out.append(u)
    out.sort(key=lambda u: ts(u["started_at"]), reverse=True)
    return out
