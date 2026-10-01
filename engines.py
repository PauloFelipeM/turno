"""Motores do autopilot: como cada CLI roda, lê a cota e diz como uma execução terminou."""

import datetime
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path


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


def make_engine(cfg):
    kind = (cfg.get("engine") or {}).get("kind", "codex")
    engines = {"codex": CodexEngine}
    if kind not in engines:
        sys.exit(f"motor desconhecido em [engine] kind: {kind}")
    return engines[kind](cfg)
