"""Cross-process tests: real separate Python processes sharing one SQLite file.

These prove the locks and the claim-before-send logic hold across processes, which an
in-process test cannot show. Each test takes a few seconds.
"""
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from django.test import SimpleTestCase

HELPER = Path(__file__).resolve().parent / "testing" / "proc.py"
ENV = {
    **os.environ,
    "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/x",
    "BLUESKY_HANDLE": "bot.bsky.social",
    "BLUESKY_PASSWORD": "not-used",
    "SCHEDULER_LEASE_SECONDS": "2",
    "SEND_STALE_SECONDS": "1",
    "ACCOUNT_LOCK_SECONDS": "2",
    "BOT_LOG_LEVEL": "CRITICAL",
    "SETTINGS_ENV_MIRROR_PATH": "",  # never touch the real .env
}


class ProcessTestCase(SimpleTestCase):
    extra_env = {}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.db = str(self.dir / "shared.sqlite3")
        self.log = str(self.dir / "discord.log")
        self.env = {**ENV, **self.extra_env}
        self.run_proc("migrate")

    def start(self, *args):
        return subprocess.Popen([sys.executable, str(HELPER), self.db, *args], env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def run_proc(self, *args, expect_ok=True):
        p = self.start(*args)
        out, err = p.communicate(timeout=60)
        if expect_ok:
            self.assertEqual(p.returncode, 0, err)
        return out.strip(), p.returncode

    def run_together(self, n, args_for):
        """Start n processes that all begin work at the same moment (a shared start time)."""
        start_at = str(time.time() + 3)  # leaves time for every process to import Django
        procs = [self.start(*args_for(i, start_at)) for i in range(n)]
        outs = []
        for p in procs:
            out, err = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0, err)
            outs.append(out.strip())
        return outs

    def sends(self):
        if not os.path.exists(self.log):
            return Counter()
        return Counter(Path(self.log).read_text(encoding="utf-8").split())


class LeaseAcrossProcessesTests(ProcessTestCase):
    def test_second_process_is_denied_while_first_holds_the_lease(self):
        holder = self.start("lease", "A", "8", "release")
        self.assertEqual(holder.stdout.readline().strip(), "ACQUIRED")
        out, _ = self.run_proc("lease", "B", "0", "release")
        self.assertEqual(out, "DENIED")
        holder.communicate(timeout=30)
        out, _ = self.run_proc("lease", "B", "0", "release")
        self.assertEqual(out, "ACQUIRED", "a released lease is free at once")

    def test_crashed_holder_is_recovered_after_the_lease_expires(self):
        self.env["SCHEDULER_LEASE_SECONDS"] = "8"
        crashed_at = time.time()
        out, _ = self.run_proc("lease", "A", "0", "crash")  # exits without releasing
        self.assertEqual(out, "ACQUIRED")
        out, _ = self.run_proc("lease", "B", "0", "release")
        self.assertEqual(out, "DENIED", "the dead holder's lease is still valid")
        time.sleep(max(0, crashed_at + 9 - time.time()))  # wait out the 8 s lease
        out, _ = self.run_proc("lease", "B", "0", "release")
        self.assertEqual(out, "ACQUIRED", "a stale lease can be taken over")

    def test_only_one_of_many_simultaneous_checkers_runs_a_cycle(self):
        outs = self.run_together(4, lambda i, t: ("cycle", f"C{i}", self.log, t))
        self.assertEqual(sorted(outs), ["RAN", "STANDBY", "STANDBY", "STANDBY"])
        self.assertTrue(all(n == 1 for n in self.sends().values()))


class DuplicatePreventionAcrossProcessesTests(ProcessTestCase):
    def test_concurrent_checks_of_one_account_send_each_post_once(self):
        outs = self.run_together(4, lambda i, t: ("poll", self.log, t))
        sends = self.sends()
        self.assertEqual(len(sends), 5, outs)
        self.assertEqual(set(sends.values()), {1}, f"duplicates: {sends}")
        self.assertIn("BUSY", " ".join(outs), "the account lock turned the other checks away")

    def test_claims_alone_prevent_duplicates_without_the_account_lock(self):
        self.run_together(4, lambda i, t: ("claim_and_deliver", self.log, t))
        sends = self.sends()
        self.assertEqual(len(sends), 5)
        self.assertEqual(set(sends.values()), {1}, f"duplicates: {sends}")


