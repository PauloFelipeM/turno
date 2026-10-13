#!/usr/bin/env python3
"""Agendador do autopilot do Codex para vários projetos que dividem a mesma cota.

Roda uma "unidade" de trabalho por vez (um task, uma spec, um plano, a finalização de um item),
sempre numa sessão curta do `codex exec`. Entre uma unidade e outra:

- termina primeiro a unidade que ficou interrompida (limite de uso, erro, fim de resposta precoce);
- escolhe o projeto de maior prioridade cujo uso semanal ainda cabe no seu `weekly_cap`;
- só começa se as janelas de 5 h e semanal tiverem folga para o custo médio aprendido da unidade;
- dorme até o reset quando não há folga.

Decisões de produto que importam viram uma pergunta no celular (ntfy), com um botão por opção.
O projeto espera a resposta até o prazo; sem resposta, vale a opção padrão. Enquanto isso, os
outros projetos seguem.

Uso:
  autopilot.py start                      inicia os dois loops (Codex e Claude) no tmux "autopilot"
  autopilot.py stop                       para os dois loops (como um Ctrl+C em cada um)
  autopilot.py run                        um loop só, o do Codex (Ctrl+C para; rodar de novo retoma)
  autopilot.py status                     estado de cada projeto e o uso atual das janelas
  autopilot.py check                      o que o loop faria agora (lê a cota; não roda nada)
  autopilot.py reset NOME                 volta um projeto idle ou blocked para ativo
  autopilot.py decisions                  decisões pendentes
  autopilot.py answer NOME ID OPÇÃO       responde uma decisão pelo terminal
  autopilot.py test-notify                manda uma notificação de teste
  autopilot.py serve [--port 8765]        o painel em http://localhost:8765 (só lê; seguro)
  autopilot.py --config claude.toml run   a instância do Claude (ver README)
"""

import argparse
import datetime
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

import backlog
import coord
import engines
import history
import paths
import schedule

BASE = Path(__file__).resolve().parent  # código: prompts, papéis, interface
HOME = paths.home()  # dados: configuração, estado, logs, execuções
CONFIG = HOME / "projects.toml"
# O tópico do ntfy é segredo: fica aqui, fora do git, e vale para as duas instâncias.
SECRETS = HOME / "secrets.toml"
STATE = HOME / "state.json"
LOG = HOME / "autopilot.log"
RUNS = HOME / "runs"
HISTORY = HOME / "history-codex.jsonl"
PROMPTS = BASE / "prompts"
UNIT_FILE = Path(".superpowers") / "autopilot" / "last-unit.json"
DECISIONS = Path(".superpowers") / "autopilot" / "decisions"
KEEP_RUNS = 80
EMA_ALPHA = 0.3


def log(msg):
    line = f"{time.strftime('%F %T')} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


engines.log = log
from engines import window  # noqa: E402  (usada na escolha do projeto e no status)


def resolve_config(config_path):
    """Caminho relativo vale a partir da pasta de dados; sem caminho, projects.toml."""
    if not config_path:
        return HOME / "projects.toml"
    path = Path(config_path).expanduser()
    return path if path.is_absolute() else HOME / path


def configure(config_path=None):
    """Aponta CONFIG, STATE, LOG e RUNS para a instância do arquivo de configuração.

    Sem [instance] name valem os nomes de sempre; com name = "claude", state-claude.json,
    autopilot-claude.log e runs-claude/. Tudo dentro da pasta de dados.
    """
    global CONFIG, STATE, LOG, RUNS, HISTORY
    HOME.mkdir(parents=True, exist_ok=True)
    CONFIG = resolve_config(config_path)
    cfg = load_config()
    name = (cfg.get("instance") or {}).get("name")
    suffix = f"-{name}" if name else ""
    STATE = HOME / f"state{suffix}.json"
    LOG = HOME / f"autopilot{suffix}.log"
    RUNS = HOME / f"runs{suffix}"
    HISTORY = HOME / f"history-{(cfg.get('engine') or {}).get('kind', 'codex')}.jsonl"


def load_config():
    with CONFIG.open("rb") as f:
        cfg = tomllib.load(f)
    if SECRETS.exists():
        with SECRETS.open("rb") as f:
            cfg.setdefault("notify", {}).update(tomllib.load(f).get("notify") or {})
    return cfg


def load_state(cfg):
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    d = cfg["defaults"]
    # `since` do ntfy começa agora: respostas anteriores ao primeiro run não valem.
    state.setdefault("_notify", {"since": str(int(time.time()))})
    state.setdefault("_window", {"active": False})
    # Respostas digitadas no tópico principal: também só valem as que chegarem daqui em diante.
    state["_notify"].setdefault("since_typed", str(int(time.time())))
    for p in cfg["project"]:
        s = state.setdefault(p["name"], {})
        s.setdefault("status", "active")  # active | idle | blocked | waiting
        s.setdefault("session", None)
        s.setdefault("interrupted", False)
        s.setdefault("errors", 0)
        s.setdefault("early_ends", 0)
        s.setdefault("blocked_until", 0)
        s.setdefault("cost", {"primary": d["initial_cost_primary"], "secondary": d["initial_cost_secondary"]})
        s.setdefault("dirty_logged", False)
        s.setdefault("unit_start_limits", None)
        s.setdefault("notes_sent", [])
        s.setdefault("unit_id", None)
        s.setdefault("run", None)
    return state


def save_state(state):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE)


# --- notificações e decisões (ntfy) -----------------------------------------------------------


def ntfy_server(cfg):
    return (cfg.get("notify") or {}).get("server", "https://ntfy.sh").rstrip("/")


def ntfy(cfg, title, message, priority=3, tags=(), actions=None):
    """Publica no tópico do ntfy. Falha de rede é registrada e nunca derruba o agendador."""
    topic = (cfg.get("notify") or {}).get("topic")
    if not topic:
        return
    body = {"topic": topic, "title": title, "message": message, "priority": priority, "tags": list(tags)}
    if actions:
        body["actions"] = actions
    req = urllib.request.Request(ntfy_server(cfg), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=15).close()
    except OSError as e:
        log(f"ntfy falhou: {e}")


def reply_topic(cfg):
    return cfg["notify"]["topic"] + "-reply"


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


