"""The interface texts the control fixes depend on (plain text checks: there is no JS runtime in the tests)."""

import json
import re
import unittest
from pathlib import Path

OLD_ROLE = "plan" + "-reviewer"  # the removed role, spelled so a grep for it stays clean

WEB = Path(__file__).resolve().parents[1] / "web"


class WebTextsTest(unittest.TestCase):
    def setUp(self):
        self.i18n = (WEB / "i18n" / "pt-BR.js").read_text(encoding="utf-8")
        self.app = (WEB / "app.js").read_text(encoding="utf-8")
        self.css = (WEB / "style.css").read_text(encoding="utf-8")

    def block(self, name):
        return re.search(rf"\n  {name}: \{{\n(.*?)\n  \}},", self.i18n, re.S).group(1)

    def test_stopped_outcome_has_a_text_and_a_colour(self):
        self.assertIn('stopped: "interrompida; retoma depois"', self.block("outcome"))
        rule = next(line for line in self.css.splitlines() if ".oc-cut" in line)
        self.assertIn(".oc-stopped", rule)
        self.assertIn("var(--warn)", rule)

    def test_engine_start_and_stop_have_their_own_toasts(self):
        self.assertIn('engineStarting: "Pedido enviado; o loop do {engine} está iniciando."', self.i18n)
        self.assertIn('engineStopping: "Pedido enviado; o loop do {engine} está parando; pode levar até 1 min."', self.i18n)
        self.assertNotIn("engineRequested", self.i18n + self.app)
        self.assertIn('t("engineStarting"', self.app)
        self.assertIn('t("engineStopping"', self.app)

    def test_restart_needed_error_has_a_text_the_interface_uses(self):
        self.assertIn("restart_needed:", self.block("errors"))
        self.assertIn("errors.${", self.app)

    def test_setup_texts_and_calls(self):
        block = self.block("setup")
        for text in ('prepare: "Preparar com um agente"', 'goalLabel: "O que este projeto deve virar"',
                     'request: "Pedir preparo"', 'approve: "Aprovar"', 'changes: "Pedir mudanças"',
                     'cancel: "Cancelar preparo"', 'chip: { requested: "preparando", approved: "preparando", review: "rascunho pronto" }'):
            self.assertIn(text, block)
        for call in ("/setup`, { goal:", "/setup/approve`", "/setup/changes`", '/setup`, undefined, "DELETE"'):
            self.assertIn(call, self.app)
        strip = self.app[self.app.index("function attentionItems"):self.app.index("function decisionItem")]
        self.assertIn('p.setup === "review"', strip)
        self.assertIn('t("setup.prepare")', self.app)
        self.assertIn(".draft", self.css)

    def test_every_error_code_the_server_returns_has_a_text(self):
        root = WEB.parent
        codes = set(re.findall(r'ApiError\(\d+, "(\w+)"', (root / "control.py").read_text()))
        codes |= set(re.findall(r'\{"error": "(\w+)"', (root / "server.py").read_text()))
        self.assertEqual(codes, {"bad_option", "bad_request", "not_found", "already_running", "busy", "not_running",
                                 "paused", "restart_needed", "forbidden", "internal", "exists", "in_use", "disabled",
                                 "config", "bad_path", "setup_open", "prepared", "not_git", "wrong_phase"})
        block = self.block("errors")
        for code in codes:
            self.assertRegex(block, rf'\n?\s*{code}: "[^"]+",', code)
        self.assertIn('bad_path: "A pasta não existe ou não é uma pasta."', block)
        self.assertIn('restart_needed: "Este loop precisa ser reiniciado para aceitar a interrupção."', block)

    def test_retry_wait_shows_on_the_card_and_can_be_cleared_from_the_project_panel(self):
        self.assertIn('retry: "tenta de novo {when}"', self.i18n)
        self.assertIn('retryNow: "Tentar agora"', self.i18n)
        self.assertIn('t("retry", {', self.app)
        self.assertIn('t("project.retryNow")', self.app)
        self.assertIn("retry_at", self.app)
        strip = self.app[self.app.index("function attentionItems"):self.app.index("function decisionItem")]
        self.assertNotIn("retry_at", strip)  # a waiting project stays on its card, out of the strip

    def test_settings_edit_a_model_and_effort_per_role(self):
        settings = self.block("settings")
        for text in ('role: "Papel"', 'orchestrator: "Orquestrador"', 'planner: "Planejador"',
                     'implementer: "Implementador"', 'reviewer: "Revisor"'):
            self.assertIn(text, settings)
        self.assertNotIn(OLD_ROLE, self.i18n + self.app)
        self.assertIn('const ROLES = ["planner", "implementer", "reviewer"]', self.app)
        self.assertIn('"xhigh"', self.app)
        self.assertIn("known_models", self.app)
        self.assertIn('h("datalist"', self.app)
        self.assertIn("body.roles = roles", self.app)  # only what changed is sent
        self.assertNotIn("innerHTML", self.app.replace("// Builds elements without innerHTML", ""))


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


class LanguagesTest(unittest.TestCase):
    def bundle(self, code):
        return leaves(js_object((WEB / "i18n" / f"{code}.js").read_text(encoding="utf-8")))

    def test_english_has_exactly_the_keys_and_fields_of_portuguese(self):
        pt, en = self.bundle("pt-BR"), self.bundle("en")
        self.assertEqual(set(en), set(pt))
        for key, text in pt.items():
            self.assertEqual(set(re.findall(r"\{(\w+)\}", en[key])), set(re.findall(r"\{(\w+)\}", text)), key)

    def test_every_language_of_the_config_has_a_bundle_and_a_name(self):
        import config
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn(f"const LANGS = {json.dumps(list(config.LANGUAGES))};", app)
        for code in config.LANGUAGES:
            names = self.bundle(code)
            for other in config.LANGUAGES:
                self.assertIn(f"settings.languages.{other}", names, (code, other))

    def test_the_page_loads_the_configured_language(self):
        app = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('import T from', app)  # no fixed bundle
        self.assertIn("await import(`./i18n/${code}.js`)", app)
        self.assertIn('LANGS.includes(lang) ? lang : "pt-BR"', app)  # unknown language: pt-BR
        self.assertNotIn('toLocaleString("pt-BR"', app)  # dates and numbers follow the language
        self.assertIn('"/api/settings/general"', app)
        self.assertIn('tab("general", t("settings.general"))', app)
        self.assertIn("settings.warnings.${w}", app)

    def test_the_topic_never_appears_in_the_page(self):
        code = [line for line in (WEB / "app.js").read_text(encoding="utf-8").splitlines()
                if not line.lstrip().startswith("//")]
        self.assertFalse([line for line in code if re.search(r"\btopic\b", line)])  # settings.topicHint is only a text


if __name__ == "__main__":
    unittest.main()
