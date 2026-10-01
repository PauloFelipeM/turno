"""Motores do autopilot: como cada CLI roda, lê a cota e diz como uma execução terminou."""

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import schedule


def log(msg):
    """Trocada pelo log do autopilot.py ao importar."""
    print(msg, flush=True)


# --- genéricos ---------------------------------------------------------------------------------


def window(limits, key):
    """(uso em %, resets_at) de uma janela; janela já resetada conta como 0%."""
    w = (limits or {}).get(key) or {}
    used, resets = float(w.get("used_percent") or 0), int(w.get("resets_at") or 0)
    if resets and resets <= time.time():
        return 0.0, 0
    return used, resets


def parse_events(lines):
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def read_events(path):
    try:
        with open(path, errors="replace") as f:
            return parse_events(f)
    except OSError:
        return []


def read_text(path):
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return ""


def failure_text(out, err):
    """Eventos de falha do JSONL mais o stderr, e se houve algum evento de falha."""
    failures = [json.dumps(ev, ensure_ascii=False) for ev in read_events(out)
                if ev.get("type") in ("error", "turn.failed")]
    return "\n".join(failures) + "\n" + read_text(err), bool(failures)


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


# --- Codex -------------------------------------------------------------------------------------


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



def thread_id(out):
    for ev in read_events(out):
        if ev.get("type") == "thread.started":
            return ev.get("thread_id")
    return None


