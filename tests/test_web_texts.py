"""The interface texts the control fixes depend on (plain text checks: there is no JS runtime in the tests)."""

import re
import unittest
from pathlib import Path

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

    def test_every_error_code_the_server_returns_has_a_text(self):
        root = WEB.parent
        codes = set(re.findall(r'ApiError\(\d+, "(\w+)"', (root / "control.py").read_text()))
        codes |= set(re.findall(r'\{"error": "(\w+)"', (root / "server.py").read_text()))
        self.assertEqual(codes, {"bad_option", "bad_request", "not_found", "already_running", "busy", "not_running",
                                 "paused", "restart_needed", "forbidden", "internal", "exists", "in_use", "disabled",
                                 "config", "bad_path"})
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


if __name__ == "__main__":
    unittest.main()
