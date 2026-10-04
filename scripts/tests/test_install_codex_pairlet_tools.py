import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("installer", SCRIPTS / "install-codex-pairlet-tools.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "home"
        self.source = Path(self.temporary.name) / "source"
        self.source.mkdir()
        for name in (*installer.FILES, installer.LAUNCHER):
            (self.source / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SCRIPTS / name, self.source / name)

    def test_launcher_works_without_original_source(self):
        release = installer.install(self.source, self.home)
        self.source.rename(self.source.with_name("unavailable-source"))
        result = subprocess.run([str(self.home / "bin/update-codex-pairlet"), "--help"],
                                env=dict(os.environ, HOME=str(self.home)), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--prepare-only", result.stdout)
        manifest = json.loads((release / "manifest.json").read_text())
        for name in installer.FILES:
            self.assertEqual(installer.digest(release / name), manifest["sha256"][name])
        self.assertNotIn(str(self.source), (self.home / "bin/update-codex-pairlet").read_text())

    def test_install_is_idempotent(self):
        first = installer.install(self.source, self.home)
        second = installer.install(self.source, self.home)
        self.assertEqual(first, second)
        self.assertEqual((first.parent.parent / "current").resolve(), first)

    def test_modified_bundle_is_not_overwritten(self):
        release = installer.install(self.source, self.home)
        (release / installer.FILES[0]).write_text("modified")
        with self.assertRaisesRegex(RuntimeError, "modified"):
            installer.install(self.source, self.home)
        self.assertEqual((release / installer.FILES[0]).read_text(), "modified")

    def test_previous_launcher_is_backed_up(self):
        binary = self.home / "bin/update-codex-pairlet"
        binary.parent.mkdir(parents=True)
        binary.write_text("original launcher")
        release = installer.install(self.source, self.home)
        backups = list(release.parent.parent.glob("launcher.backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "original launcher")


if __name__ == "__main__":
    unittest.main()
