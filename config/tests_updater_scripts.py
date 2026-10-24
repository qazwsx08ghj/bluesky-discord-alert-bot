"""The one-click updater in update.bat (docker/scripts/common.bat, :update_main), run for real under
cmd.exe with a fake docker and curl (config/testing/fake_docker.cmd). Windows only.

A release folder pinned to 1.0.0-rc.3; the fake GitHub check (update-plan) offers 1.0.0-rc.4, whose
files come from a bundle exported from this checkout (with a marker in README.txt, and a changed
update.bat, so the swap - including of the running script - is visible).
"""
import os
import sys
import unittest
from pathlib import Path

from config.tests_deploy import ScriptHarness

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "docker"))
import make_release_bundle as mrb  # noqa: E402

OLD = "ghcr.io/qazwsx08ghj/bluesky-discord-alert-bot:1.0.0-rc.3@sha256:" + "3" * 64
NEW = "ghcr.io/qazwsx08ghj/bluesky-discord-alert-bot:1.0.0-rc.4@sha256:" + "4" * 64
BACKUP = "db-before-update-20261024-000000-000001.sqlite3"
HEALTHY_NEW = '{"status":"ok","version":"1.0.0-rc.4"}'
RUNNING_SAME = dict(FAKE_OWN_IDS="web1 checker1", FAKE_RUNNING_IMAGE="sha256:same", FAKE_NEW_IMAGE="sha256:same")


