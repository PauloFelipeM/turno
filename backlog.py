"""Andamento do backlog que o orquestrador grava ao fechar cada unidade (backlog.json)."""

import json
from pathlib import Path

FILE = Path(".superpowers") / "autopilot" / "backlog.json"
STATES = ("todo", "spec", "plan", "doing", "done")


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_tasks(tasks):
    return (isinstance(tasks, dict) and _count(tasks.get("done")) and _count(tasks.get("total"))
            and tasks["total"] >= 1 and 0 <= tasks["done"] <= tasks["total"])


def valid_backlog(data):
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return False
    for item in items:
        if not (isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()
                and isinstance(item.get("title"), str) and item["title"].strip()
                and item.get("state") in STATES):
            return False
        if "tasks" in item and not _valid_tasks(item["tasks"]):
            return False
    return True


def read_backlog(project_path, cache=None):
    """{"items": [...]} ou {"error": "missing" | "invalid"}.

    O agente pode estar no meio da escrita: com `cache`, um arquivo inválido devolve a última leitura
    válida, marcada como `stale`.
    """
    path = Path(project_path) / FILE
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return {"error": "missing"}
    except (OSError, ValueError):
        data = None
    if valid_backlog(data):
        if cache is not None:
            cache[str(path)] = data
        return {"items": data["items"]}
    if cache is not None and str(path) in cache:
        return {"items": cache[str(path)]["items"], "stale": True}
    return {"error": "invalid"}


def current(items):
    """O item em andamento: o primeiro em `doing`; senão o primeiro que não terminou; senão None."""
    return (next((i for i in items if i["state"] == "doing"), None)
            or next((i for i in items if i["state"] != "done"), None))
