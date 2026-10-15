"""The built panel (turno/web: index.html and hashed assets/) as the server hands it out, and the page shown
when the panel is not built. The tests use a fake build in a temporary folder, never `npm`."""

import http.client
import io
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import helpers

FILES = {
    "index.html": b'<!doctype html><script type="module" src="/static/assets/index-AbC123.js"></script>',
    "assets/index-AbC123.js": b"console.log(1)",
    "assets/index-AbC123.css": b"body{}",
    "assets/inter-latin-wght-normal-X1.woff2": b"wOF2",
    "assets/logo-Z9.svg": b"<svg/>",
    "assets/icon-Q7.png": b"\x89PNG",
}
TYPES = {"index.html": "text/html", ".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2",
         ".svg": "image/svg+xml", ".png": "image/png"}


class StaticTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        helpers.load(self.home)
        from turno import server
        self.server = server
        helpers.make_home(self.home)
        self.web = Path(tempfile.mkdtemp()) / "web"
        for rel, data in FILES.items():
            (self.web / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.web / rel).write_bytes(data)
        (self.web.parent / "secret.txt").write_text("outside")
        patch = mock.patch.object(server, "WEB", self.web)
        patch.start()
        self.addCleanup(patch.stop)
        hub = server.Hub(self.home, {"codex": self.home / "cx", "claude": self.home / "cl"}, interval=0.1)
        self.srv = server.Server(hub, 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path, headers={"Host": f"127.0.0.1:{self.port}"})
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, r.getheader("Content-Type"), r.getheader("Cache-Control"), body

    def test_the_page_is_served_at_the_root_and_never_cached(self):
        status, ctype, cache, body = self.get("/")
        self.assertEqual((status, cache, body), (200, "no-cache", FILES["index.html"]))
        self.assertTrue(ctype.startswith("text/html"))

    def test_assets_have_their_types_and_a_long_cache(self):
        for rel, data in FILES.items():
            if not rel.startswith("assets/"):
                continue
            status, ctype, cache, body = self.get(f"/static/{rel}")
            self.assertEqual((status, body), (200, data), rel)
            self.assertTrue(ctype.startswith(TYPES[Path(rel).suffix]), (rel, ctype))
            self.assertEqual(cache, "public, max-age=31536000, immutable", rel)

    def test_the_api_is_still_never_cached(self):
        status, _, cache, _ = self.get("/api/overview")
        self.assertEqual((status, cache), (200, "no-store"))

    def test_nothing_outside_the_web_folder_is_served(self):
        for path in ("/static/../secret.txt", "/static/%2e%2e/secret.txt", "/static/assets/../../secret.txt",
                     "/static/assets/%2e%2e/%2e%2e/secret.txt", "/static/assets", "/static/nope.js"):
            self.assertEqual(self.get(path)[0], 404, path)

    def test_a_symlink_out_of_the_web_folder_is_refused(self):
        (self.web / "assets" / "link.js").symlink_to(self.web.parent / "secret.txt")
        self.assertEqual(self.get("/static/assets/link.js")[0], 404)

    def test_not_built_answers_with_the_build_command_and_the_api_still_works(self):
        for rel in FILES:
            (self.web / rel).unlink()
        status, ctype, _, body = self.get("/")
        self.assertEqual(status, 503)
        self.assertTrue(ctype.startswith("text/html"))
        self.assertIn(b"npm --prefix ui ci &amp;&amp; npm --prefix ui run build", body)
        self.assertEqual(self.get("/api/overview")[0], 200)
        self.assertEqual(self.get("/static/assets/index-AbC123.js")[0], 404)

    def test_no_web_folder_at_all_is_the_same_page(self):
        with mock.patch.object(self.server, "WEB", self.web.parent / "missing"):
            self.assertEqual(self.get("/")[0], 503)

    def test_serve_says_so_when_the_panel_is_not_built(self):
        out = io.StringIO()
        with mock.patch.object(self.server, "WEB", self.web.parent / "missing"), \
                mock.patch.object(self.server.Server, "serve_forever", side_effect=KeyboardInterrupt), \
                mock.patch.object(self.server.pypi, "watch"), redirect_stdout(out):
            self.server.serve(self.home, 0)
        self.assertIn("The panel is not built: run `npm --prefix ui ci && npm --prefix ui run build`", out.getvalue())


if __name__ == "__main__":
    unittest.main()