class TwoChannelsAcrossProcessesTests(ProcessTestCase):
    """The account sends to the default channel and a second one: each gets each post once."""
    extra_env = {"PROC_SECOND_CHANNEL": "https://discord.com/api/webhooks/2/second"}

    def assert_each_channel_got_each_post_once(self, outs=None):
        sends = self.sends()
        self.assertEqual(len(sends), 10, outs)
        self.assertEqual(set(sends.values()), {1}, f"duplicates: {sends}")
        self.assertEqual(Counter(key.split("|")[0] for key in sends), {"x": 5, "second": 5})

    def test_concurrent_checks_send_each_post_once_to_each_channel(self):
        outs = self.run_together(4, lambda i, t: ("poll", self.log, t))
        self.assert_each_channel_got_each_post_once(outs)

    def test_claims_alone_prevent_duplicates_per_channel(self):
        outs = self.run_together(4, lambda i, t: ("claim_and_deliver", self.log, t))
        self.assert_each_channel_got_each_post_once(outs)

    def test_a_crash_mid_send_is_recovered_once_per_channel(self):
        self.env["ACCOUNT_LOCK_SECONDS"] = "2"
        crashed_at = time.time()
        _, code = self.run_proc("crash_mid_send", self.log, expect_ok=False)
        self.assertEqual(code, 3)
        status, _ = self.run_proc("status")
        self.assertEqual(status.count(" sending "), 1, f"only the delivery being sent is claimed:\n{status}")
        time.sleep(max(0, crashed_at + 3 - time.time()))  # the lock (2 s) and the stale send (1 s)
        out, _ = self.run_proc("poll", self.log, "0")
        self.assertEqual(out, "SENT 5")
        self.assert_each_channel_got_each_post_once()
        out, _ = self.run_proc("poll", self.log, "0")
        self.assertEqual(out, "SENT 0", "nothing is sent twice afterwards")


class CheckAllAcrossProcessesTests(ProcessTestCase):
    def test_check_all_during_a_scheduled_check_sends_each_post_once(self):
        def args(i, start_at):
            return ("cycle", "S", self.log, start_at) if i == 0 else ("check_all", self.log, start_at)

        outs = self.run_together(4, args)
        sends = self.sends()
        self.assertEqual(len(sends), 5, outs)
        self.assertEqual(set(sends.values()), {1}, f"duplicates: {sends}")
        results = [o for o in outs[1:]]
        self.assertTrue(all(o in ("artist.bsky.social=checked", "artist.bsky.social=already_checking")
                            for o in results), results)
        self.assertIn("already_checking", " ".join(results), "overlapping checks were turned away, not repeated")


class CheckAllJobAcrossProcessesTests(ProcessTestCase):
    """The background job, the scheduled check and two more Check All runs at once."""

    def test_background_job_during_other_checks_sends_each_post_once(self):
        self.env["SCHEDULER_LEASE_SECONDS"] = "30"  # the job's checker keeps its lease throughout
        job_id, _ = self.run_proc("queue_job")
        self.assertTrue(job_id)

        def args(i, start_at):
            if i == 0:
                return ("run_jobs", "S", self.log, start_at)
            if i == 1:
                return ("cycle", "S", self.log, start_at)
            return ("check_all", self.log, start_at)

        outs = self.run_together(4, args)
        sends = self.sends()
        self.assertEqual(len(sends), 5, outs)
        self.assertEqual(set(sends.values()), {1}, f"duplicates: {sends}")
        self.assertTrue(outs[0].startswith("RAN 1 finished "), outs[0])
        self.assertIn(outs[0].rsplit(" ", 1)[1], ("artist.bsky.social=checked", "artist.bsky.social=already_checking"))
        self.assertIn("already_checking", " ".join(outs), "overlapping checks were turned away, not repeated")


class InterruptedSendTests(ProcessTestCase):
    def test_send_interrupted_by_a_crash_is_delivered_once_after_recovery(self):
        self.env["ACCOUNT_LOCK_SECONDS"] = "8"  # long enough to observe it held after the crash
        crashed_at = time.time()
        _, code = self.run_proc("crash_mid_send", self.log, expect_ok=False)
        self.assertEqual(code, 3)
        status, _ = self.run_proc("status")
        self.assertIn("sending", status, "the post the process died on stays claimed")
        self.assertEqual(self.sends(), Counter())

        out, _ = self.run_proc("poll", self.log, "0")
        self.assertEqual(out, "BUSY", "the crashed check's account lock is still valid")

        time.sleep(max(0, crashed_at + 9 - time.time()))  # wait out the 8 s lock (stale send: 1 s)
        out, _ = self.run_proc("poll", self.log, "0")
        self.assertEqual(out, "SENT 5")
        self.assertEqual(set(self.sends().values()), {1})
        self.assertEqual(len(self.sends()), 5)

        out, _ = self.run_proc("poll", self.log, "0")
        self.assertEqual(out, "SENT 0", "nothing is sent twice afterwards")
