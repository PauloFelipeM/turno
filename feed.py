"""Fluxo ao vivo: lê o JSONL de uma execução aos poucos e traduz cada evento para itens comuns."""

import json
from pathlib import Path

import engines


class Tail:
    """Lê só as linhas completas novas de um arquivo que ainda está sendo escrito.

    Devolve pares (número do evento, evento); o número permite ao navegador descartar repetidos.
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
        self.rest = lines.pop()  # sem quebra no fim: espera completar
        return self._parse(lines)

    def flush(self):
        """A última linha sem quebra, para um arquivo que já terminou."""
        rest, self.rest = self.rest, b""
        return self._parse([rest])


def translate(engine_name, event, root=None):
    cls = engines.ENGINES.get(engine_name)
    if cls is None:
        return [{"kind": "unknown", "text": str(event.get("type"))}]
    items = cls.feed_items(event)
    if root:
        prefix = str(root).rstrip("/") + "/"
        for item in items:
            if item["kind"] == "file" and item["text"].startswith(prefix):
                item["text"] = item["text"][len(prefix):]
    return items


def items(engine_name, pairs, root=None):
    out = []
    for n, ev in pairs:
        for item in translate(engine_name, ev, root):
            item["n"] = n
            out.append(item)
    return out


def read_run(engine_name, path, root=None):
    """Todos os itens de uma execução; None se o arquivo já foi apagado."""
    if not Path(path).exists():
        return None
    tail = Tail(path)
    return items(engine_name, tail.read() + tail.flush(), root)
