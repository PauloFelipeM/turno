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


if __name__ == "__main__":
    unittest.main()
