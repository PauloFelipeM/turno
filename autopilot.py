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
  autopilot.py run                        loop principal (Ctrl+C para; rodar de novo retoma)
  autopilot.py status                     estado de cada projeto e o uso atual das janelas
  autopilot.py reset NOME                 volta um projeto idle ou blocked para ativo
  autopilot.py decisions                  decisões pendentes
  autopilot.py answer NOME ID OPÇÃO       responde uma decisão pelo terminal
  autopilot.py test-notify                manda uma notificação de teste
"""

import argparse
import datetime
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "projects.toml"
STATE = BASE / "state.json"
LOG = BASE / "autopilot.log"
RUNS = BASE / "runs"
PROMPTS = BASE / "prompts"
SESSIONS = Path.home() / ".codex" / "sessions"
UNIT_FILE = Path(".superpowers") / "autopilot" / "last-unit.json"
DECISIONS = Path(".superpowers") / "autopilot" / "decisions"
KEEP_RUNS = 80
EMA_ALPHA = 0.3


def log(msg):
    line = f"{time.strftime('%F %T')} {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def load_config():
    with CONFIG.open("rb") as f:
        return tomllib.load(f)


def load_state(cfg):
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    d = cfg["defaults"]
    # `since` do ntfy começa agora: respostas anteriores ao primeiro run não valem.
    state.setdefault("_notify", {"since": str(int(time.time()))})
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
    return state


def save_state(state):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE)


def find_codex():
    if os.environ.get("CODEX_BIN"):
        return os.environ["CODEX_BIN"]
    found = shutil.which("codex")
    if found:
        return found
    # A extensão do VS Code traz o binário; usa a versão mais nova instalada.
    exts = Path.home() / ".vscode-server" / "extensions"
    bins = sorted(exts.glob("openai.chatgpt-*/bin/linux-x86_64/codex"),
                  key=lambda p: [int(x) for x in re.findall(r"\d+", p.parts[-4])])
    if bins:
        return str(bins[-1])
    sys.exit("codex não encontrado; defina CODEX_BIN")


# --- cota -------------------------------------------------------------------------------------


def find_rate_limits(node):
    """Procura a chave `rate_limits` em qualquer nível de um evento do log da sessão."""
    if isinstance(node, dict):
        if isinstance(node.get("rate_limits"), dict):
            return node["rate_limits"]
        node = list(node.values())
    if isinstance(node, list):
        for child in node:
            found = find_rate_limits(child)
            if found:
                return found
    return None


def has_windows(limits):
    # O log também traz snapshots de outros limites (ex.: "premium") com as janelas vazias;
    # tomá-los como o uso atual faria a cota parecer 0% e esconderia o horário do reset.
    return isinstance(limits.get("primary"), dict) or isinstance(limits.get("secondary"), dict)


def snapshots(path):
    """Todos os `rate_limits` com janelas de um log de sessão, em ordem."""
    out = []
    try:
        with open(path, errors="replace") as f:
            for line in f:
                if '"rate_limits"' in line:
                    try:
                        found = find_rate_limits(json.loads(line))
                    except ValueError:
                        continue
                    if found and has_windows(found):
                        out.append(found)
    except OSError:
        pass
    return out


def window(limits, key):
    """(uso em %, resets_at) de uma janela; janela já resetada conta como 0%."""
    w = (limits or {}).get(key) or {}
    used, resets = float(w.get("used_percent") or 0), int(w.get("resets_at") or 0)
    if resets and resets <= time.time():
        return 0.0, 0
    return used, resets


def current_limits():
    """O snapshot mais recente entre as sessões mais novas (inclui uso interativo seu)."""
    files = sorted(SESSIONS.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[:10]:
        snaps = snapshots(path)
        if snaps:
            return snaps[-1]
    return {}


def session_files(thread):
    return list(SESSIONS.rglob(f"rollout-*{thread}*.jsonl")) if thread else []


def unit_cost(thread):
    """Custo da unidade em pontos percentuais, do primeiro ao último snapshot da sessão."""
    snaps = [s for f in session_files(thread) for s in snapshots(f)]
    if len(snaps) < 2:
        return None
    cost = {}
    for key in ("primary", "secondary"):
        a, b = (snaps[0].get(key) or {}), (snaps[-1].get(key) or {})
        # Reset no meio da unidade invalida a medida daquela janela.
        if a.get("resets_at") != b.get("resets_at"):
            return None
        cost[key] = max(0.0, float(b.get("used_percent") or 0) - float(a.get("used_percent") or 0))
    return cost


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


def typed_reply(cfg, projects, text):
    """Resposta digitada no app. Vale só para decisão pendente e opção listada, como os botões."""
    m = TYPED_REPLY.match(text)
    if m and m.group(1) and m.group(1).lower() not in projects:
        m = None
    scope = [projects[m.group(1).lower()]] if m and m.group(1) else list(projects.values())
    candidates = [(p, path, d) for p in scope for path, d in open_decisions(p)]
    if not m:
        # "triggered" é o texto que o ntfy põe numa mensagem vazia.
        if candidates and text.lower() != "triggered":
            ntfy(cfg, "autopilot: não entendi",
                 "Responda com a letra da opção, por exemplo: B. Com mais de uma pergunta aberta, "
                 "ponha o projeto antes: my-app B.", priority=3, tags=["grey_question"])
        return
    option = m.group(2).lower()
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


def poll_replies(cfg, state):
    """Lê as respostas: os botões (tópico -reply, formato projeto/decisão/opção) e as digitadas no app."""
    if not (cfg.get("notify") or {}).get("topic"):
        return
    meta = state["_notify"]
    projects = {p["name"]: p for p in cfg["project"]}
    for msg in fetch_messages(cfg, reply_topic(cfg), meta["since"], "respostas") or []:
        meta["since"] = msg["id"]
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
    for msg in fetch_messages(cfg, cfg["notify"]["topic"], meta["since_typed"], "respostas digitadas") or []:
        meta["since_typed"] = msg["id"]
        # Tudo o que o agendador publica tem título; mensagem sem título é alguém digitando no app.
        if msg.get("title"):
            continue
        typed_reply(cfg, projects, (msg.get("message") or "").strip())


def process_decisions(cfg, state):
    """Notifica decisões novas, lê respostas, aplica o padrão vencido o prazo e libera projetos."""
    hours = (cfg.get("notify") or {}).get("decision_timeout_hours", 4)
    poll_replies(cfg, state)
    now = time.time()
    for p in cfg["project"]:
        pending = 0
        for path in decision_files(p):
            d = read_json(path)
            if d is None or "answer" in d:
                continue
            if not valid_decision(d, path):
                # Formato errado: não dá para perguntar; a próxima unidade vê e corrige.
                d.update(answer=None, answered_by="invalid-format", answered_at=time.strftime("%FT%T%z"))
                write_json(path, d)
                log(f"{p['name']}: decisão com formato inválido em {path}")
                continue
            if not d.get("notified_at"):
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
                pending += 1
            elif now > d.get("deadline", 0):
                label = answer_decision(p, path, d, d["default"], "timeout")
                ntfy(cfg, f"{p['name']}: sem resposta", f"{d['question']}\n→ {label} (padrão)", tags=["hourglass"])
            else:
                pending += 1
        s = state[p["name"]]
        if s["status"] == "waiting" and pending == 0:
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


def failure_text(out, err):
    """Eventos de falha do JSONL mais o stderr, e se houve algum evento de falha."""
    failures = []
    try:
        with open(out, errors="replace") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") in ("error", "turn.failed"):
                    failures.append(json.dumps(ev, ensure_ascii=False))
    except OSError:
        pass
    try:
        stderr = Path(err).read_text(errors="replace")
    except OSError:
        stderr = ""
    return "\n".join(failures) + "\n" + stderr, bool(failures)


def retry_at(text):
    """Horário do "try again at ..." da mensagem de limite, como timestamp; None se não houver."""
    m = re.search(r"try again at (.*?\d{1,2}:\d{2}\s*[AP]M)", text, re.I)
    if not m:
        return None
    when = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", m.group(1)).strip()
    now = datetime.datetime.now()
    try:
        # Só a hora ("1:33 AM"): a próxima vez que ela chegar.
        t = datetime.datetime.strptime(when.upper(), "%I:%M %p").time()
        at = datetime.datetime.combine(now.date(), t)
        if at <= now:
            at += datetime.timedelta(days=1)
        return at.timestamp()
    except ValueError:
        pass
    for fmt in ("%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p", "%b %d %Y %I:%M %p", "%B %d %Y %I:%M %p"):
        try:
            return datetime.datetime.strptime(when, fmt).timestamp()
        except ValueError:
            continue
    return None


def classify(out, err, code):
    """'usage_limit', 'error' ou 'ok' a partir do JSONL, do stderr e do exit code."""
    text, failed = failure_text(out, err)
    if re.search(r"usage limit|usage_limit|rate.?limit", text, re.I):
        return "usage_limit", bool(re.search(r"usage limit|usage_limit", text, re.I))
    return ("error" if failed or code != 0 else "ok"), False


def thread_id(out):
    with open(out, errors="replace") as f:
        for line in f:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "thread.started":
                return ev.get("thread_id")
    return None


def run_unit(cfg, project, s):
    d = cfg["defaults"]
    # Procurado a cada unidade: a extensão do VS Code troca de pasta quando se atualiza.
    codex = find_codex()
    path = project["path"]
    resume = bool(s["session"])
    # replace, não format: os prompts têm exemplos de JSON com chaves.
    prompt = (PROMPTS / ("resume.md" if resume else "unit.md")).read_text().replace("{project}", project["name"])
    flags = ["--json", "-m", d["orchestrator_model"],
             "-c", f'model_reasoning_effort="{d["orchestrator_effort"]}"',
             "-c", f"agents.max_concurrent_threads_per_session={d['max_subagents']}",
             "--enable", "multi_agent",
             # Docker, rede e instalação de dependências exigem sair do sandbox.
             "--dangerously-bypass-approvals-and-sandbox"]
    cmd = [codex, "exec", "resume", *flags, s["session"], "-"] if resume else [codex, "exec", *flags, "-"]
    unit_file = Path(path) / UNIT_FILE
    if not resume:
        unit_file.unlink(missing_ok=True)

    RUNS.mkdir(exist_ok=True)
    stem = RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-{project['name']}"
    out, err = f"{stem}.jsonl", f"{stem}.err"
    log(f"{project['name']}: {'retomando sessão ' + s['session'] if resume else 'unidade nova'}")
    s["interrupted"] = True  # só volta a False quando a unidade termina de fato
    with open(out, "w") as fo, open(err, "w") as fe:
        code = subprocess.run(cmd, cwd=path, input=prompt, text=True, stdout=fo, stderr=fe).returncode
    if not resume:
        s["session"] = thread_id(out)
    for old in sorted(RUNS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[KEEP_RUNS * 2:]:
        old.unlink(missing_ok=True)
    return out, err, code, unit_file


def after_unit(cfg, project, s, out, err, code, unit_file):
    """Atualiza o estado do projeto e devolve quantos segundos dormir antes da próxima unidade."""
    d = cfg["defaults"]
    status, hard_limit = classify(out, err, code)

    if status == "usage_limit":
        s["errors"] = 0
        limits = current_limits()
        resets = [r for used, r in (window(limits, "primary"), window(limits, "secondary")) if used >= 90 and r]
        if not resets and (at := retry_at(failure_text(out, err)[0])):
            # Sem snapshot com o reset: vale o horário que a própria mensagem de erro informa.
            resets = [at]
        wait = (max(resets) - time.time() + 120) if resets else (1800 if hard_limit else 300)
        wait = max(wait, 300)
        log(f"{project['name']}: limite de uso; a unidade continua depois do reset "
            f"({time.strftime('%F %T', time.localtime(time.time() + wait))})")
        return wait

    if status == "error":
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
        # Resposta terminou sem fechar a unidade: retoma a mesma sessão, até duas vezes.
        s["early_ends"] += 1
        if s["early_ends"] >= 3:
            log(f"{project['name']}: a unidade não fecha; recomeçando do ledger em sessão nova")
            s["session"], s["early_ends"] = None, 0
        else:
            log(f"{project['name']}: resposta terminou sem last-unit.json; retomando")
        return 5

    # Mede a sessão inteira só quando a unidade fecha, para não contar duas vezes uma retomada.
    cost = unit_cost(s["session"])
    if cost:
        for key in ("primary", "secondary"):
            s["cost"][key] = round((1 - EMA_ALPHA) * s["cost"][key] + EMA_ALPHA * cost[key], 3)
    s.update(session=None, interrupted=False, early_ends=0)
    unit_status = result.get("status", "worked")
    summary = result.get("summary", "")
    log(f"{project['name']}: {unit_status} — {result.get('unit', '?')}")
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


def pick(cfg, state, limits):
    """Escolhe o próximo projeto. Devolve (projeto, None) ou (None, segundos até valer checar)."""
    d = cfg["defaults"]
    sec_used, sec_reset = window(limits, "secondary")
    now = time.time()
    waits = []

    # Unidade interrompida vai primeiro, qualquer que seja a prioridade: nada fica pela metade.
    for p in cfg["project"]:
        if state[p["name"]]["interrupted"]:
            return p, None

    for p in cfg["project"]:
        s = state[p["name"]]
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
        if is_dirty(p["path"]):
            if not s["dirty_logged"]:
                log(f"{p['name']}: árvore com alterações que não são do autopilot; pulando até ficar limpa")
                s["dirty_logged"] = True
            waits.append(600)
            continue
        s["dirty_logged"] = False
        if sec_used + d["safety_factor"] * s["cost"]["secondary"] > p["weekly_cap"]:
            if sec_reset:
                waits.append(sec_reset - now + 120)
            continue
        return p, None
    return None, (min(waits) if waits else 3600)


def cmd_run(args):
    cfg = load_config()
    state = load_state(cfg)
    codex = find_codex()
    d = cfg["defaults"]

    def on_signal(*_):
        save_state(state)
        log("interrompido; rode de novo para retomar")
        sys.exit(130)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    log(f"autopilot iniciado (codex={codex}, orquestrador={d['orchestrator_model']}/{d['orchestrator_effort']})")

    while True:
        process_decisions(cfg, state)
        limits = current_limits()
        project, wait = pick(cfg, state, limits)
        save_state(state)
        if project is None:
            if all(state[p["name"]]["status"] == "idle" for p in cfg["project"]):
                log("todos os projetos idle; fim")
                ntfy(cfg, "autopilot: fim", "Todos os projetos terminaram o backlog.", tags=["checkered_flag"])
                return
            log(f"nada elegível agora; próxima checagem em {int(wait // 60)} min")
            nap(cfg, state, max(wait, 60))
            continue

        s = state[project["name"]]
        pri_used, pri_reset = window(limits, "primary")
        if pri_used + d["safety_factor"] * s["cost"]["primary"] > d["primary_cap"] and not s["interrupted"]:
            wait = (pri_reset - time.time() + 120) if pri_reset else 1800
            log(f"janela de 5 h em {pri_used:.0f}%; esperando o reset ({int(wait // 60)} min)")
            nap(cfg, state, max(wait, 60))
            continue

        docker = prepare_docker(cfg, project)
        if docker == "busy":
            nap(cfg, state, 600)
            continue
        if docker != "ok":
            # Sem os containers a unidade só gastaria cota; avisa uma vez e tenta de novo em 10 min.
            if not state["_notify"].get("docker_alerted"):
                ntfy(cfg, "autopilot: Docker com problema",
                     f"Não consegui subir os containers de {project['name']} ({docker}). Veja o autopilot.log.",
                     priority=4, tags=["warning"])
                state["_notify"]["docker_alerted"] = True
            nap(cfg, state, 600)
            continue
        state["_notify"]["docker_alerted"] = False
        out, err, code, unit_file = run_unit(cfg, project, s)
        wait = after_unit(cfg, project, s, out, err, code, unit_file)
        save_state(state)
        if wait > 60:
            log(f"aguardando até {time.strftime('%F %T', time.localtime(time.time() + wait))}")
        nap(cfg, state, wait)


def cmd_status(args):
    cfg = load_config()
    state = load_state(cfg)
    limits = current_limits()
    for key, label in (("primary", "5 h"), ("secondary", "semanal")):
        used, reset = window(limits, key)
        when = time.strftime("%F %T", time.localtime(reset)) if reset else "-"
        print(f"janela {label:8} {used:5.1f}%   reset {when}")
    print()
    for p in cfg["project"]:
        s = state[p["name"]]
        flags = []
        if s["interrupted"]:
            flags.append("unidade interrompida")
        if s["session"]:
            flags.append(f"sessão {s['session']}")
        if is_dirty(p["path"]):
            flags.append("árvore suja")
        print(f"{p['name']:10} {s['status']:8} teto {p['weekly_cap']:>3}%  "
              f"custo/unidade {s['cost']['primary']:.1f}% (5 h) {s['cost']['secondary']:.2f}% (semana)  "
              f"{', '.join(flags)}")
    pending = sum(1 for p in cfg["project"] for d in map(read_json, decision_files(p)) if d and "answer" not in d)
    if pending:
        print(f"\n{pending} decisão(ões) pendente(s): autopilot.py decisions")


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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").set_defaults(func=cmd_run)
    sub.add_parser("status").set_defaults(func=cmd_status)
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
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception:
        # Sem isto o traceback some junto com a janela do tmux.
        import traceback
        log("erro inesperado; o loop parou:\n" + traceback.format_exc())
        if args.cmd == "run":
            ntfy(load_config(), "autopilot: parou", "Erro inesperado. Veja o fim do autopilot.log.",
                 priority=5, tags=["rotating_light"])
        raise


if __name__ == "__main__":
    main()