def decision_files(project):
    folder = Path(project["path"]) / DECISIONS
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


def valid_decision(d, path):
    options = d.get("options")
    return (isinstance(options, list) and 1 <= len(options) <= 3
            and all(isinstance(o, dict) and re.fullmatch(r"[\w-]+", str(o.get("id", ""))) and o.get("label")
                    for o in options)
            and d.get("default") in {o["id"] for o in options}
            and d.get("id") == path.stem and re.fullmatch(r"[\w-]+", path.stem) and d.get("question"))


def option_label(d, option):
    return next((o["label"] for o in d.get("options") or [] if o.get("id") == option), option)


def answer_decision(project, path, d, option, by):
    d.update(answer=option, answered_by=by, answered_at=time.strftime("%FT%T%z"))
    write_json(path, d)
    log(f"{project['name']}: decisão {d.get('id')} = {option} ({by})")
    return option_label(d, option)


def fetch_messages(cfg, topic, since, label):
    """Mensagens de um tópico desde `since`; None se a rede falhar."""
    url = f"{ntfy_server(cfg)}/{topic}/json?poll=1&since={urllib.parse.quote(since)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            lines = r.read().decode(errors="replace").splitlines()
    except OSError as e:
        log(f"ntfy ({label}) falhou: {e}")
        return None
    out = []
    for line in lines:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("event") == "message":
            out.append(msg)
    return out


def owner_answer(cfg, project, path, d, option):
    label = answer_decision(project, path, d, option, "owner")
    ntfy(cfg, f"{project['name']}: anotado", f"{d['question']}\n→ {label}", priority=2, tags=["white_check_mark"])


def open_decisions(project):
    """Decisões já enviadas ao celular e ainda sem resposta: as únicas que aceitam resposta."""
    out = []
    for path in decision_files(project):
        d = read_json(path)
        if d and "answer" not in d and d.get("notified_at") and valid_decision(d, path):
            out.append((path, d))
    return out


# Letra da opção no início, sozinha ou seguida de pontuação: "B", "b)", "B, manter até…".
# O projeto pode vir antes: "my-app B", "my-app: b". "a primeira" não casa (letra seguida de palavra).
TYPED_REPLY = re.compile(r"(?:([\w-]+)[\s/:]+)?([a-z])\s*(?:$|[,.;:)\-–—])", re.I)


def note_targets(projects, text):
    """Projetos citados pelo nome na frase; nenhum citado = todos."""
    named = [p for name, p in projects.items() if re.search(rf"\b{re.escape(name)}\b", text, re.I)]
    return named or list(projects.values())


def leave_note(cfg, targets, text):
    for p in targets:
        coord.add_note(p["name"], text)
    names = ", ".join(p["name"] for p in targets)
    ntfy(cfg, "autopilot: mensagem anotada", f"Para {names}, na próxima unidade:\n{text[:200]}",
         priority=2, tags=["memo"])
    log(f"mensagem do dono anotada para {names}")


def typed_reply(cfg, projects, text):
    """Mensagem digitada no app: a letra no início responde a uma decisão aberta; o resto vai aos agentes.

    Sem proteção, por escolha do Paulo: quem souber o tópico também consegue mandar mensagens. As
    regras "Never" do protocolo continuam valendo acima de qualquer mensagem.
    """
    if not text or text.lower() == "triggered":  # "triggered" é o texto de uma mensagem vazia no ntfy
        return
    m = TYPED_REPLY.match(text)
    if m and m.group(1) and m.group(1).lower() not in projects:
        m = None
    if not m:
        leave_note(cfg, note_targets(projects, text), text)
        return
    comment = text[m.end():].strip(" ,.;:-–—")
    option = m.group(2).lower()
    scope = [projects[m.group(1).lower()]] if m.group(1) else list(projects.values())
    candidates = [(p, path, d) for p in scope for path, d in open_decisions(p)]
    fits = [(p, path, d) for p, path, d in candidates if option in {o["id"] for o in d["options"]}]
    if comment and not fits:
        # "E aí, tudo certo?" começa como uma letra, mas não responde a nada: é uma mensagem.
        leave_note(cfg, note_targets(projects, text), text)
        return
    if not candidates:
        ntfy(cfg, "autopilot: nenhuma pergunta aberta", f"Não há decisão esperando resposta. Ignorei: {text[:80]}",
             priority=2, tags=["grey_question"])
        return
    if len(candidates) > 1:
        names = sorted({p["name"] for p, _, _ in candidates})
        hint = f"ponha o projeto antes da letra, por exemplo: {names[0]} {option.upper()}" if len(names) > 1 \
            else "use os botões da notificação"
        ntfy(cfg, "autopilot: qual pergunta?", f"Há {len(candidates)} perguntas abertas; {hint}.",
             priority=3, tags=["grey_question"])
        return
    project, path, d = candidates[0]
    ids = {o["id"] for o in d["options"]}
    if option not in ids:
        ntfy(cfg, f"{project['name']}: opção inexistente",
             f"{d['question']}\nAs opções são: {', '.join(sorted(i.upper() for i in ids))}.",
             priority=3, tags=["grey_question"])
        return
    owner_answer(cfg, project, path, d, option)
    if comment:
        coord.add_note(project["name"], f"Comentário na resposta à decisão {d['id']} (opção {option.upper()}): {comment}")


def poll_replies(cfg, notify):
    """Lê as respostas: os botões (tópico -reply, formato projeto/decisão/opção) e as digitadas no app.

    `notify` é o estado compartilhado do ntfy (since, since_typed), o mesmo para as duas instâncias.
    """
    if not (cfg.get("notify") or {}).get("topic"):
        return
    now = str(int(time.time()))
    notify.setdefault("since", now)
    notify.setdefault("since_typed", now)
    projects = {p["name"]: p for p in cfg["project"]}
    for msg in fetch_messages(cfg, reply_topic(cfg), notify["since"], "respostas") or []:
        notify["since"] = msg["id"]
        m = re.fullmatch(r"([\w-]+)/([\w-]+)/([\w-]+)", (msg.get("message") or "").strip())
        if not m or m.group(1) not in projects:
            continue
        project = projects[m.group(1)]
        path = Path(project["path"]) / DECISIONS / f"{m.group(2)}.json"
        d = read_json(path)
        # Resposta a decisão inexistente, já respondida ou com opção fora da lista é ignorada.
        if not d or "answer" in d or not valid_decision(d, path) or m.group(3) not in {o["id"] for o in d["options"]}:
            continue
        owner_answer(cfg, project, path, d, m.group(3))
    for msg in fetch_messages(cfg, cfg["notify"]["topic"], notify["since_typed"], "respostas digitadas") or []:
        notify["since_typed"] = msg["id"]
        # Tudo o que o agendador publica tem título; mensagem sem título é alguém digitando no app.
        if msg.get("title"):
            continue
        typed_reply(cfg, projects, (msg.get("message") or "").strip())


