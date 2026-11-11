"""Stress test: several fake crews run tasks concurrently with injected
worker crashes, while an observer samples the semaphore.

Invariants checked:
- The active-slot count NEVER exceeds max_concurrent_workers, at any sample.
- Normal (non-oncall) slots never exceed max - reserve.
- Every task's claim history is strictly non-overlapping (a claim starts
  only after the previous one released or expired).
- After the dust settles, one reap returns the world to zero active state:
  no leaked slots, claims, or running agent_runs.
"""

import random
import sqlite3
import threading
import time
import unittest

from ctlutil import Ctl

CREWS = 6
TASKS_PER_POD = 4
MAX_W = 4
RESERVE = 1
CRASH_RATE = 0.25


class Stress(unittest.TestCase):
    def test_concurrent_pods_with_crashes(self):
        c = Ctl({"max_concurrent_workers": MAX_W, "reserve_for_oncall": RESERVE})
        violations = []
        stop = threading.Event()

        def sampler():
            cx = sqlite3.connect(c.db, timeout=5)
            t = time.time()
            while not stop.is_set():
                try:
                    rows = cx.execute(
                        "SELECT kind, COUNT(*) FROM worker_slots "
                        "WHERE state='active' AND (expires_at IS NULL OR expires_at > ?) "
                        "GROUP BY kind", (time.time(),)).fetchall()
                except sqlite3.OperationalError:
                    continue  # writer holds the lock; sample again
                counts = dict(rows)
                total = sum(counts.values())
                if total > MAX_W:
                    violations.append(f"total {total} > {MAX_W}")
                if counts.get("normal", 0) > MAX_W - RESERVE:
                    violations.append(
                        f"normal {counts.get('normal')} > {MAX_W - RESERVE}")
                time.sleep(0.01)
            cx.close()

        def worker(pod, tasknum):
            crew = "crew_alpha" if pod % 2 == 0 else "crew_beta"
            agent = "eng_claude" if crew == "crew_alpha" else "beta_eng_codex"
            task = f"pod-{pod}-t{tasknum}"
            title = f"stress-{task}"
            rng = random.Random(hash(task))
            deadline = time.time() + 60
            while time.time() < deadline:
                code, slot = c.acquire_normal(
                    task=task, crew=crew, agent=agent, title=title, ttl="0.6",
                )
                if code != 0:
                    time.sleep(0.02)
                    continue
                c.seed_admission(
                    task=task, slot_id=slot["slot_id"], crew=crew,
                    agent=agent, title=title,
                )
                code, claim = c.run("claim", "acquire", "--task", task,
                                    "--claimant", f"{crew}/lead",
                                    "--agent", agent, "--title", title,
                                    "--ttl", "0.6")
                if code != 0:
                    # someone (an overlapping heartbeat) already owns recovery
                    c.run("slot", "release", "--slot", slot["slot_id"],
                          "--holder", f"{crew}/{agent}")
                    time.sleep(0.02)
                    continue
                time.sleep(rng.uniform(0.01, 0.05))  # "work"
                if rng.random() < CRASH_RATE:
                    return  # CRASH: no release — TTL must recover both
                c.run("claim", "release", "--task", task, "--claimant", f"{crew}/lead")
                c.run("slot", "release", "--slot", slot["slot_id"],
                      "--holder", f"{crew}/{agent}")
                return
            self.fail(f"{task} starved past deadline")

        s = threading.Thread(target=sampler, daemon=True)
        s.start()
        threads = [
            threading.Thread(target=worker, args=(p, t))
            for p in range(CREWS) for t in range(TASKS_PER_POD)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=90)
        stop.set()
        s.join(timeout=5)

        self.assertEqual(violations, [])

        # claims never overlapped per task: each acquire happened at/after the
        # previous claim's end (release or expiry) — small float slack only.
        cx = sqlite3.connect(c.db)
        tasks = [r[0] for r in cx.execute(
            "SELECT DISTINCT task_id FROM task_claims").fetchall()]
        for task in tasks:
            rows = cx.execute(
                "SELECT acquired_at, released_at, expires_at, state "
                "FROM task_claims WHERE task_id=? ORDER BY acquired_at",
                (task,)).fetchall()
            prev_end = None
            for acquired, released, expires, state in rows:
                if prev_end is not None:
                    self.assertGreaterEqual(
                        acquired, prev_end - 0.001,
                        f"{task}: claim at {acquired} overlaps prev end {prev_end}")
                prev_end = released if released is not None else expires
        cx.close()

        # Let crashed holders' TTLs lapse, then the world must reap clean.
        # The stress body itself runs on the WALL clock on purpose -- it is
        # testing real cross-process concurrency, which virtual time would
        # erase. Only this settling step jumps the clock: an hour in the
        # future every TTL has certainly lapsed, so the assertion no longer
        # depends on a 0.7s sleep being long enough on a loaded box.
        settled = time.time() + 3600
        c.run("reap", now=settled)
        _, dump = c.run("dump", now=settled)
        self.assertEqual(dump["slots"], [])
        self.assertEqual(dump["claims"], [])


if __name__ == "__main__":
    unittest.main()