@unittest.skipUnless(sys.platform == "win32", "the .bat scripts run on Windows")
class UpdaterScriptTests(ScriptHarness):
    def setUp(self):
        super().setUp()
        env = self.dir / "docker" / "compose.env"
        env.write_text(f"# settings\nALERTBOT_IMAGE={OLD}\nALERTBOT_PORT={self.port}\nMY_NOTE=kept\n", encoding="ascii")
        for name in ("README.txt", "README.zh-TW.txt", "LICENSE", "THIRD_PARTY_NOTICES.txt"):
            (self.dir / name).write_bytes(mrb.bundle_files()[name])
        self.bundle = self.dir.parent / "new-version"
        files = mrb.bundle_files()
        files["README.txt"] = files["README.txt"] + b"\r\nNEW VERSION 1.0.0-rc.4\r\n"
        files["update.bat"] = files["update.bat"] + b"rem updated to 1.0.0-rc.4\r\n"
        self.write_bundle(files)
        self.env.update(FAKE_BUNDLE_DIR=str(self.bundle), ALERTBOT_PORT="")
        self.env.pop("ALERTBOT_PORT")
        self.old_readme = (self.dir / "README.txt").read_bytes()

    def write_bundle(self, files, tamper=None):
        folder = self.bundle / "windows"
        for name, data in files.items():
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(data)
        info = mrb.index(files)
        (self.bundle / "windows.sha256").write_bytes(  # LF line ends, as in the image
            "".join(f"{d} {n}\n" for n, d in sorted(info["files"].items())).encode("ascii"))
        if tamper:
            (folder / tamper).write_bytes(b"changed after the hashes were made")

    def update(self, **fake):
        return self.run_script("update.bat", **{**RUNNING_SAME, "FAKE_CURL_BODY": HEALTHY_NEW, **fake})

    def state(self):
        path = self.dir / "docker" / "update" / "state.txt"
        return dict(l.split("=", 1) for l in path.read_text(encoding="ascii").splitlines()) if path.exists() else {}

    def compose_env(self):
        return (self.dir / "docker" / "compose.env").read_text(encoding="ascii")

    def has(self, calls, text):
        return any(text in c for c in calls)

    def assertUnchanged(self, calls):
        self.assertEqual((self.dir / "README.txt").read_bytes(), self.old_readme, "the files weren't replaced")
        self.assertIn(f"ALERTBOT_IMAGE={OLD}", self.compose_env())
        self.assertFalse(self.has(calls, " trial"), "the data wasn't touched")
        self.assertFalse(self.has(calls, " restore "))

    # -- checking ---------------------------------------------------------------------------------------
    def test_up_to_date_changes_nothing(self):
        code, out, calls = self.update(FAKE_PLAN="up_to_date")
        self.assertEqual(code, 0, out)
        self.assertIn("You have the newest version 1.0.0-rc.3", out)
        for step in ("stop", "up -d", "backup --label", "pull"):
            self.assertFalse(any(step in c for c in self.compose_calls(calls)), step)
        self.assertFalse(self.has(calls, "docker pull"))
        seen = (self.dir / "docker" / "update" / "seen.txt").read_text(encoding="ascii")
        self.assertIn("REVISION=2", seen, "the withdrawal list it accepted is remembered")

    def test_env_options_survive_the_loops_that_read_answers(self):
        # The fake docker refuses a "-e NAME=value" that arrived without its "=".
        code, out, calls = self.update(FAKE_PLAN="up_to_date", ALERTBOT_UPDATE_TEST_NETWORK="testnet",
                                       ALERTBOT_UPDATE_TEST_API="http://fakegh:8080")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.has(calls, "ALERTBOT_UPDATE_TEST_API=http://fakegh:8080"))

    def test_up_to_date_while_stopped_doesnt_start_it(self):
        code, out, calls = self.update(FAKE_PLAN="up_to_date", FAKE_OWN_IDS="")
        self.assertEqual(code, 0, out)
        self.assertIn("The bot isn't running right now: start.bat starts it", out)
        self.assertNotIn("up -d", self.compose_calls(calls))

    def test_the_check_is_made_by_the_installed_image_with_the_remembered_list(self):
        (self.dir / "docker" / "update").mkdir(parents=True)
        (self.dir / "docker" / "update" / "seen.txt").write_text("REVISION=7\nDIGEST=" + "7" * 64 + "\n", encoding="ascii")
        _, _, calls = self.update(FAKE_PLAN="up_to_date")
        plan = [c for c in calls if " update-plan " in c][0]
        self.assertIn(f'"{OLD}" update-plan --channel prerelease --seen-revision 7 --seen-digest ' + "7" * 64, plan)
        for flag in ("--read-only", "--cap-drop ALL", "--security-opt no-new-privileges"):
            self.assertIn(flag, plan)
        self.assertNotIn("-v ", plan, "the check never sees the data")

    def test_the_channel(self):
        _, _, calls = self.update(FAKE_PLAN="up_to_date", ALERTBOT_CHANNEL="stable")
        self.assertIn("--channel stable", [c for c in calls if " update-plan " in c][0])
        env = self.dir / "docker" / "compose.env"
        env.write_text(self.compose_env().replace(":1.0.0-rc.3@", ":1.0.0@"), encoding="ascii")
        _, _, calls = self.update(FAKE_PLAN="up_to_date")
        self.assertIn("--channel stable", [c for c in calls if " update-plan " in c][0], "a final release: stable")

    def test_a_blocked_check_changes_nothing(self):
        code, out, calls = self.update(FAKE_PLAN="blocked")
        self.assertEqual(code, 1)
        self.assertIn("GitHub couldn't be reached. Nothing was changed.", out)
        self.assertUnchanged(calls)
        self.assertFalse((self.dir / "docker" / "update" / "seen.txt").exists())

    def test_no_answer_from_the_check(self):
        code, out, calls = self.update()
        self.assertEqual(code, 1)
        self.assertIn("Couldn't check for updates", out)
        self.assertUnchanged(calls)

    # -- updating -----------------------------------------------------------------------------------------
    def test_a_successful_update(self):
        code, out, calls = self.update(FAKE_PLAN="update")
        self.assertEqual(code, 0, out)
        self.assertIn("The update is complete: 1.0.0-rc.4 is running", out)
        self.assertIn(b"NEW VERSION 1.0.0-rc.4", (self.dir / "README.txt").read_bytes(), "the new files")
        self.assertIn(b"rem updated to 1.0.0-rc.4", (self.dir / "update.bat").read_bytes(), "update.bat too, while it ran")
        env = self.compose_env()
        self.assertIn(f"ALERTBOT_IMAGE={NEW}", env, "pinned by digest")
        self.assertNotIn(OLD, env)
        self.assertIn(f"ALERTBOT_PORT={self.port}", env, "the other settings are kept")
        self.assertIn("MY_NOTE=kept", env)
        previous = self.dir / "docker" / "update" / "previous"
        self.assertEqual((previous / "README.txt").read_bytes(), self.old_readme, "the previous files are kept")
        self.assertIn(OLD, (previous / "docker" / "compose.env").read_text(encoding="ascii"))
        self.assertEqual(self.state()["STATE"], "DONE")
        self.assertEqual(self.state()["BACKUP_FILE"], BACKUP)
        self.assertIn("REVISION=3", (self.dir / "docker" / "update" / "seen.txt").read_text(encoding="ascii"))
        order = [next(i for i, c in enumerate(calls) if text in c) for text in (
            "create --name alertbot-update-lock", f'"{NEW}" describe', "cp alertbot-update-files", " update-preflight",
            " stop", 'ps --filter "volume=alertbot_bot-data"', "backup --label before-update", f'"{NEW}" trial',
            " fingerprint", " up -d", "api/health/")]
        self.assertEqual(order, sorted(order), "check, stop, back up, swap, trial, start")
        self.assertTrue(calls[-1].startswith("docker rm -f alertbot-update-lock") or
                        self.has(calls[order[-1]:], "rm -f alertbot-update-lock"))
        trial = [c for c in calls if f'"{NEW}" trial' in c][0]
        for flag in ("--network none", "--read-only", "--cap-drop ALL", "-v alertbot_bot-data:/data"):
            self.assertIn(flag, trial)
        self.assertNotIn(" -p ", trial, "no port during the trial")

    def test_more_steps_ahead_are_announced(self):
        code, out, _ = self.update(FAKE_PLAN="update", FAKE_STEPS="2", FAKE_FINAL="1.0.0-rc.5")
        self.assertEqual(code, 0, out)
        self.assertIn("The next step towards 1.0.0-rc.5 is ready: run update.bat again", out)

    # -- failures before anything changes ---------------------------------------------------------------
    def assertAbortedUnchanged(self, code, out, calls, restarted=False):
        self.assertEqual(code, 1, out)
        self.assertUnchanged(calls)
        self.assertEqual(self.state()["STATE"], "ABORTED")
        self.assertTrue(self.has(calls, "rm -f alertbot-update-lock"), "the lock is released")
        self.assertEqual("up -d" in self.compose_calls(calls), restarted)

    def test_the_wrong_version_was_downloaded(self):
        code, out, calls = self.update(FAKE_PLAN="update", FAKE_TARGET_VERSION="9.9.9")
        self.assertIn("isn't version 1.0.0-rc.4", out)
        self.assertAbortedUnchanged(code, out, calls)
        self.assertFalse(self.has(calls, " stop"))

    def test_a_download_that_fails(self):
        code, out, calls = self.update(FAKE_PLAN="update", FAKE_IMAGE_MISSING="1.0.0-rc.4", FAKE_PULL_FAIL="1")
        self.assertIn(f"Downloading {NEW}", out)
        self.assertAbortedUnchanged(code, out, calls)

    def test_a_file_that_doesnt_match_its_hash(self):
        self.write_bundle(mrb.bundle_files(), tamper="docker/scripts/common.bat")
        code, out, calls = self.update(FAKE_PLAN="update")
        self.assertIn("docker/scripts/common.bat doesn't match its hash", out)
        self.assertAbortedUnchanged(code, out, calls)

    def test_files_that_cant_be_copied_out(self):
        code, out, calls = self.update(FAKE_PLAN="update", FAKE_CP_FAIL="1")
        self.assertIn("couldn't be copied out", out)
        self.assertAbortedUnchanged(code, out, calls)

    def test_a_preflight_that_fails(self):
        code, out, calls = self.update(FAKE_PLAN="update", FAKE_PREFLIGHT="1")
        self.assertIn("can't take over your data", out)
        self.assertAbortedUnchanged(code, out, calls)
        self.assertFalse(self.has(calls, " stop"), "the bot was never stopped")

    def test_data_used_by_something_else(self):
        code, out, calls = self.update(FAKE_PLAN="update", FAKE_VOLUME_USERS="oldbot-checker-1")
        self.assertIn("oldbot-checker-1", out)
        self.assertAbortedUnchanged(code, out, calls, restarted=True)
        self.assertFalse(self.has(calls, "backup --label before-update"))

    def test_a_backup_that_fails(self):
        code, out, calls = self.update(FAKE_PLAN="update", FAKE_FAIL_ON="backup --label before-update")
        self.assertAbortedUnchanged(code, out, calls, restarted=True)

    # -- failures after the swap: going back --------------------------------------------------------------
    def assertRolledBack(self, code, out, calls):
        self.assertEqual(code, 1, out)
        self.assertIn("the previous version is back, with your data as it was before the update", out)
        self.assertEqual((self.dir / "README.txt").read_bytes(), self.old_readme, "the previous files are back")
        self.assertIn(f"ALERTBOT_IMAGE={OLD}", self.compose_env())
        restore = [c for c in calls if " restore " in c and c.startswith("docker run")]
        self.assertEqual(len(restore), 1)
        self.assertIn(f'"{OLD}" restore "/data/backups/{BACKUP}"', restore[0], "the backup, with the old version")
        self.assertEqual(self.state()["STATE"], "ROLLED_BACK")

    def test_a_trial_run_that_fails_goes_back(self):
        code, out, calls = self.update(FAKE_PLAN="update", FAKE_TRIAL="1")
        self.assertIn("failed in the trial run", out)
        self.assertRolledBack(code, out, calls)
        self.assertEqual(self.compose_calls(calls).count("up -d"), 1, "only the old version was started")

    def test_a_new_version_that_doesnt_start_goes_back_when_nothing_was_written(self):
        code, out, calls = self.update(FAKE_PLAN="update", FAKE_CURL_BODY='{"status":"ok","version":"1.0.0-rc.3"}')
        self.assertIn("1.0.0-rc.4 didn't start properly", out)
        self.assertRolledBack(code, out, calls)

    def test_a_new_version_that_wrote_data_is_left_for_the_user(self):
        code, out, calls = self.update(FAKE_PLAN="update", FAKE_CURL_BODY='{"status":"ok","version":"1.0.0-rc.3"}',
                                       FAKE_FP_LATER="bbbbbbbbbbbbbbbbbbbb")
        self.assertEqual(code, 1)
        self.assertIn("STOP: the update couldn't be finished or undone by itself", out)
        self.assertIn(BACKUP, out)
        self.assertFalse(self.has(calls, " restore "), "nothing written since is thrown away")
        self.assertEqual(self.state()["STATE"], "NEEDS_USER")

    def test_a_restore_that_fails_is_left_for_the_user(self):
        code, out, _ = self.update(FAKE_PLAN="update", FAKE_TRIAL="1", FAKE_RESTORE="1")
        self.assertEqual(code, 1)
        self.assertIn("STOP:", out)
        self.assertEqual(self.state()["STATE"], "NEEDS_USER")

    # -- interrupted updates, and the lock ---------------------------------------------------------------------
    def seed(self, state, swapped=False):
        upd = self.dir / "docker" / "update"
        (upd / "previous" / "docker" / "scripts").mkdir(parents=True, exist_ok=True)
        for name in ("README.txt", "update.bat", "docker/compose.env", "docker/scripts/common.bat"):
            (upd / "previous" / name).write_bytes((self.dir / name).read_bytes())
        if swapped:
            (self.dir / "README.txt").write_bytes(b"half-installed new version")
            (self.dir / "docker" / "compose.env").write_text(f"ALERTBOT_IMAGE={NEW}\nALERTBOT_PORT={self.port}\n",
                                                             encoding="ascii")
        (upd / "state.txt").write_text(
            f"STATE={state}\nOLD_IMAGE={OLD}\nNEW_IMAGE={NEW}\nTARGET_VERSION=1.0.0-rc.4\nBACKUP_FILE={BACKUP}\n"
            f"FP_TRIAL=aaaaaaaaaaaaaaaaaaaa\nWAS_RUNNING=1\n", encoding="ascii")

    def test_an_update_interrupted_after_the_swap_is_undone_first(self):
        self.seed("SWAPPED", swapped=True)
        code, out, calls = self.update(FAKE_PLAN="up_to_date")
        self.assertIn("An earlier update stopped part-way (at SWAPPED)", out)
        self.assertRolledBack(code, out, calls)
        self.assertFalse(self.has(calls, " update-plan "), "it stops after undoing")

    def test_an_update_interrupted_before_anything_changed(self):
        self.seed("BEGIN")
        code, out, calls = self.update(FAKE_PLAN="up_to_date")
        self.assertEqual(code, 0, out)
        self.assertIn("Nothing had changed yet", out)
        self.assertIn("You have the newest version", out, "then it checks as usual")
        self.assertFalse(self.has(calls, " restore "))

    def test_an_update_interrupted_while_starting_that_had_worked(self):
        self.seed("STARTING", swapped=True)
        code, out, calls = self.update(FAKE_PLAN="up_to_date")
        self.assertIn("It had finished: 1.0.0-rc.4 is running", out)
        self.assertFalse(self.has(calls, " restore "))
        self.assertEqual(code, 0, out)

    def test_a_decision_left_to_the_user(self):
        self.seed("NEEDS_USER", swapped=True)
        code, out, calls = self.update(FAKE_PLAN="up_to_date")
        self.assertEqual(code, 1)
        self.assertIn("type KEEP to keep it", out)
        self.assertFalse(self.has(calls, " update-plan "))
        code, out, _ = self.update(FAKE_PLAN="up_to_date", ALERTBOT_UPDATE_RESOLVE="KEEP")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.state()["STATE"], "DONE")

    def test_other_scripts_wait_for_an_unfinished_update(self):
        self.seed("TRIAL_OK", swapped=True)
        for name in ("start.bat", "restore.bat", "setup.bat"):
            with self.subTest(script=name):
                code, out, calls = self.run_script(name, **RUNNING_SAME)
                self.assertEqual(code, 1, out)
                self.assertIn("An update didn't finish (it stopped at TRIAL_OK). Run update.bat first", out)
                self.assertNotIn("up -d", self.compose_calls(calls))
        self.seed("NEEDS_USER")
        code, out, _ = self.run_script("start.bat", **RUNNING_SAME)
        self.assertEqual(code, 0, out)

    def test_another_update_holding_the_lock(self):
        code, out, calls = self.update(FAKE_PLAN="update", FAKE_LOCK_HELD="1")
        self.assertEqual(code, 1)
        self.assertIn("Another update is running, or one was interrupted", out)
        self.assertUnchanged(calls)
        code, out, _ = self.update(FAKE_PLAN="update", FAKE_LOCK_HELD="1", ALERTBOT_UPDATE_TAKE_LOCK="YES")
        self.assertEqual(code, 0, out)

    # -- moving to this folder's own version (a newer download's folder) ---------------------------------
    def test_a_folder_with_another_version_than_the_running_one(self):
        code, out, calls = self.update(FAKE_PLAN="update", FAKE_RUNNING_IMAGE="sha256:older")
        self.assertEqual(code, 0, out)
        self.assertIn("Moving it to this folder's version", out)
        self.assertIn("A newer version, 1.0.0-rc.4, is available too: run update.bat again", out)
        self.assertEqual(self.compose_calls(calls)[-1], "up -d")
        self.assertFalse(self.has(calls, " trial"))