def dispatch_decisions(cfg, notify):
    """Lê respostas, envia as decisões novas e aplica o padrão vencido o prazo."""
    hours = (cfg.get("notify") or {}).get("decision_timeout_hours", 4)
    poll_replies(cfg, notify)
    now = time.time()
    for p in cfg["project"]:
        for path in decision_files(p):
            d = read_json(path)
            if d is None or "answer" in d:
                continue
            if not valid_decision(d, path):
                # Formato errado: não dá para perguntar; a próxima unidade vê e corrige.
                d.update(answer=None, answered_by="invalid-format", answered_at=time.strftime("%FT%T%z"))
                write_json(path, d)
                log(f"{p['name']}: decisão com formato inválido em {path}")
            elif not d.get("notified_at"):
                d.update(notified_at=now, deadline=now + hours * 3600)
                write_json(path, d)
                lines = [f"{o['id'].upper()}) {o['label']}" for o in d["options"]]
                actions = [{"action": "http", "label": o["label"][:40], "url": f"{ntfy_server(cfg)}/{reply_topic(cfg)}",
                            "method": "POST", "body": f"{p['name']}/{d['id']}/{o['id']}", "clear": True}
                           for o in d["options"]]
                message = "\n".join([d["question"], "", d.get("context", ""), "", *lines, "",
                                     f"Responda pelos botões ou digitando a letra (ex.: {d['options'][0]['id'].upper()}).",
                                     f"Sem resposta em {hours} h: {option_label(d, d['default'])}"])
                ntfy(cfg, f"{p['name']}: decisão", message.strip(), priority=4, tags=["question"], actions=actions)
                log(f"{p['name']}: decisão {d['id']} enviada; prazo {hours} h")
            elif now > d.get("deadline", 0):
                label = answer_decision(p, path, d, d["default"], "timeout")
                ntfy(cfg, f"{p['name']}: sem resposta", f"{d['question']}\n→ {label} (padrão)", tags=["hourglass"])


def process_decisions(cfg, state):
    """Fala com o ntfy (uma instância por vez) e libera os projetos cujas decisões foram resolvidas."""
    lock = coord.try_lock("decisions")
    if lock:
        try:
            # Na primeira vez, a posição de leitura vem do state.json desta instância.
            legacy = {k: state["_notify"][k] for k in ("since", "since_typed") if k in state["_notify"]}
            notify = coord.load_notify(legacy)
            dispatch_decisions(cfg, notify)
            coord.save_notify(notify)
        finally:
            coord.unlock(lock)
    for p in cfg["project"]:
        s = state[p["name"]]
        pending = any(d and "answer" not in d for d in map(read_json, decision_files(p)))
        if s["status"] == "waiting" and not pending:
            s["status"] = "active"
            log(f"{p['name']}: decisões resolvidas; projeto liberado")


def next_deadline(project):
    deadlines = [d.get("deadline", 0) for d in map(read_json, decision_files(project))
                 if d and "answer" not in d and d.get("deadline")]
    return min(deadlines) if deadlines else time.time() + 300


def nap(cfg, state, secs):
    """Dorme em fatias de 1 min lendo respostas; acorda cedo quando um projeto em espera é liberado."""
    end = time.time() + secs
    while (left := end - time.time()) > 0:
        time.sleep(min(60, left))
        before = {p["name"]: state[p["name"]]["status"] for p in cfg["project"]}
        process_decisions(cfg, state)
        save_state(state)
        if any(state[name]["status"] != status for name, status in before.items()):
            return


# --- execução ---------------------------------------------------------------------------------


def git(path, *args):
    return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True).stdout.strip()


def is_dirty(path):
    # Os arquivos do próprio autopilot não contam: o agendador grava a resposta no arquivo da
    # decisão, e num projeto que versiona essa pasta isso deixaria a árvore suja para sempre.
    return bool(git(path, "status", "--porcelain", "--", ".", f":(exclude){UNIT_FILE.parent}"))


def compose(path, *args):
    if not (Path(path) / "docker-compose.yml").exists() and not (Path(path) / "compose.yaml").exists():
        return True
    r = subprocess.run(["docker", "compose", *args], cwd=path, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"docker compose {' '.join(args)} falhou em {path}: {r.stderr.strip()[-300:]}")
    return r.returncode == 0


DOCKER_DESKTOP = Path("/mnt/c/Program Files/Docker/Docker/Docker Desktop.exe")


