"""The PyPI version check: parsing, at most once a day, off by config, silent on network failure. Never the real network."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers


def answer(version):
    return json.dumps({"info": {"version": version}, "releases": {}}).encode()


class PypiTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        helpers.load(self.home)
        from turno import pypi
        self.pypi = pypi
        self.fetched = []
        self.reply = answer("99.0.0")

        def fetch(url):
            self.fetched.append(url)
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply

        patch = mock.patch.object(pypi, "_fetch", fetch)
        patch.start()
        self.addCleanup(patch.stop)
        helpers.write_config(self.home)

    def test_parses_the_answer(self):
        self.assertEqual(self.pypi.latest_from(answer("1.2.3")), "1.2.3")
        self.assertIsNone(self.pypi.latest_from(answer("1.2.3rc1")))
        self.assertIsNone(self.pypi.latest_from(b'{"info": {}}'))
        self.assertIsNone(self.pypi.latest_from(b"[]"))
        with self.assertRaises(ValueError):
            self.pypi.latest_from(b"not json")

    def test_compares_versions_as_numbers(self):
        self.assertTrue(self.pypi.newer("0.10.0", "0.9.9"))
        self.assertFalse(self.pypi.newer("0.1.0", "0.1.0"))
        self.assertFalse(self.pypi.newer("0.0.9", "0.1.0"))
        self.assertFalse(self.pypi.newer(None, "0.1.0"))
        self.assertFalse(self.pypi.newer("1.0.0b1", "0.1.0"))

    def test_asks_at_most_once_a_day(self):
        self.assertEqual(self.pypi.check(self.home, now=1000)["latest"], "99.0.0")
        self.pypi.check(self.home, now=1000 + 3600)
        self.assertEqual(self.fetched, [self.pypi.URL])
        self.pypi.check(self.home, now=1000 + 24 * 3600)
        self.assertEqual(len(self.fetched), 2)

    def test_the_notice_only_reads_the_cache(self):
        self.assertEqual(self.pypi.notice(self.home), {"current": self.pypi.__version__, "latest": None, "newer": False})
        self.pypi.check(self.home, now=1000)
        self.assertEqual(self.pypi.notice(self.home), {"current": self.pypi.__version__, "latest": "99.0.0", "newer": True})
        self.assertEqual(len(self.fetched), 1)

    def test_same_version_is_no_notice(self):
        self.reply = answer(self.pypi.__version__)
        self.pypi.check(self.home, now=1000)
        self.assertFalse(self.pypi.notice(self.home)["newer"])

    def test_off_by_config(self):
        helpers.write_config(self.home, edit=lambda d: d.update(update_check=False))
        self.assertEqual(self.pypi.check(self.home, now=1000), {})
        self.assertEqual(self.fetched, [])
        (self.home / "shared").mkdir(exist_ok=True)
        (self.home / "shared" / "update-check.json").write_text(json.dumps({"checked_at": 1, "latest": "99.0.0"}))
        self.assertFalse(self.pypi.notice(self.home)["newer"])

    def test_network_failure_is_silent_and_waits_a_day(self):
        self.reply = OSError("offline")
        self.assertIsNone(self.pypi.check(self.home, now=1000)["latest"])
        self.pypi.check(self.home, now=2000)
        self.assertEqual(len(self.fetched), 1)
        self.reply = ValueError("bad json")
        self.pypi.check(self.home, now=1000 + 24 * 3600)
        self.assertFalse(self.pypi.notice(self.home)["newer"])

    def test_a_failure_keeps_the_version_already_known(self):
        self.pypi.check(self.home, now=1000)
        self.reply = OSError("offline")
        self.pypi.check(self.home, now=1000 + 24 * 3600)
        self.assertEqual(self.pypi.notice(self.home)["latest"], "99.0.0")

    def test_a_corrupt_cache_counts_as_never_checked(self):
        (self.home / "shared").mkdir(exist_ok=True)
        (self.home / "shared" / "update-check.json").write_text("[1, 2]")
        self.assertEqual(self.pypi.check(self.home, now=1000)["latest"], "99.0.0")

    def test_the_overview_carries_the_version_and_the_notice(self):
        from turno import panel
        self.pypi.check(self.home, now=1000)
        ov = panel.overview(self.home, {})
        self.assertEqual(ov["version"], self.pypi.__version__)
        self.assertEqual(ov["update"]["latest"], "99.0.0")


if __name__ == "__main__":
    unittest.main()