class CodexEngine:
    name = "codex"
    label = "Codex"
    needs_limits = False

    def __init__(self, cfg, home=None):
        self.cfg = cfg
        self.home = home or Path.home() / ".codex"
        self.binary = None

    def _bin(self):
        # Procurado de novo se sumir: a extensão do VS Code troca de pasta quando se atualiza.
        if not self.binary or not Path(self.binary).exists():
            self.binary = find_codex()
        return self.binary

    def describe(self):
        d = self.cfg["defaults"]
        return f"codex={self._bin()}, orquestrador={d['orchestrator_model']}/{d['orchestrator_effort']}"

    def limits(self, state, max_age=None):
        """O snapshot mais recente entre as sessões mais novas (inclui uso interativo seu)."""
        sessions = self.home / "sessions"
        files = sorted(sessions.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files[:10]:
            snaps = snapshots(path)
            if snaps:
                return snaps[-1]
        return {}

    def resumable(self, s):
        return bool(s["session"])

    def begin(self, s, resume):
        pass  # o id da sessão do Codex só aparece na saída

    def command(self, state, project, s, resume, stem):
        d = self.cfg["defaults"]
        flags = ["--json", "-m", d["orchestrator_model"],
                 "-c", f'model_reasoning_effort="{d["orchestrator_effort"]}"',
                 "-c", f"agents.max_concurrent_threads_per_session={d['max_subagents']}",
                 "--enable", "multi_agent",
                 # Docker, rede e instalação de dependências exigem sair do sandbox.
                 "--dangerously-bypass-approvals-and-sandbox"]
        codex = self._bin()
        if resume:
            return [codex, "exec", "resume", *flags, s["session"], "-"], None
        return [codex, "exec", *flags, "-"], None

    def after_run(self, state, s, out, resume):
        if not resume:
            s["session"] = thread_id(out)

    def classify(self, out, err, code):
        """('usage_limit' | 'error' | 'ok', horário do reset ou None, se o limite é duro)."""
        text, failed = failure_text(out, err)
        if re.search(r"usage limit|usage_limit|rate.?limit", text, re.I):
            hard = bool(re.search(r"usage limit|usage_limit", text, re.I))
            limits = self.limits(None)
            resets = [r for used, r in (window(limits, "primary"), window(limits, "secondary")) if used >= 90 and r]
            if not resets and (at := retry_at(text)):
                # Sem snapshot com o reset: vale o horário que a própria mensagem de erro informa.
                resets = [at]
            return "usage_limit", (max(resets) if resets else None), hard
        return ("error" if failed or code != 0 else "ok"), None, False

    def unit_cost(self, state, s):
        """Custo da unidade em pontos percentuais, do primeiro ao último snapshot da sessão."""
        thread = s["session"]
        files = list((self.home / "sessions").rglob(f"rollout-*{thread}*.jsonl")) if thread else []
        snaps = [x for f in files for x in snapshots(f)]
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

    def close_unit(self, s):
        pass


# --- Claude ------------------------------------------------------------------------------------

ROLES = Path(__file__).resolve().parent / "claude-agents.json"
BASE_WINDOWS = {"five_hour", "seven_day"}
LIMIT_TEXT = re.compile(r"usage limit|rate limit|limit reached|out of (extra )?usage", re.I)
FABLE_LIMIT = re.compile(r"fable[^\n]{0,300}?(usage limit|rate limit|limit reached)"
                         r"|(usage limit|rate limit|limit reached)[^\n]{0,300}?fable", re.I)


def find_claude():
    if os.environ.get("CLAUDE_BIN"):
        return os.environ["CLAUDE_BIN"]
    found = shutil.which("claude") or (Path.home() / ".local" / "bin" / "claude")
    if Path(found).exists():
        return str(found)
    sys.exit("claude não encontrado; defina CLAUDE_BIN")


def to_limits(info):
    """rate_limit_info do Claude -> o formato do Codex: primary (5 h), secondary (7 dias), extra."""
    windows = info.get("unifiedWindows") or {}

    def convert(w):
        return {"used_percent": round(float(w.get("utilization") or 0) * 100, 1),
                "resets_at": int(w.get("resetsAt") or 0)}

    out = {}
    if isinstance(windows.get("five_hour"), dict):
        out["primary"] = convert(windows["five_hour"])
    if isinstance(windows.get("seven_day"), dict):
        out["secondary"] = convert(windows["seven_day"])
    extra = {k: convert(v) for k, v in windows.items() if k not in BASE_WINDOWS and isinstance(v, dict)}
    if extra:
        out["extra"] = extra
    return out


def rate_infos(events):
    return [e["rate_limit_info"] for e in events
            if e.get("type") == "rate_limit_event" and isinstance(e.get("rate_limit_info"), dict)]


def tool_result_texts(events):
    """Textos dos resultados de ferramenta (inclui as respostas dos subagentes), não os do orquestrador."""
    for e in events:
        if e.get("type") != "user":
            continue
        for c in (e.get("message") or {}).get("content") or []:
            if not isinstance(c, dict) or c.get("type") != "tool_result":
                continue
            content = c.get("content")
            if isinstance(content, str):
                yield content
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        yield part["text"]


class ClaudeEngine:
    name = "claude"
    label = "Claude"
    needs_limits = True  # sem saber o uso, não dá para respeitar o teto de 90%

    def __init__(self, cfg, home=None, roles=None):
        self.cfg = cfg
        self.home = home or Path.home() / ".claude"
        self.roles = roles or ROLES
        self.binary = None

    def _bin(self):
        if not self.binary:
            self.binary = find_claude()
        return self.binary

    def describe(self):
        d = self.cfg["defaults"]
        return f"claude={self._bin()}, orquestrador={d['orchestrator_model']}/{d['orchestrator_effort']}"

    def _cache(self, state):
        return state.setdefault("_engine", {})

    def limits(self, state, max_age=1800):
        """A última leitura da cota; consulta de novo se ela tiver mais de `max_age` segundos."""
        cache = self._cache(state)
        if time.time() - cache.get("limits_at", 0) > max_age:
            self.probe(state)
        return cache.get("limits") or {}

    def probe(self, state):
        """Consulta mínima ao Haiku, sem plugins nem hooks, só para ler o evento de cota."""
        cmd = [self._bin(), "-p", "ok", "--model", "haiku", "--max-turns", "1", "--safe-mode",
               "--no-session-persistence", "--output-format", "stream-json", "--verbose"]
        try:
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired) as e:
            log(f"consulta de cota do Claude falhou: {e}")
            return
        infos = rate_infos(parse_events(r.stdout.splitlines()))
        if not infos:
            log(f"consulta de cota do Claude sem evento de cota (exit={r.returncode})")
            return
        self.record(state, infos)

    def record(self, state, infos):
        cache = self._cache(state)
        merged = {}
        for info in infos:
            limits = to_limits(info)
            extra = limits.pop("extra", {})
            merged.update(limits)
            merged.setdefault("extra", {}).update(extra)
        cache.update(limits=merged, limits_at=time.time())
        for key, w in merged.get("extra", {}).items():
            # Janela além das gerais = a cota própria do Fable (aparece nas execuções com Fable).
            if w["used_percent"] >= 98 and w["resets_at"] > time.time():
                self._fable_out(state, w["resets_at"], f"janela {key} em {w['used_percent']:.0f}%")

    def _fable_out(self, state, until, why):
        cache = self._cache(state)
        if cache.get("fable_exhausted_until", 0) < until:
            cache["fable_exhausted_until"] = until
            log(f"Fable sem cota até {time.strftime('%F %T', time.localtime(until))} ({why}); "
                f"planner e plan-reviewer passam para o Opus")

    def fable_available(self, state):
        return self._cache(state).get("fable_exhausted_until", 0) <= time.time()

    def resumable(self, s):
        sid = s.get("session")
        return bool(sid) and any((self.home / "projects").glob(f"*/{sid}.jsonl"))

    def begin(self, s, resume):
        if not resume:
            # O id é escolhido aqui e gravado antes de a CLI começar: um corte nunca o perde.
            s["session"] = str(uuid.uuid4())

    def write_agents(self, state, path):
        roles = json.loads(Path(self.roles).read_text())
        fable_ok = self.fable_available(state)
        out = {}
        for name, role in roles.items():
            role = dict(role)
            fallback = role.pop("fallback_model", None)
            if fallback and not fable_ok:
                role["model"] = fallback
            out[name] = role
        Path(path).write_text(json.dumps(out, indent=2, ensure_ascii=False))

    def command(self, state, project, s, resume, stem):
        d = self.cfg["defaults"]
        agents = Path(f"{stem}-agents.json")
        self.write_agents(state, agents)
        cmd = [self._bin(), "-p", "--output-format", "stream-json", "--verbose",
               "--model", d["orchestrator_model"], "--effort", d["orchestrator_effort"],
               "--agents", str(agents), "--dangerously-skip-permissions"]
        cmd += ["--resume", s["session"]] if resume else ["--session-id", s["session"]]
        return cmd, None

    def after_run(self, state, s, out, resume):
        events = read_events(out)
        infos = rate_infos(events)
        if infos:
            self.record(state, infos)
            if s.get("unit_start_limits") is None:
                s["unit_start_limits"] = to_limits(infos[0])
        if any(FABLE_LIMIT.search(t) for t in tool_result_texts(events)):
            extra = (self._cache(state).get("limits") or {}).get("extra") or {}
            until = max((w["resets_at"] for w in extra.values()), default=0)
            if until <= time.time():
                until = schedule.next_occurrence("sun 04:00", datetime.datetime.now()).timestamp()
            self._fable_out(state, until, "mensagem de limite num subagente")

    def classify(self, out, err, code):
        events = read_events(out)
        result = next((e for e in reversed(events) if e.get("type") == "result"), None)
        if result and not result.get("is_error") and code == 0:
            return "ok", None, False  # terminou bem: um evento "rejected" de outro modelo não conta
        rejected = [i for i in rate_infos(events) if i.get("status") == "rejected"]
        text = str((result or {}).get("result") or "") + "\n" + read_text(err)
        if rejected or LIMIT_TEXT.search(text):
            reset = max([int(i.get("resetsAt") or 0) for i in rejected] or [0])
            if not reset and (m := re.search(r"limit reached\|(\d{9,})", text)):
                reset = int(m.group(1))
            if not reset:
                reset = retry_at(text) or 0
            return "usage_limit", (reset or None), True
        return "error", None, False

    def unit_cost(self, state, s):
        """Uso depois da unidade (consulta nova) menos o do início dela, por janela."""
        start = s.get("unit_start_limits")
        self.probe(state)
        end = self._cache(state).get("limits")
        if not start or not end:
            return None
        cost = {}
        for key in ("primary", "secondary"):
            a, b = start.get(key) or {}, end.get(key) or {}
            if not a or not b or a.get("resets_at") != b.get("resets_at"):
                return None
            cost[key] = max(0.0, b["used_percent"] - a["used_percent"])
        return cost

    def close_unit(self, s):
        s["unit_start_limits"] = None


def make_engine(cfg):
    kind = (cfg.get("engine") or {}).get("kind", "codex")
    engines = {"codex": CodexEngine, "claude": ClaudeEngine}
    if kind not in engines:
        sys.exit(f"motor desconhecido em [engine] kind: {kind}")
    return engines[kind](cfg)
