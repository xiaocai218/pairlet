import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("stack", Path(__file__).resolve().parents[1] / "pairlet_stack.py")
stack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stack)


class StackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.runtime = self.home / "runtime"
        self.runtime.mkdir()
        (self.runtime / "file").write_text("original")

    def test_hash_detects_content_and_mode_changes(self):
        first = stack.tree_digest(self.runtime)
        (self.runtime / "file").write_text("modified")
        self.assertNotEqual(first, stack.tree_digest(self.runtime))
        second = stack.tree_digest(self.runtime)
        (self.runtime / "file").chmod(0o700)
        self.assertNotEqual(second, stack.tree_digest(self.runtime))

    def test_symlinks_rejected(self):
        (self.runtime / "link").symlink_to("file")
        with self.assertRaisesRegex(RuntimeError, "symlinks"):
            stack.tree_digest(self.runtime)

    def test_override_preserves_arguments_and_supports_second_upgrade(self):
        unit = "[Service]\nExecStart=/old/bin/cc-pocket-daemon run --relay wss://nas.xiaocai218.top --codex-bin /canonical/codex.js\n"
        first = stack.unit_override(unit, Path("/old"), Path("/new"))
        self.assertIn("--codex-bin /canonical/codex.js", first)
        second = stack.unit_override(first, Path("/new"), Path("/next"))
        self.assertIn("ExecStart=/next/bin/cc-pocket-daemon run", second)

    def test_unknown_service_command_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Unknown service"):
            stack.unit_override("ExecStart=/other/server run", Path("/old"), Path("/new"))

    def test_paired_state_backup_is_private(self):
        source = self.home / ".local/share/cc-pocket-selfhost"
        source.mkdir(parents=True)
        (source / "devices.json").write_text("{}")
        backup = self.home / "backup"
        stack.backup_state(self.home, backup)
        self.assertEqual((backup / "devices.json").read_text(), "{}")
        self.assertEqual((backup / "devices.json").stat().st_mode & 0o777, 0o600)

    def test_changed_staged_runtime_rejected(self):
        record = dict(schema=1, state="ready-for-local-switch", codex=str(self.runtime), daemon=str(self.runtime),
                      codexSha256=stack.tree_digest(self.runtime), daemonSha256=stack.tree_digest(self.runtime))
        stack.validate(record)
        (self.runtime / "file").write_text("tampered")
        with self.assertRaisesRegex(RuntimeError, "changed"):
            stack.validate(record)

    def test_missing_client_delivery_blocks_assembly(self):
        candidate = self.home / "candidate"
        candidate.mkdir()
        (candidate / "candidate.json").write_text(json.dumps(dict(state="daemon-built")))
        clients = self.home / "clients.json"
        clients.write_text(json.dumps(dict(version="2.2.0")))
        with self.assertRaisesRegex(RuntimeError, "Verified NAS delivery"):
            stack.assemble(self.home, self.runtime, candidate, clients)


if __name__ == "__main__":
    unittest.main()
