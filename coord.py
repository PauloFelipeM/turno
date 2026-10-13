"""Coordenação entre as instâncias do autopilot (Codex e Claude) que trabalham nos mesmos projetos.

- Reserva (claims/<projeto>.json): o motor que abriu uma unidade no projeto. Dura até a unidade
  fechar, inclusive enquanto ela está interrompida. Reserva "any" é um bastão: qualquer motor pega.
- Trava de execução (<grupo>.lock): flock mantido enquanto a unidade roda. O sistema solta a trava
  se o processo morrer. Projetos do mesmo docker_group dividem a trava.
- Decisões (decisions.lock e notify.json): uma instância por vez fala com o ntfy.
"""

import contextlib
import fcntl
import json
import time
from pathlib import Path

import paths

SHARED = paths.home() / "shared"
ANY = "any"


def shared_dir(*parts):
    path = SHARED.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def try_lock(name):
    """flock exclusivo e não bloqueante em shared/<name>.lock. Devolve o arquivo aberto ou None."""
    f = open(shared_dir() / f"{name}.lock", "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        return None
    return f


def unlock(handle):
    if handle is not None:
        handle.close()  # fechar o arquivo solta o flock


def group_of(project):
    return project.get("docker_group") or project["name"]


@contextlib.contextmanager
def _claims_mutex():
    # Bloqueante e curto: só serializa a leitura e a escrita das reservas entre as instâncias.
    with open(shared_dir() / "claims.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def _claim_path(project):
    return shared_dir("claims") / f"{project}.json"


def read_claim(project):
    try:
        return json.loads(_claim_path(project).read_text())
    except (OSError, ValueError):
        return None


def _write_claim(project, data):
    path = _claim_path(project)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def claim(project, engine):
    """Reserva o projeto para `engine`, assumindo um bastão. False se o outro motor já o reservou."""
    with _claims_mutex():
        current = read_claim(project)
        if current and current.get("engine") not in (engine, ANY):
            return False
        if not current or current.get("engine") != engine:
            _write_claim(project, {"engine": engine, "since": time.time()})
        return True


def release(project, engine):
    """Remove a reserva, se ela for de `engine`."""
    with _claims_mutex():
        current = read_claim(project)
        if current and current.get("engine") == engine:
            _claim_path(project).unlink(missing_ok=True)


def hand_off(project, engine, unit_id=None):
    """Troca a reserva de `engine` por um bastão que qualquer motor pode pegar.

    O bastão leva o identificador da unidade, para o histórico juntar as execuções dos dois motores.
    """
    with _claims_mutex():
        current = read_claim(project)
        if current and current.get("engine") not in (engine, ANY):
            return
        _write_claim(project, {"engine": ANY, "handoff_from": engine, "unit_id": unit_id, "since": time.time()})


def is_handoff(project):
    current = read_claim(project)
    return bool(current) and current.get("engine") == ANY


def held_by_other(project, engine):
    current = read_claim(project)
    return bool(current) and current.get("engine") not in (engine, ANY)


def reconcile(engine, open_units):
    """Acerta as reservas de `engine` com o estado dele, ao iniciar.

    `open_units` é {projeto: há unidade interrompida no estado do motor}. Remove as reservas sem
    unidade aberta, cria as que faltam e devolve os projetos com unidade aberta aqui, mas
    reservados pelo outro motor.
    """
    conflicts = []
    with _claims_mutex():
        for project, is_open in open_units.items():
            current = read_claim(project)
            owner = current.get("engine") if current else None
            if owner == engine and not is_open:
                _claim_path(project).unlink(missing_ok=True)
            elif is_open and owner in (None, ANY):
                _write_claim(project, {"engine": engine, "since": time.time()})
            elif is_open and owner != engine:
                conflicts.append(project)
    return conflicts


def claims():
    """{projeto: reserva} de todas as reservas atuais."""
    return {p.stem: read_claim(p.stem) for p in sorted(shared_dir("claims").glob("*.json"))}


def load_notify(default):
    try:
        return json.loads((shared_dir() / "notify.json").read_text())
    except (OSError, ValueError):
        return dict(default)


def save_notify(data):
    path = shared_dir() / "notify.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


# --- mensagens do dono (texto livre pelo ntfy) ----------------------------------------------------


def _notes_path(project):
    return shared_dir("notes") / f"{project}.json"


def _read_notes(project):
    try:
        return json.loads(_notes_path(project).read_text())
    except (OSError, ValueError):
        return []


def _write_notes(project, notes):
    path = _notes_path(project)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(notes, ensure_ascii=False, indent=1))
    tmp.replace(path)


def add_note(project, text):
    """Guarda uma mensagem para a próxima unidade do projeto, de qualquer motor."""
    with _claims_mutex():
        notes = _read_notes(project)
        notes.append({"id": f"{time.time_ns()}", "at": time.time(), "text": text, "delivered": False})
        _write_notes(project, notes)


def pending_notes(project):
    return [n for n in _read_notes(project) if not n.get("delivered")]


def mark_delivered(project, ids):
    """Marca como entregues as mensagens que foram no prompt de uma unidade que fechou."""
    ids = set(ids)
    with _claims_mutex():
        notes = _read_notes(project)
        for n in notes:
            if n["id"] in ids:
                n["delivered"] = True
        _write_notes(project, notes)
