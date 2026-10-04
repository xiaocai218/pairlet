import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "update-codex-pairlet.py"
SPEC = importlib.util.spec_from_file_location("updater", SCRIPT)
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.instance = updater.Updater(self.home)
        self.instance.root.mkdir(parents=True)
        self.instance.package.mkdir(parents=True)
        (self.instance.package / "original").touch()
        self.candidate = self.instance.root / "versions/0.161.0/node_modules/@openai/codex"
        self.candidate.mkdir(parents=True)
        self.state = dict(version="0.160.0", managed=dict(status="running"), pairletPid=123)

    def test_stack_prepare_never_switches_or_checks_idle(self):
        with patch.object(updater.sys, "argv", ["updater", "--prepare-stack", "--version", "0.160.0",
                                               "--pairlet-ref", "v2.2.0"]), \
                patch.object(updater, "Updater", return_value=self.instance), \
                patch.object(self.instance, "status", return_value=self.state), \
                patch.object(self.instance, "prepare", return_value=self.candidate) as prepare, \
                patch.object(self.instance, "idle") as idle, \
                patch.object(self.instance, "promote") as promote, \
                patch.object(updater, "run", return_value="candidate prepared") as commands:
            updater.main()
        prepare.assert_called_once_with("0.160.0")
        idle.assert_not_called()
        promote.assert_not_called()
        self.assertIn("v2.2.0", commands.call_args.args)

    def test_stack_failure_never_promotes(self):
        with patch.object(updater.sys, "argv", ["updater", "--prepare-stack", "--version", "0.160.0"]), \
                patch.object(updater, "Updater", return_value=self.instance), \
                patch.object(self.instance, "status", return_value=self.state), \
                patch.object(self.instance, "prepare", return_value=self.candidate), \
                patch.object(self.instance, "promote") as promote, \
                patch.object(updater, "run", side_effect=RuntimeError("patch conflict")):
            with self.assertRaisesRegex(RuntimeError, "patch conflict"):
                updater.main()
        promote.assert_not_called()

    def test_exact_version_validation(self):
        self.assertEqual(updater.validate_version("0.161.0"), "0.161.0")
        for invalid in ("latest", "../foo", "0.161.0;id", ""):
            with self.assertRaises(ValueError):
                updater.validate_version(invalid)

    def test_runner_accepts_candidate_build_timeout(self):
        with patch.object(updater.subprocess, "run", return_value=updater.subprocess.CompletedProcess([], 0, "ok")) as process:
            self.assertEqual(updater.run("test-command", timeout=2100), "ok")
        self.assertEqual(process.call_args.kwargs["timeout"], 2100)

    def test_successful_upgrade_can_be_rolled_back_later(self):
        old = dict(self.state, managed=dict(status="stopped"), pairletInstallation="/old/pairlet")
        updated = dict(old, version="0.161.0")
        with patch.object(updater, "version", return_value="0.161.0"), \
                patch.object(updater, "run", return_value=""), \
                patch.object(self.instance, "idle"), \
                patch.object(self.instance, "status", return_value=updated):
            self.instance.promote(self.candidate, old)
        with patch.object(updater, "run", return_value=""), \
                patch.object(self.instance, "idle"), \
                patch.object(self.instance, "status", side_effect=[updated, old]):
            self.instance.rollback_last()
        self.assertTrue((self.instance.package / "original").exists())
        self.assertFalse((self.instance.root / "last-good.json").exists())
        self.assertFalse(self.instance.journal.exists())

    def test_rollback_last_blocks_external_runtime_change(self):
        updater.atomic_json(self.instance.root / "last-good.json",
                            dict(version="0.161.0", pairletInstallation="/expected", previous={}))
        with patch.object(self.instance, "status", return_value=dict(self.state, pairletInstallation="/other")):
            with self.assertRaisesRegex(RuntimeError, "changed"):
                self.instance.rollback_last()
        self.assertFalse(self.instance.journal.exists())

    def test_stack_failure_restores_daemon_override(self):
        old = dict(self.state, pairletInstallation="/old/pairlet")
        fragment = self.home / "cc-pocket-daemon.service"
        fragment.write_text("[Service]\nExecStart=/old/pairlet/bin/cc-pocket-daemon run --relay wss://nas.xiaocai218.top\n")
        stack = dict(codex=str(self.candidate), codexVersion="0.161.0", daemon="/new/pairlet")
        failures = [True]

        def command(*arguments, **kwargs):
            if "FragmentPath" in arguments:
                return str(fragment)
            if "start" in arguments and updater.SERVICE in arguments and failures:
                failures.pop()
                raise RuntimeError("new daemon failed")
            return ""

        with patch.object(updater, "version", return_value="0.161.0"), \
                patch.object(updater, "run", side_effect=command), \
                patch.object(self.instance, "idle"), \
                patch.object(self.instance, "status", return_value=old), \
                patch.object(updater.pairlet_stack, "validate"), \
                patch.object(updater.pairlet_stack, "backup_state"):
            with self.assertRaisesRegex(RuntimeError, "new daemon failed"):
                self.instance.promote(self.candidate, old, stack)
        override = self.home / ".config/systemd/user" / (updater.SERVICE + ".d") / updater.pairlet_stack.DROPIN
        self.assertFalse(override.exists())
        self.assertTrue((self.instance.package / "original").exists())
        self.assertFalse(self.instance.journal.exists())

    def test_journal_written_with_private_permissions(self):
        updater.atomic_json(self.instance.journal, dict(phase="prepared"))
        self.assertEqual(json.loads(self.instance.journal.read_text()), dict(phase="prepared"))
        self.assertEqual(self.instance.journal.stat().st_mode & 0o777, 0o600)

    def test_active_sessions_block_before_mutation(self):
        with patch.object(updater, "version", return_value="0.161.0"), \
                patch.object(self.instance, "idle", side_effect=RuntimeError("active")):
            with self.assertRaisesRegex(RuntimeError, "active"):
                self.instance.promote(self.candidate, self.state)
        self.assertTrue((self.instance.package / "original").exists())
        self.assertFalse(self.instance.journal.exists())

    def test_pending_upgrade_blocks_promotion(self):
        updater.atomic_json(self.instance.journal, {})
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            self.instance.promote(self.candidate, self.state)

    def test_success_preserves_old_package(self):
        updated = dict(self.state, version="0.161.0")
        with patch.object(updater, "version", return_value="0.161.0"), \
                patch.object(updater, "run", return_value="") as commands, \
                patch.object(self.instance, "idle"), \
                patch.object(self.instance, "status", return_value=updated):
            self.instance.promote(self.candidate, self.state)
        self.assertEqual(self.instance.package.resolve(), self.candidate)
        saved = json.loads((self.instance.root / "last-good.json").read_text())
        self.assertTrue((Path(saved["backup"]) / "original").exists())
        self.assertFalse(self.instance.journal.exists())
        self.assertTrue(any("--from-cli" in call.args for call in commands.call_args_list))

    def test_failure_restores_package_and_versions(self):
        failures = [True]

        def command(*args):
            if "--from-cli" in args and failures:
                failures.pop()
                raise RuntimeError("update failed")
            return ""

        with patch.object(updater, "version", return_value="0.161.0"), \
                patch.object(updater, "run", side_effect=command), \
                patch.object(self.instance, "idle"), \
                patch.object(self.instance, "status", return_value=self.state):
            with self.assertRaisesRegex(RuntimeError, "update failed"):
                self.instance.promote(self.candidate, self.state)
        self.assertFalse(self.instance.package.is_symlink())
        self.assertTrue((self.instance.package / "original").exists())
        self.assertFalse(self.instance.journal.exists())

    def test_failed_rollback_keeps_journal(self):
        backup = self.instance.package.with_name("codex.backup")
        self.instance.package.rename(backup)
        self.instance.package.symlink_to(self.candidate)
        updater.atomic_json(self.instance.journal, dict(backup=str(backup), oldVersion="0.160.0",
                                                      managedRunning=True, pairletRunning=True))
        with patch.object(updater, "run", side_effect=RuntimeError("service unavailable")):
            with self.assertRaises(RuntimeError):
                self.instance.rollback()
        self.assertTrue(self.instance.journal.exists())

    def test_probe_failure_never_changes_canonical_package(self):
        with patch.object(updater, "version", return_value="0.161.0"), \
                patch.object(updater.subprocess, "run", return_value=updater.subprocess.CompletedProcess([], 1)):
            with self.assertRaisesRegex(RuntimeError, "probe-codex-wire.py failed"):
                self.instance.prepare("0.161.0")
        self.assertTrue((self.instance.package / "original").exists())
        self.assertFalse(self.instance.journal.exists())

    def test_version_drift_fails_before_service_changes(self):
        state = dict(cliVersion="0.160.0", managedCodexVersion="0.159.0")
        with patch.object(updater, "version", return_value="0.160.0"), \
                patch.object(updater, "run", return_value=json.dumps(state)) as commands:
            with self.assertRaisesRegex(RuntimeError, "differ"):
                self.instance.status()
        self.assertEqual(commands.call_count, 1)

    def test_running_managed_server_blocks_upgrade(self):
        with self.assertRaisesRegex(RuntimeError, "daemon stop"):
            self.instance.idle(self.state)

    def test_unknown_managed_state_blocks_upgrade(self):
        with self.assertRaisesRegex(RuntimeError, "unknown managed"):
            self.instance.idle(dict(managed={}, pairletPid=0))

    def test_candidate_is_retested_without_reinstall(self):
        with patch.object(updater, "version", return_value="0.161.0"), \
                patch.object(updater, "run") as commands, \
                patch.object(updater.subprocess, "run", return_value=updater.subprocess.CompletedProcess([], 0)) as probes:
            self.assertEqual(self.instance.prepare("0.161.0"), self.candidate)
        commands.assert_not_called()
        self.assertEqual(probes.call_count, 2)

    def test_same_version_prepare_still_runs_probes(self):
        with patch.object(updater.sys, "argv", ["updater", "--prepare-only", "--version", "0.160.0"]), \
                patch.object(updater, "Updater", return_value=self.instance), \
                patch.object(self.instance, "status", return_value=self.state), \
                patch.object(self.instance, "prepare", return_value=self.candidate) as prepare, \
                patch.object(self.instance, "promote") as promote:
            updater.main()
        prepare.assert_called_once_with("0.160.0")
        promote.assert_not_called()

    def test_same_version_normal_command_never_restarts(self):
        with patch.object(updater.sys, "argv", ["updater", "--version", "0.160.0"]), \
                patch.object(updater, "Updater", return_value=self.instance), \
                patch.object(self.instance, "status", return_value=self.state), \
                patch.object(self.instance, "prepare") as prepare, \
                patch.object(self.instance, "promote") as promote:
            updater.main()
        prepare.assert_not_called()
        promote.assert_not_called()


if __name__ == "__main__":
    unittest.main()
