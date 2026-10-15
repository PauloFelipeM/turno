"""The panel's serial loader (ui/src/lib/loader.ts), run by node as it ships: one request at a time, an answer for
a key the page has left is dropped, and what was asked meanwhile runs for the key open now."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "ui"

SCRIPT = """
import { createSerialLoader } from './src/lib/loader.ts';
const out = {};
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
const tick = () => new Promise((r) => setTimeout(r, 0));

// Open A, then B while A's requests still run: A's data never lands, B's does, once.
{
  let key = 'A';
  const applied = [], started = [], gates = {};
  const loader = createSerialLoader({
    getKey: () => key,
    run: async (k, mode, isCurrent) => {
      started.push(k);
      gates[k] = deferred();
      const v = await gates[k].promise;
      if (isCurrent()) applied.push(v);
    },
  });
  loader.load('load');
  await tick();
  key = 'B';
  loader.load('load');      // asked while A runs
  gates.A.resolve('data of A');
  await tick(); await tick();
  gates.B.resolve('data of B');
  await tick(); await tick();
  out.raceStarted = started; out.raceApplied = applied;
}

// A "more" asked while busy is not repeated; a refresh and a reset are, merged into a reset.
{
  const modes = [], gates = [];
  const loader = createSerialLoader({
    getKey: () => 'p',
    repeat: (m) => m !== 'more',
    combine: (q, m) => (q === 'reset' || m === 'reset' ? 'reset' : 'refresh'),
    run: async (k, mode) => { modes.push(mode); const g = deferred(); gates.push(g); await g.promise; },
  });
  loader.load('refresh');
  await tick();
  loader.load('more'); loader.load('refresh'); loader.load('reset'); loader.load('refresh');
  gates[0].resolve();
  await tick(); await tick();
  gates[1].resolve();
  await tick(); await tick();
  out.modes = modes;
}

// A failing run does not stop the queue.
{
  const seen = [];
  let first = true;
  const loader = createSerialLoader({ getKey: () => 1, run: async (k, m) => { seen.push(m); if (first) { first = false; throw new Error('x'); } } });
  await loader.load('a').catch(() => {});
  await loader.load('b');
  out.afterFailure = seen;
}
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class SerialLoaderTest(unittest.TestCase):
    def test_loader(self):
        r = subprocess.run(["node", "--input-type=module", "-e", SCRIPT], cwd=UI, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual(got["raceStarted"], ["A", "B"])
        self.assertEqual(got["raceApplied"], ["data of B"])
        self.assertEqual(got["modes"], ["refresh", "reset"])
        self.assertEqual(got["afterFailure"], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
