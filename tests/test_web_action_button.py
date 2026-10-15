"""ActionButton must keep its action when a wrapper (a TooltipTrigger with asChild) passes its own onClick.

The wrapper's handler arrives in the props at runtime. When the props were spread after onClick, that
handler replaced the action and the pause, resume, next and interrupt icons did nothing.
"""
import re
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "ui" / "src" / "components" / "ActionButton.tsx"


class ActionButtonClick(unittest.TestCase):
    def test_the_props_are_spread_before_the_click_handler(self):
        text = SRC.read_text()
        button = text[text.index("<Button"):text.index("/>", text.index("<Button"))]
        self.assertIn("{...props}", button)
        self.assertLess(button.index("{...props}"), button.index("onClick="))

    def test_the_wrapper_click_is_composed_not_dropped(self):
        text = SRC.read_text()
        self.assertRegex(text, r"onClick:\s*wrapperClick")
        self.assertRegex(text, re.compile(r"wrapperClick\?\.\(ev\)"))


if __name__ == "__main__":
    unittest.main()
