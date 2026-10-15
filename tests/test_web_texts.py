"""The panel's texts and the rules its source must keep (plain text checks over ui/src; node, when present,
runs the window-format helpers). The panel is React + TypeScript in ui/, built into turno/web."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

OLD_ROLE = "plan" + "-reviewer"  # the removed role, spelled so a grep for it stays clean

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"
SRC = UI / "src"
I18N = SRC / "i18n"


def source(*, include_ui_kit=True):
    """{relative path: text} of every .ts/.tsx file in ui/src."""
    out = {}
    for path in sorted(SRC.rglob("*.ts*")):
        rel = path.relative_to(SRC).as_posix()
        if not include_ui_kit and rel.startswith("components/ui/"):
            continue
        out[rel] = path.read_text(encoding="utf-8")
    return out


def code_lines(text):
    return [line for line in text.splitlines() if not line.lstrip().startswith(("//", "*", "/*"))]


SPACE = re.compile(r"(?:\s+|//[^\n]*)*")
STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
NAME = re.compile(r"[A-Za-z_$][\w$]*")


def js_object(text):
    """The object literal after `export default` as a dict (string values and nested objects only)."""
    def skip(p):
        return SPACE.match(text, p).end()

    def string(p):
        m = STRING.match(text, p)
        if not m:
            raise ValueError(f"string expected at {p}")
        return json.loads(f'"{m.group(1)}"'), m.end()

    def obj(p):
        if text[p] != "{":
            raise ValueError(f"object expected at {p}")
        out, p = {}, skip(p + 1)
        while text[p] != "}":
            if text[p] == '"':
                key, p = string(p)
            else:
                m = NAME.match(text, p)
                if not m:
                    raise ValueError(f"key expected at {p}")
                key, p = m.group(0), m.end()
            p = skip(p)
            if text[p] != ":":
                raise ValueError(f"':' expected at {p}")
            p = skip(p + 1)
            out[key], p = obj(p) if text[p] == "{" else string(p)
            p = skip(p)
            if text[p] == ",":
                p = skip(p + 1)
        return out, p + 1

    return obj(skip(text.index("export default") + len("export default")))[0]


def leaves(d, prefix=""):
    """{"a.b": "text", ...} for every string in a nested dict."""
    out = {}
    for k, v in d.items():
        out.update(leaves(v, f"{prefix}{k}.") if isinstance(v, dict) else {f"{prefix}{k}": v})
    return out


def bundle(code):
    return js_object((I18N / f"{code}.ts").read_text(encoding="utf-8"))


class TextsTest(unittest.TestCase):
    def setUp(self):
        self.pt = bundle("pt-BR")
        self.en = bundle("en")
        self.src = source()
        self.app = "\n".join(source(include_ui_kit=False).values())

    def test_english_has_exactly_the_keys_and_fields_of_portuguese(self):
        pt, en = leaves(self.pt), leaves(self.en)
        self.assertEqual(set(en), set(pt))
        for key, text in pt.items():
            self.assertEqual(set(re.findall(r"\{(\w+)\}", en[key])), set(re.findall(r"\{(\w+)\}", text)), key)

    def test_every_language_of_the_config_has_a_bundle_and_a_name_and_english_is_the_fallback(self):
        from turno import config
        index = (I18N / "index.tsx").read_text(encoding="utf-8")
        self.assertIn(f"export const LANGS = {json.dumps(list(config.LANGUAGES))};", index)
        self.assertIn('export const DEFAULT_LANG = "en";', index)
        self.assertEqual(config.DEFAULT_LANGUAGE, "en")
        for code in config.LANGUAGES:
            names = leaves(bundle(code))
            for other in config.LANGUAGES:
                self.assertIn(f"settings.languages.{other}", names, (code, other))

    def test_every_error_code_the_server_returns_has_a_text(self):
        codes = set(re.findall(r'ApiError\(\d+, "(\w+)"', (ROOT / "turno" / "control.py").read_text()))
        codes |= set(re.findall(r'\{"error": "(\w+)"', (ROOT / "turno" / "server.py").read_text()))
        self.assertEqual(codes, {"bad_option", "bad_request", "not_found", "already_running", "busy", "not_running",
                                 "paused", "restart_needed", "forbidden", "internal", "exists", "in_use", "disabled",
                                 "config", "bad_path", "setup_open", "prepared", "not_git", "wrong_phase"})
        for lang in (self.pt, self.en):
            for code in codes:
                self.assertTrue(lang["errors"].get(code), code)
        self.assertEqual(self.pt["errors"]["bad_path"], "A pasta não existe ou não é uma pasta.")
        self.assertEqual(self.pt["errors"]["restart_needed"], "Este loop precisa ser reiniciado para aceitar a interrupção.")
        self.assertIn("`errors.${e.code}`", self.src["i18n/index.tsx"])

    def test_every_loop_phase_and_outcome_has_a_text(self):
        phases = set(re.findall(r'set_phase\(state, "(\w+)"', (ROOT / "turno" / "autopilot.py").read_text()))
        phases |= {"sleeping", "outside_window", "stopped", "unknown"}  # idle_phase, and the panel's own two
        for lang in (self.pt, self.en):
            self.assertLessEqual(phases, set(lang["phase"]))
            self.assertIn("stopped", lang["outcome"])
        self.assertEqual(self.pt["outcome"]["stopped"], "interrompida; retoma depois")

    def test_every_literal_key_the_components_use_exists(self):
        known = leaves(self.en)
        groups = {k.rsplit(".", 1)[0] for k in known if "." in k}
        used = set(re.findall(r'\bt\(\s*"([\w.-]+)"', self.app))
        used |= set(re.findall(r'\btextIn\([^,]+,\s*"([\w.-]+)"', self.app))
        self.assertTrue(used)
        self.assertFalse(sorted(k for k in used if k not in known), "keys used but missing from the dictionaries")
        prefixes = set(re.findall(r"\bt\(\s*`([\w.]+)\.\$\{", self.app))
        self.assertFalse(sorted(p for p in prefixes if p not in groups), "key groups used but missing")

    def test_no_hard_coded_text_in_the_components(self):
        jsx_text = re.compile(r"(?<![=-])>([^<>{}();=]*[A-Za-zÀ-ÿ][^<>{}();=]*)<")
        attr_text = re.compile(r'\b(placeholder|title|aria-label|alt|label|description|confirm)="([^"]*[A-Za-zÀ-ÿ][^"]*)"')
        found = []
        for rel, text in source(include_ui_kit=False).items():
            if not rel.endswith(".tsx"):
                continue
            found += [(rel, m.group(1).strip()) for m in jsx_text.finditer(text)]
            found += [(rel, m.group(2)) for m in attr_text.finditer(text)]
        self.assertEqual(found, [], "visible text must go through t()")

    def test_owner_facing_texts_kept_from_the_old_panel(self):
        self.assertEqual(self.pt["engineStarting"], "Pedido enviado; o loop do {engine} está iniciando.")
        self.assertEqual(self.pt["engineStopping"], "Pedido enviado; o loop do {engine} está parando; pode levar até 1 min.")
        self.assertEqual(self.pt["retry"], "tenta de novo {when}")
        self.assertEqual(self.pt["project"]["retryNow"], "Tentar agora")
        self.assertEqual(self.pt["footer"]["restart"], "Reiniciar turno")
        setup = self.pt["setup"]
        self.assertEqual((setup["prepare"], setup["request"], setup["approve"], setup["changes"], setup["cancel"]),
                         ("Preparar com um agente", "Pedir preparo", "Aprovar", "Pedir mudanças", "Cancelar preparo"))
        self.assertEqual(setup["chip"], {"requested": "preparando", "approved": "preparando", "review": "rascunho pronto"})
        settings = self.pt["settings"]
        self.assertEqual(settings["roles"], {"orchestrator": "Orquestrador", "planner": "Planejador",
                                             "implementer": "Implementador", "reviewer": "Revisor"})
        self.assertEqual(set(settings["days"]), {"mon", "tue", "wed", "thu", "fri", "sat", "sun"})


class SourceRulesTest(unittest.TestCase):
    def setUp(self):
        self.src = source()
        self.app = "\n".join(source(include_ui_kit=False).values())

    def test_writes_carry_the_guard_header_and_json(self):
        api = self.src["api.ts"]
        self.assertIn('headers: { "Content-Type": "application/json", "X-Turno": "1" }', api)
        self.assertEqual(self.app.count("fetch("), api.count("fetch("), "every request goes through api.ts")

    def test_no_raw_html_and_no_external_request(self):
        for rel, text in self.src.items():
            self.assertNotIn("dangerouslySetInnerHTML", text, rel)
            self.assertNotIn("innerHTML", text, rel)
            if not rel.startswith("i18n/"):  # the texts may name a URL; the code never requests one
                self.assertIsNone(re.search(r"https?://", "\n".join(code_lines(text))), rel)
        html = (UI / "index.html").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"https?://", html))

    def test_the_secret_ntfy_value_never_appears_in_the_page(self):
        for rel, text in self.src.items():
            if rel.startswith("i18n/"):
                continue  # settings.topicHint and the no-topic warning only talk about it
            self.assertFalse([line for line in code_lines(text) if re.search(r"\btopic\b", line)], rel)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_window_text_round_trip(self):
        from turno import schedule
        script = (
            "import { parsePoint, formatPoint } from './src/lib/schedule.ts';"
            "const specs = ['fri 19:00', 'sun 03:00', ' MON 7:05 ', 'sat 23:59', 'fri 24:00', 'fri 7:60', 'friday 19:00', '', 'fri'];"
            "const out = specs.map((s) => { const p = parsePoint(s); return [s, p, p && formatPoint(p)]; });"
            "out.push(['empty time', null, formatPoint({ day: 'tue', time: '' })]);"
            "console.log(JSON.stringify(out));"
        )
        r = subprocess.run(["node", "--input-type=module", "-e", script], cwd=UI, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        rows = json.loads(r.stdout)
        for spec, point, text in rows[:-1]:
            try:
                minutes = schedule.parse(spec)
            except ValueError:
                minutes = None
            if minutes is None:
                self.assertIsNone(point, spec)  # the pickers refuse what the loop would refuse
            else:
                self.assertEqual(schedule.parse(text), minutes, spec)  # same moment, normalised as "fri 19:00"
                self.assertRegex(text, r"^(mon|tue|wed|thu|fri|sat|sun) \d\d:\d\d$")
        self.assertEqual(rows[0][2], "fri 19:00")
        self.assertEqual(rows[2][2], "mon 07:05")
        self.assertIsNone(rows[-1][2])


class ScreensTest(unittest.TestCase):
    """What each screen must keep from the old panel."""

    def setUp(self):
        self.src = source()
        self.app = "\n".join(source(include_ui_kit=False).values())

    def test_the_theme_is_remembered_and_follows_the_system(self):
        theme = self.src["theme.tsx"]
        self.assertIn('const KEY = "turno.theme";', theme)
        self.assertIn('matchMedia("(prefers-color-scheme: dark)")', theme)
        self.assertIn('classList.toggle("dark"', theme)

    def test_a_retry_wait_stays_on_the_card_out_of_the_needs_you_strip(self):
        strip = self.src["components/NeedsYou.tsx"]
        items = strip[strip.index("export function attentionItems"):strip.index("function DecisionItem")]
        self.assertIn('p.setup === "review"', items)
        self.assertNotIn("retry_at", items)
        self.assertIn('t("retry", {', self.src["components/Projects.tsx"])


if __name__ == "__main__":
    unittest.main()
