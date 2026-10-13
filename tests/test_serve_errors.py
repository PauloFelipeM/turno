import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers


class ServeErrorTest(unittest.TestCase):
    def test_serve_error_propagates_without_touching_the_codex_log(self):
        ap = helpers.load(Path(tempfile.mkdtemp()))
        def boom(args):
            raise RuntimeError("x")
        with mock.patch.object(sys, "argv", ["autopilot.py", "serve"]), \
                mock.patch.object(ap, "cmd_serve", boom), mock.patch.object(ap, "ntfy") as ntfy:
            with self.assertRaises(RuntimeError):
                ap.main()
        self.assertFalse(ap.LOG.exists())
        ntfy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
