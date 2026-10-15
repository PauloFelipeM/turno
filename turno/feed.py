"""Live feed: reads a run's JSONL incrementally and translates each event into common items."""

import json
from pathlib import Path

from . import engines


class Tail:
    """Reads only the new complete lines of a file that is still being written.

    Returns (event number, event) pairs; the number lets the browser drop duplicates.
    """

    def __init__(self, path):
        self.path, self.pos, self.rest, self.count = Path(path), 0, b"", 0

    def _parse(self, lines):
        out = []
        for line in lines:
            if not line.strip():
                continue
            n, self.count = self.count, self.count + 1
            try:
                ev = json.loads(line.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if isinstance(ev, dict):
                out.append((n, ev))
        return out

    def read(self):
        try:
            with self.path.open("rb") as f:
                f.seek(self.pos)
                chunk = f.read()
        except OSError:
            return []
        self.pos += len(chunk)
        lines = (self.rest + chunk).split(b"\n")
        self.rest = lines.pop()  # no newline at the end: wait for it to complete
        return self._parse(lines)

    def flush(self):
        """The last line without a newline, for a file that has finished."""
        rest, self.rest = self.rest, b""
        return self._parse([rest])


def translate(engine_name, event, root=None):
    """Translates an engine event into feed items; an odd event becomes `unknown`."""
    cls = engines.ENGINES.get(engine_name)
    if cls is None:
        return [{"kind": "unknown", "text": str(event.get("type"))}]
    try:
        items = cls.feed_items(event)
    except Exception:  # a new CLI format does not bring the feed down
        return [{"kind": "unknown", "text": str(event.get("type"))}]
    if root:
        prefix = str(root).rstrip("/") + "/"
        for item in items:
            text = item.get("text")
            if item["kind"] == "file" and isinstance(text, str) and text.startswith(prefix):
                item["text"] = text[len(prefix):]
    return items


def items(engine_name, pairs, root=None):
    out = []
    for n, ev in pairs:
        for item in translate(engine_name, ev, root):
            item["n"] = n
            out.append(item)
    return out


def read_run(engine_name, path, root=None):
    """Every item of a run; None if the file was already deleted."""
    if not Path(path).exists():
        return None
    tail = Tail(path)
    return items(engine_name, tail.read() + tail.flush(), root)
