import multiprocessing as mp
import os
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
import coord

CTX = mp.get_context("fork")


def _claim_in_child(shared, engine, start, results):
    coord.SHARED = Path(shared)
    start.wait()
    results.put((engine, coord.claim("my-app", engine)))


def _hold_lock(shared, ready, release):
    coord.SHARED = Path(shared)
    handle = coord.try_lock("web")
    assert handle is not None
    ready.set()
    release.wait(10)
    os._exit(0)  # dies without releasing the lock: the OS must release it


class CoordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        coord.SHARED = self.tmp / "shared"

    def test_only_one_engine_wins_a_race(self):
        start, results = CTX.Event(), CTX.Queue()
        procs = [CTX.Process(target=_claim_in_child, args=(str(coord.SHARED), e, start, results))
                 for e in ("codex", "claude")]
        for p in procs:
            p.start()
        start.set()
        for p in procs:
            p.join(10)
        got = dict(results.get(timeout=5) for _ in procs)
        self.assertEqual(sorted(got.values()), [False, True])
        winner = next(e for e, ok in got.items() if ok)
        self.assertEqual(coord.read_claim("my-app")["engine"], winner)

    def test_claim_is_idempotent_for_the_owner(self):
        self.assertTrue(coord.claim("my-api", "codex"))
        self.assertTrue(coord.claim("my-api", "codex"))
        self.assertFalse(coord.claim("my-api", "claude"))
        self.assertTrue(coord.held_by_other("my-api", "claude"))
        self.assertFalse(coord.held_by_other("my-api", "codex"))

    def test_handoff_can_be_taken_by_any_engine(self):
        coord.claim("side-project", "claude")
        coord.hand_off("side-project", "claude")
        self.assertTrue(coord.is_handoff("side-project"))
        self.assertEqual(coord.read_claim("side-project")["handoff_from"], "claude")
        self.assertFalse(coord.held_by_other("side-project", "codex"))
        self.assertTrue(coord.claim("side-project", "codex"))
        self.assertEqual(coord.read_claim("side-project")["engine"], "codex")

    def test_hand_off_does_not_steal_another_engines_claim(self):
        coord.claim("side-project", "codex")
        coord.hand_off("side-project", "claude")
        self.assertEqual(coord.read_claim("side-project")["engine"], "codex")

    def test_release_only_removes_own_claim(self):
        coord.claim("my-app", "codex")
        coord.release("my-app", "claude")
        self.assertIsNotNone(coord.read_claim("my-app"))
        coord.release("my-app", "codex")
        self.assertIsNone(coord.read_claim("my-app"))

    def test_group_lock_excludes_and_frees_on_death(self):
        ready, release = CTX.Event(), CTX.Event()
        child = CTX.Process(target=_hold_lock, args=(str(coord.SHARED), ready, release))
        child.start()
        self.assertTrue(ready.wait(10))
        self.assertIsNone(coord.try_lock("web"))
        self.assertIsNotNone(coord.try_lock("my-api"))
        release.set()
        child.join(10)
        handle = coord.try_lock("web")
        self.assertIsNotNone(handle)
        coord.unlock(handle)

    def test_same_process_second_open_is_also_excluded(self):
        first = coord.try_lock("decisions")
        self.assertIsNone(coord.try_lock("decisions"))
        coord.unlock(first)
        self.assertIsNotNone(coord.try_lock("decisions"))

    def test_group_of(self):
        self.assertEqual(coord.group_of({"name": "my-app", "docker_group": "web"}), "web")
        self.assertEqual(coord.group_of({"name": "my-api"}), "my-api")

    def test_reconcile(self):
        coord.claim("my-app", "codex")       # claim without an open unit: removed
        coord.claim("side-project", "claude")       # open unit, other engine's claim: conflict
        conflicts = coord.reconcile("codex", {"my-app": False, "my-api": True, "side-project": True})
        self.assertIsNone(coord.read_claim("my-app"))
        self.assertEqual(coord.read_claim("my-api")["engine"], "codex")
        self.assertEqual(conflicts, ["side-project"])

    def test_notify_round_trip_and_default(self):
        self.assertEqual(coord.load_notify({"since": "a"}), {"since": "a"})
        coord.save_notify({"since": "b", "since_typed": "c"})
        self.assertEqual(coord.load_notify({"since": "a"}), {"since": "b", "since_typed": "c"})

    def test_claims_lists_everything(self):
        coord.claim("my-app", "codex")
        coord.claim("my-api", "claude")
        self.assertEqual(set(coord.claims()), {"my-app", "my-api"})


if __name__ == "__main__":
    unittest.main()