def docker_up():
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def ensure_docker(cfg):
    """Garante o daemon do Docker; abre o Docker Desktop do Windows se estiver fechado."""
    if docker_up():
        return True
    if DOCKER_DESKTOP.exists():
        log("Docker fora do ar; abrindo o Docker Desktop")
        subprocess.Popen([str(DOCKER_DESKTOP)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(18):
            time.sleep(10)
            if docker_up():
                log("Docker no ar")
                return True
    return False


def prepare_docker(cfg, project):
    """Deixa o stack do projeto no ar. Devolve "ok", "docker-down", "busy" ou "compose-failed"."""
    if not ensure_docker(cfg):
        log("Docker não respondeu; nenhuma unidade roda até ele voltar")
        return "docker-down"
    group = project.get("docker_group")
    if group:
        for other in cfg["project"]:
            if other is not project and other.get("docker_group") == group:
                running = subprocess.run(["docker", "compose", "ps", "-q"], cwd=other["path"],
                                         capture_output=True, text=True).stdout.strip()
                if not running:
                    continue
                if is_dirty(other["path"]):
                    # Alguém trabalha no outro projeto: parar o stack dele quebraria esse trabalho.
                    log(f"{project['name']}: portas ocupadas por {other['name']}, que tem trabalho em andamento; esperando")
                    return "busy"
                log(f"parando o stack de {other['name']} (mesmas portas de {project['name']})")
                compose(other["path"], "stop")
    # --wait espera os healthchecks: a unidade não começa com o banco ainda subindo.
    if not compose(project["path"], "up", "-d", "--wait", "--wait-timeout", "180"):
        return "compose-failed"
    return "ok"


def owner_messages(messages):
    lines = [f"- [{time.strftime('%Y-%m-%d %H:%M', time.localtime(n['at']))}] {n['text']}" for n in messages]
    return ("Owner messages (sent from the phone, oldest first; they may be in Portuguese):\n"
            + "\n".join(lines) + "\n"
            "Follow them in this unit when they concern this project and ignore those that do not. Record "
            "a lasting instruction in the decisions file so later units keep it. They never override step 7.")


CUT = "cut"  # "código de saída" de uma unidade cortada no fim da janela
CURRENT = None  # processo da unidade em andamento, para o Ctrl+C encerrar o grupo dele


def stop_group(proc):
    """SIGTERM no grupo do processo; SIGKILL se ele não sair em 30 s."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    except ProcessLookupError:
        pass


def wait_until(proc, deadline):
    """Espera o processo; passado `deadline` (timestamp), encerra o grupo dele e devolve CUT."""
    while True:
        left = None if deadline is None else deadline - time.time()
        if left is not None and left <= 0:
            stop_group(proc)
            return CUT
        try:
            return proc.wait(timeout=None if left is None else min(left, 60))
        except subprocess.TimeoutExpired:
            continue


def set_phase(state, phase, project=None, until=None):
    """O que o loop está fazendo, para o painel: running, sleeping ou outside_window."""
    state["_loop"] = {"phase": phase, "project": project, "until": until}


def idle_phase(win, now):
    return "outside_window" if win and not win.contains(now) else "sleeping"


def run_unit(cfg, engine, state, project, deadline=None):
    global CURRENT
    s = state[project["name"]]
    path = project["path"]
    resume = engine.resumable(s)
    # replace, não format: os prompts têm exemplos de JSON com chaves.
    prompt = (PROMPTS / ("resume.md" if resume else "unit.md")).read_text().replace("{project}", project["name"])
    notes = PROMPTS / f"engine-{engine.name}.md"
    if notes.exists():
        prompt += "\n\n" + notes.read_text()
    messages = coord.pending_notes(project["name"])
    if messages:
        prompt += "\n\n" + owner_messages(messages)
    # Entregues só quando a unidade fechar; numa retomada elas vão de novo.
    s["notes_sent"] = [n["id"] for n in messages]
    unit_file = Path(path) / UNIT_FILE
    if not resume:
        unit_file.unlink(missing_ok=True)

    RUNS.mkdir(exist_ok=True)
    stem = RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-{project['name']}"
    out, err = f"{stem}.jsonl", f"{stem}.err"
    if not s.get("unit_id"):
        s["unit_id"] = stem.name  # a primeira execução dá nome à unidade; retomadas e bastão o mantêm
    s["run"] = {"id": stem.name, "started": time.time()}
    engine.begin(s, resume)
    cmd, env = engine.command(state, project, s, resume, stem)
    log(f"{project['name']}: {'retomando sessão ' + s['session'] if resume else 'unidade nova'}")
    set_phase(state, "running", project["name"])
    s["interrupted"] = True  # só volta a False quando a unidade termina de fato
    save_state(state)  # se o loop morrer agora, a unidade continua marcada como aberta
    with open(out, "w") as fo, open(err, "w") as fe:
        # Grupo próprio: o corte e o Ctrl+C encerram também os processos que a CLI abriu.
        CURRENT = subprocess.Popen(cmd, cwd=path, stdin=subprocess.PIPE, stdout=fo, stderr=fe, text=True,
                                   env=env, start_new_session=True)
        try:
            CURRENT.stdin.write(prompt)
            CURRENT.stdin.close()
        except BrokenPipeError:
            pass
        code = wait_until(CURRENT, deadline)
        CURRENT = None
    engine.after_run(state, s, out, resume)
    for old in sorted(RUNS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[KEEP_RUNS * 3:]:
        old.unlink(missing_ok=True)
    return out, err, code, unit_file


def record_run(engine, project, s, outcome, result=None, cost=None):
    """Uma linha no histórico por execução encerrada. Resumo e custo só na que fecha a unidade."""
    run = s.get("run") or {}
    entry = {"unit_id": s.get("unit_id") or run.get("id"), "run": run.get("id"), "project": project["name"],
             "engine": engine.name, "started_at": history.iso(run.get("started") or time.time()),
             "ended_at": history.iso(time.time()), "outcome": outcome, "closed": result is not None}
    if result is not None:
        entry.update(unit=result.get("unit"), summary=result.get("summary"),
                     milestone=result.get("milestone"), cost=cost)
    try:
        history.append(HISTORY, entry)
    except OSError as e:
        log(f"histórico não gravado: {e}")
    s["run"] = None


def after_unit(cfg, engine, state, project, out, err, code, unit_file):
    """Atualiza o estado do projeto e devolve quantos segundos dormir antes da próxima unidade."""
    d = cfg["defaults"]
    s = state[project["name"]]
    if code == CUT:
        record_run(engine, project, s, "cut")
        pass_baton(engine, project["name"], s)
        log(f"{project['name']}: unidade cortada no fim da janela; bastão passado")
        return 5
    status, reset, hard = engine.classify(out, err, code)

    if status == "usage_limit":
        record_run(engine, project, s, "usage_limit")
        s["errors"] = 0
        wait = (reset - time.time() + 120) if reset else (1800 if hard else 300)
        wait = max(wait, 300)
        log(f"{project['name']}: limite de uso; a unidade continua depois do reset "
            f"({time.strftime('%F %T', time.localtime(time.time() + wait))})")
        return wait

    if status == "error":
        record_run(engine, project, s, "error")
        s["errors"] += 1
        log(f"{project['name']}: erro (exit={code}, tentativa {s['errors']}; ver {err})")
        if s["errors"] % 3 == 0:
            # Sessão que não retoma: a próxima unidade recomeça do ledger (passo de recuperação).
            log(f"{project['name']}: descartando a sessão após erros seguidos")
            s["session"] = None
            ntfy(cfg, f"{project['name']}: erros seguidos",
                 f"{s['errors']} execuções com erro. Último log: {err}", priority=4, tags=["warning"])
        return min(60 * 2 ** min(s["errors"], 5), 1800)

    s["errors"] = 0
    try:
        result = json.loads(unit_file.read_text())
    except (OSError, ValueError):
        result = None
    if not result:
        record_run(engine, project, s, "unfinished")
        # Resposta terminou sem fechar a unidade: retoma a mesma sessão, até duas vezes.
        s["early_ends"] += 1
        if s["early_ends"] >= 3:
            log(f"{project['name']}: a unidade não fecha; recomeçando do ledger em sessão nova")
            s["session"], s["early_ends"] = None, 0
        else:
            log(f"{project['name']}: resposta terminou sem last-unit.json; retomando")
        return 5

    # Mede a sessão inteira só quando a unidade fecha, para não contar duas vezes uma retomada.
    cost = engine.unit_cost(state, s)
    if cost:
        for key in ("primary", "secondary"):
            s["cost"][key] = round((1 - EMA_ALPHA) * s["cost"][key] + EMA_ALPHA * cost[key], 3)
    record_run(engine, project, s, result.get("status", "worked"), result=result, cost=cost)
    engine.close_unit(s)
    s.update(session=None, interrupted=False, early_ends=0, unit_id=None)
    coord.release(project["name"], engine.name)
    if s["notes_sent"]:
        coord.mark_delivered(project["name"], s["notes_sent"])
        ntfy(cfg, f"{project['name']}: mensagem entregue ao {engine.label}",
             f"{len(s['notes_sent'])} mensagem(ns) lida(s) nesta unidade: {result.get('unit', '?')}",
             priority=2, tags=["envelope"])
        s["notes_sent"] = []
    unit_status = result.get("status", "worked")
    summary = result.get("summary", "")
    log(f"{project['name']}: {unit_status} — {result.get('unit', '?')}")
    if not backlog.valid_backlog(read_json(Path(project["path"]) / backlog.FILE)):
        log(f"{project['name']}: backlog.json ausente ou inválido; o painel fica sem o andamento")
    if unit_status == "idle":
        s["status"] = "idle"
        ntfy(cfg, f"{project['name']}: sem trabalho", summary or "Backlog concluído.", tags=["checkered_flag"])
    elif unit_status == "blocked":
        s["status"] = "blocked"
        s["blocked_until"] = time.time() + d["blocked_cooldown_hours"] * 3600
        ntfy(cfg, f"{project['name']}: bloqueado", summary, priority=4, tags=["no_entry"])
    elif unit_status == "waiting":
        s["status"] = "waiting"
    if result.get("milestone"):
        ntfy(cfg, f"{project['name']}: {result['milestone']}", summary, priority=2, tags=["tada"])
    return 5


def pick(cfg, engine, state, limits):
    """Escolhe o próximo projeto e pega a trava do grupo dele.

    Devolve (projeto, trava, None) ou (None, None, segundos até valer checar de novo).
    """
    d = cfg["defaults"]
    pri_used, pri_reset = window(limits, "primary")
    sec_used, sec_reset = window(limits, "secondary")
    now = time.time()
    waits = []

    # Unidade interrompida deste motor e bastão de qualquer motor vão primeiro: nada fica pela metade.
    for p in cfg["project"]:
        name = p["name"]
        if not (state[name]["interrupted"] or coord.is_handoff(name)) or coord.held_by_other(name, engine.name):
            continue
        lock = coord.try_lock(coord.group_of(p))
        if lock:
            return p, lock, None
        waits.append(60)

    for p in cfg["project"]:
        name = p["name"]
        s = state[name]
        if s["interrupted"] or coord.is_handoff(name):
            continue  # já tratado acima (o grupo estava ocupado)
        if s["status"] == "idle":
            continue
        if s["status"] == "waiting":
            waits.append(max(60, next_deadline(p) - now))
            continue
        if s["status"] == "blocked":
            if s["blocked_until"] > now:
                waits.append(s["blocked_until"] - now)
                continue
            s["status"] = "active"
        if coord.held_by_other(name, engine.name):
            waits.append(600)
            continue
        if is_dirty(p["path"]):
            if not s["dirty_logged"]:
                log(f"{name}: árvore com alterações que não são do autopilot; pulando até ficar limpa")
                s["dirty_logged"] = True
            waits.append(600)
            continue
        s["dirty_logged"] = False
        if sec_used + d["safety_factor"] * s["cost"]["secondary"] > p["weekly_cap"]:
            if sec_reset:
                waits.append(sec_reset - now + 120)
            continue
        # Janela de 5 h por projeto: se o primeiro da fila não cabe, um mais barato ainda pode rodar.
        if pri_used + d["safety_factor"] * s["cost"]["primary"] > d["primary_cap"]:
            waits.append((pri_reset - now + 120) if pri_reset else 1800)
            continue
        lock = coord.try_lock(coord.group_of(p))
        if not lock:
            waits.append(60)  # o outro motor roda um projeto do mesmo grupo Docker
            continue
        return p, lock, None
    return None, None, (min(waits) if waits else 3600)


def pass_baton(engine, name, s):
    """Fim da janela: o trabalho fica no repositório e no ledger, e qualquer motor pode retomar."""
    engine.close_unit(s)
    coord.hand_off(name, engine.name, s.get("unit_id"))
    s.update(interrupted=False, session=None, early_ends=0, errors=0, unit_id=None)


def hand_off_open_units(cfg, engine, state):
    """Fora da janela nenhuma unidade deste motor fica aberta: passa o bastão das interrompidas."""
    for p in cfg["project"]:
        s = state[p["name"]]
        if s["interrupted"] and not coord.held_by_other(p["name"], engine.name):
            pass_baton(engine, p["name"], s)
            log(f"{p['name']}: janela fechada com a unidade interrompida; bastão passado")


def window_notice(cfg, engine, state, active):
    """Avisa o começo e o fim da janela, com o uso da cota semanal."""
    # No fechamento vale a última leitura: fora da janela a cota não é consultada.
    used, _ = window(engine.limits(state, max_age=0 if active else float("inf")), "secondary")
    if active:
        cap = max(p["weekly_cap"] for p in cfg["project"])
        log(f"janela aberta; cota semanal em {used:.0f}%")
        ntfy(cfg, f"{engine.label}: começou o fim de semana", f"Cota semanal em {used:.0f}%; teto {cap}%.",
             priority=2, tags=["arrow_forward"])
        return
    handed = [name for name, c in coord.claims().items()
              if c and c.get("engine") == coord.ANY and c.get("handoff_from") == engine.name]
    tail = f"Ficaram com o bastão: {', '.join(handed)}." if handed else "Nenhuma unidade ficou pela metade."
    log(f"janela fechada; cota semanal em {used:.0f}%")
    ntfy(cfg, f"{engine.label}: fim de semana encerrado", f"Cota semanal em {used:.0f}%. {tail}",
         priority=2, tags=["stop_button"])


def run_once(cfg, engine, state, win=None, now=None):
    """Uma volta do loop. Devolve quantos segundos dormir, ou None quando não há mais trabalho."""
    process_decisions(cfg, state)
    started = time.time()
    now = now or datetime.datetime.now()
    if win:
        active = win.contains(now)
        if not active:
            hand_off_open_units(cfg, engine, state)
        if active != state["_window"]["active"]:
            state["_window"]["active"] = active
            window_notice(cfg, engine, state, active)
        if not win.can_start(now):
            # Fora da janela, nem a cota é consultada: ela é do Paulo nesses dias.
            until = win.end_of(now) if active else win.next_start(now)
            save_state(state)
            return max(60, (until - now).total_seconds())
    limits = engine.limits(state)
    if engine.needs_limits and not limits:
        log("sem leitura da cota; nenhuma unidade nova até conseguir ler")
        save_state(state)
        return 600
    project, lock, wait = pick(cfg, engine, state, limits)
    save_state(state)
    if project is None:
        if all(state[p["name"]]["status"] == "idle" for p in cfg["project"]):
            log("todos os projetos idle; fim")
            ntfy(cfg, f"{engine.label}: fim", "Todos os projetos terminaram o backlog.", tags=["checkered_flag"])
            return None
        # Sem registro no log: ele mostra só o trabalho. O motivo da espera aparece no `check`.
        return max(wait, 60)
    try:
        wait = run_project(cfg, engine, state, project, limits, win)
    finally:
        coord.unlock(lock)
    if win:
        # Uma espera longa (limite de uso, erro) não pode passar do fim da janela: no fechamento o
        # loop precisa estar acordado para passar o bastão.
        left = (win.end_of(now) - now).total_seconds() - (time.time() - started)
        wait = min(wait, max(5, left + 5))
    return wait


def run_project(cfg, engine, state, project, limits, win):
    """Roda uma unidade no projeto escolhido (a trava do grupo já está com quem chama)."""
    name = project["name"]
    s = state[name]
    current = coord.read_claim(name)
    handoff = bool(current) and current.get("engine") == coord.ANY
    if not coord.claim(name, engine.name):
        log(f"{name}: reservado pelo outro motor; pulando")
        return 60
    if handoff:
        log(f"{name}: pegando o bastão deixado por {current.get('handoff_from')}")
        engine.close_unit(s)
        s.update(interrupted=True, session=None, early_ends=0, unit_id=current.get("unit_id"))

    docker = prepare_docker(cfg, project)
    if docker != "ok":
        if not s["interrupted"]:
            coord.release(name, engine.name)  # nada começou: o projeto fica livre para o outro motor
        if docker != "busy" and not state["_notify"].get("docker_alerted"):
            # Sem os containers a unidade só gastaria cota; avisa uma vez e tenta de novo em 10 min.
            ntfy(cfg, f"{engine.label}: Docker com problema",
                 f"Não consegui subir os containers de {name} ({docker}). Veja o log.", priority=4, tags=["warning"])
            state["_notify"]["docker_alerted"] = True
        return 600
    state["_notify"]["docker_alerted"] = False
    deadline = win.end_of(datetime.datetime.now()).timestamp() if win else None
    out, err, code, unit_file = run_unit(cfg, engine, state, project, deadline)
    wait = after_unit(cfg, engine, state, project, out, err, code, unit_file)
    save_state(state)
    return wait


def cmd_run(args):
    cfg = load_config()
    engine = engines.make_engine(cfg)
    instance = instance_lock(engine.name)  # mantido aberto enquanto o loop roda
    if instance is None:
        sys.exit(f"o loop do {engine.label} já está rodando (pid {running_pid(engine.name)})")
    state = load_state(cfg)
    win = schedule.from_config(cfg)

    def on_signal(*_):
        if CURRENT is not None:
            stop_group(CURRENT)
        save_state(state)
        log("interrompido; rode de novo para retomar")
        sys.exit(130)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    log(f"autopilot iniciado ({engine.describe()}{', janela ' + win.describe() if win else ''})")
    open_units = {p["name"]: state[p["name"]]["interrupted"] for p in cfg["project"]}
    for name in coord.reconcile(engine.name, open_units):
        log(f"{name}: unidade aberta aqui, mas o projeto está reservado pelo outro motor; deixando de lado")
        ntfy(cfg, f"{engine.label}: projeto em conflito",
             f"{name} tem unidade aberta nos dois autopilots. Veja os logs.", priority=4, tags=["warning"])
    save_state(state)
    while True:
        wait = run_once(cfg, engine, state, win)
        if wait is None:
            return
        set_phase(state, idle_phase(win, datetime.datetime.now()), until=time.time() + wait)
        save_state(state)
        nap(cfg, state, wait)


def status_lines(cfg, engine, state, limits, win, now):
    out = [f"motor {engine.label}"]
    if win:
        if win.contains(now):
            out.append(f"janela {win.describe()}: dentro, fecha {schedule.label(win.end_of(now))}")
        else:
            out.append(f"janela {win.describe()}: fora, abre {schedule.label(win.next_start(now))}")
    if engine.needs_limits and not limits:
        out.append("cota: sem leitura")
    for key, label in (("primary", "5 h"), ("secondary", "semanal")):
        used, reset = window(limits, key)
        when = time.strftime("%F %T", time.localtime(reset)) if reset else "-"
        out.append(f"janela {label:8} {used:5.1f}%   reset {when}")
    if hasattr(engine, "fable_available") and not engine.fable_available(state):
        until = state["_engine"]["fable_exhausted_until"]
        out.append(f"Fable poupado até {time.strftime('%F %T', time.localtime(until))}; planejamento no Opus")
    out.append("")
    claims = coord.claims()
    for p in cfg["project"]:
        s = state[p["name"]]
        flags = []
        if s["interrupted"]:
            flags.append("unidade interrompida")
        if s["session"]:
            flags.append(f"sessão {s['session']}")
        c = claims.get(p["name"])
        if c and c.get("engine") == coord.ANY:
            flags.append(f"bastão de {c.get('handoff_from')}")
        elif c:
            flags.append(f"reservado: {c.get('engine')}")
        if is_dirty(p["path"]):
            flags.append("árvore suja")
        out.append(f"{p['name']:10} {s['status']:8} teto {p['weekly_cap']:>3}%  "
                   f"custo/unidade {s['cost']['primary']:.1f}% (5 h) {s['cost']['secondary']:.2f}% (semana)  "
                   f"{', '.join(flags)}")
    pending = sum(1 for p in cfg["project"] for d in map(read_json, decision_files(p)) if d and "answer" not in d)
    if pending:
        out.append(f"\n{pending} decisão(ões) pendente(s): autopilot.py decisions")
    return out


# --- iniciar e parar os dois loops --------------------------------------------------------------

TMUX_SESSION = "autopilot"
# (janela do tmux e nome do motor, configuração): a do Codex é a padrão.
LOOPS = (("codex", None), ("claude", HOME / "claude.toml"))


def instance_lock(name):
    """Um loop por motor: o flock dura enquanto o loop roda e o arquivo guarda o pid de quem o tem."""
    handle = coord.try_lock(f"instance-{name}")
    if handle is not None:
        handle.truncate(0)
        handle.write(str(os.getpid()))
        handle.flush()
    return handle


def running_pid(name):
    """O pid do loop do motor, se ele estiver rodando; None se a trava estiver livre."""
    handle = coord.try_lock(f"instance-{name}")
    if handle is not None:
        coord.unlock(handle)
        return None
    try:
        return int((coord.shared_dir() / f"instance-{name}.lock").read_text().strip())
    except (OSError, ValueError):
        return 0  # rodando, mas o pid ainda não foi gravado


def is_loop(argv, config):
    """Se a linha de comando é a de um loop (`autopilot.py [--config X] run`) dessa configuração."""
    if len(argv) < 3 or not argv[1].endswith("autopilot.py") or argv[-1] != "run":
        return False
    given = argv[argv.index("--config") + 1] if "--config" in argv[:-1] else None
    if config is None:
        return given is None
    return given is not None and Path(given).name == Path(config).name


def loop_pids(config):
    """Loops dessa configuração já rodando, inclusive os iniciados à mão do jeito antigo."""
    pids = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            argv = (proc / "cmdline").read_bytes().decode(errors="replace").split("\0")[:-1]
        except OSError:
            continue
        if int(proc.name) != os.getpid() and is_loop(argv, config):
            pids.append(int(proc.name))
    return pids


def tmux(*args):
    r = subprocess.run(["tmux", *args], capture_output=True, text=True)
    return r.returncode, r.stdout


def loop_command(config):
    script = shlex.quote(str(Path(__file__).resolve()))
    return f"python3 {script}" + (f" --config {shlex.quote(str(config))}" if config else "") + " run"


def log_file(name, config):
    return HOME / ("autopilot.log" if config is None else f"autopilot-{name}.log")


def open_logs_window(has_session, windows):
    """Janela "logs": os dois logs ao vivo, um em cima do outro. É a que o `tmux attach` mostra."""
    target = f"{TMUX_SESSION}:logs"
    if "logs" not in windows:
        (top_name, top_cfg), (bottom_name, bottom_cfg) = LOOPS
        top = f"tail -n 40 -F {shlex.quote(str(log_file(top_name, top_cfg)))}"
        bottom = f"tail -n 40 -F {shlex.quote(str(log_file(bottom_name, bottom_cfg)))}"
        if has_session:
            tmux("new-window", "-d", "-t", TMUX_SESSION, "-n", "logs", top)
        else:
            tmux("new-session", "-d", "-s", TMUX_SESSION, "-n", "logs", top)
        tmux("split-window", "-v", "-d", "-t", target, bottom)
        tmux("set-option", "-w", "-t", target, "pane-border-status", "top")
        tmux("set-option", "-w", "-t", target, "pane-border-format", " #{pane_title} ")
        tmux("select-pane", "-t", f"{target}.0", "-T", top_name.capitalize())
        tmux("select-pane", "-t", f"{target}.1", "-T", bottom_name.capitalize())
    tmux("select-window", "-t", target)


def start_loops():
    """Os dois loops numa sessão tmux, uma janela cada; não duplica um loop que já roda."""
    has_session = tmux("has-session", "-t", TMUX_SESSION)[0] == 0
    windows = {}
    if has_session:
        for line in tmux("list-windows", "-t", TMUX_SESSION, "-F", "#{window_name} #{pane_dead}")[1].splitlines():
            name, _, dead = line.partition(" ")
            windows[name] = dead
    for name, config in LOOPS:
        if running_pid(name) is not None or loop_pids(config):
            print(f"{name}: já está rodando")
            continue
        cmd = loop_command(config)
        if not has_session:
            tmux("new-session", "-d", "-s", TMUX_SESSION, "-n", name, cmd)
            # A janela fica aberta quando o loop termina: dá para ler a última mensagem.
            tmux("set-option", "-t", TMUX_SESSION, "remain-on-exit", "on")
            has_session = True
        elif name in windows:
            tmux("respawn-window", "-k", "-t", f"{TMUX_SESSION}:{name}", cmd)
        else:
            tmux("new-window", "-d", "-t", TMUX_SESSION, "-n", name, cmd)
        print(f"{name}: iniciado")
    open_logs_window(has_session, windows)


def stop_loops():
    """Ctrl+C nos dois loops: cada um encerra a unidade em andamento e grava o estado."""
    for name, config in LOOPS:
        pids = {pid for pid in [running_pid(name), *loop_pids(config)] if pid}
        if not pids:
            print(f"{name}: não está rodando")
            continue
        for pid in pids:
            try:
                os.kill(pid, signal.SIGINT)
            except ProcessLookupError:
                pass
        for _ in range(60):
            if running_pid(name) is None and not loop_pids(config):
                break
            time.sleep(1)
        print(f"{name}: parado" if running_pid(name) is None else f"{name}: ainda encerrando (veja o log)")


def cmd_start(args):
    start_loops()
    print(f"acompanhe com: tmux attach -t {TMUX_SESSION}  (Ctrl+B e depois N troca de janela)")


def cmd_stop(args):
    stop_loops()


def cmd_status(args):
    """Não consulta o modelo: mostra a última leitura de cota que esta instância tem."""
    cfg = load_config()
    state = load_state(cfg)
    engine = engines.make_engine(cfg)
    limits = engine.limits(state, max_age=float("inf"))
    print("\n".join(status_lines(cfg, engine, state, limits, schedule.from_config(cfg), datetime.datetime.now())))


def cmd_check(args):
    """O que o loop faria agora, sem rodar nenhuma unidade e sem gravar o estado."""
    cfg = load_config()
    state = load_state(cfg)
    engine = engines.make_engine(cfg)
    win = schedule.from_config(cfg)
    now = datetime.datetime.now()
    limits = engine.limits(state, max_age=0)  # leitura nova (no Claude, uma consulta mínima)
    print("\n".join(status_lines(cfg, engine, state, limits, win, now)))
    print()
    if win and not win.can_start(now):
        print(f"agora: não começa unidade ({'últimos minutos da janela' if win.contains(now) else 'fora da janela'})")
        return
    if engine.needs_limits and not limits:
        print("agora: não começa unidade (sem leitura da cota)")
        return
    project, lock, wait = pick(cfg, engine, state, limits)
    coord.unlock(lock)
    if project:
        kind = "retomaria" if state[project["name"]]["interrupted"] or coord.is_handoff(project["name"]) else "começaria"
        print(f"agora: {kind} uma unidade em {project['name']}")
    else:
        print(f"agora: nada elegível; checaria de novo em {int(wait // 60)} min")


def cmd_decisions(args):
    cfg = load_config()
    found = False
    for p in cfg["project"]:
        for path in decision_files(p):
            d = read_json(path) or {}
            if "answer" in d:
                continue
            found = True
            deadline = time.strftime("%F %T", time.localtime(d["deadline"])) if d.get("deadline") else "ainda não enviada"
            print(f"{p['name']} / {d.get('id', path.stem)}   prazo {deadline}   padrão {d.get('default')}")
            print(f"  {d.get('question')}")
            for o in d.get("options") or []:
                print(f"    {o.get('id')}) {o.get('label')}")
    if not found:
        print("nenhuma decisão pendente")


def cmd_answer(args):
    cfg = load_config()
    project = next((p for p in cfg["project"] if p["name"] == args.name), None)
    if not project:
        sys.exit(f"projeto desconhecido: {args.name}")
    path = Path(project["path"]) / DECISIONS / f"{args.id}.json"
    d = read_json(path)
    if not d or "answer" in d:
        sys.exit("decisão inexistente ou já respondida")
    if args.option not in {o.get("id") for o in d.get("options") or []}:
        sys.exit(f"opção inválida; use uma de: {', '.join(o.get('id') for o in d['options'])}")
    print(f"{args.name}: {answer_decision(project, path, d, args.option, 'owner')}")


def cmd_test_notify(args):
    cfg = load_config()
    if not (cfg.get("notify") or {}).get("topic"):
        sys.exit("configure [notify].topic em projects.toml")
    ntfy(cfg, "autopilot: teste", "Se você está lendo isto no celular, as notificações funcionam.", tags=["bell"])
    print(f"enviado para {ntfy_server(cfg)}/{cfg['notify']['topic']}")


def cmd_reset(args):
    cfg = load_config()
    state = load_state(cfg)
    if args.name not in {p["name"] for p in cfg["project"]}:
        sys.exit(f"projeto desconhecido: {args.name}")
    state[args.name].update(status="active", blocked_until=0, errors=0)
    save_state(state)
    print(f"{args.name}: ativo")


def cmd_serve(args):
    import server  # só o painel precisa dele

    server.serve(HOME, args.port)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="configuração da instância (padrão: projects.toml)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").set_defaults(func=cmd_run)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("check").set_defaults(func=cmd_check)
    sub.add_parser("start").set_defaults(func=cmd_start)
    sub.add_parser("stop").set_defaults(func=cmd_stop)
    reset = sub.add_parser("reset")
    reset.add_argument("name")
    reset.set_defaults(func=cmd_reset)
    sub.add_parser("decisions").set_defaults(func=cmd_decisions)
    answer = sub.add_parser("answer")
    answer.add_argument("name")
    answer.add_argument("id")
    answer.add_argument("option")
    answer.set_defaults(func=cmd_answer)
    sub.add_parser("test-notify").set_defaults(func=cmd_test_notify)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    serve.set_defaults(func=cmd_serve)
    args = parser.parse_args()
    if args.cmd != "serve":  # o painel lê todas as instâncias; não escolhe uma
        configure(args.config)
    try:
        args.func(args)
    except Exception:
        if args.cmd == "serve":  # o painel não tem log próprio; não suja o log do loop do Codex
            raise
        # Sem isto o traceback some junto com a janela do tmux.
        import traceback
        log("erro inesperado; o loop parou:\n" + traceback.format_exc())
        if args.cmd == "run":
            ntfy(load_config(), "autopilot: parou", "Erro inesperado. Veja o fim do autopilot.log.",
                 priority=5, tags=["rotating_light"])
        raise


if __name__ == "__main__":
    main()
