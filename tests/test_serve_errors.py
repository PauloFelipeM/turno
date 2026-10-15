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

    def test_a_busy_port_exits_with_a_message_and_no_traceback(self):
        import socket
        helpers.load(Path(tempfile.mkdtemp()))
        from turno import server
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            port = busy.getsockname()[1]
            with self.assertRaises(SystemExit) as done:
                server.serve(Path(tempfile.mkdtemp()), port)
        self.assertIn(f"port {port} is in use", str(done.exception.code))
        self.assertIn("service", str(done.exception.code))  # a hand-started serve vs the installed service

    def test_any_other_bind_error_is_a_message_too(self):
        helpers.load(Path(tempfile.mkdtemp()))
        from turno import server
        with mock.patch.object(server, "Server", side_effect=PermissionError(13, "denied")):
            with self.assertRaises(SystemExit) as done:
                server.serve(Path(tempfile.mkdtemp()), 80)
        self.assertIn("cannot listen on port 80", str(done.exception.code))


if __name__ == "__main__":
    unittest.main()
