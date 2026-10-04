import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("candidate", Path(__file__).resolve().parents[1] / "prepare-pairlet-candidate.py")
candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(candidate)


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        candidate.command(["git", "init", "--quiet", str(self.repository)])
        pairing = self.repository / candidate.PAIRING
        pairing.parent.mkdir(parents=True)
        pairing.write_text('const val DEFAULT_RELAY = "wss://nas.xiaocai218.top"\n')
        (self.repository / "fixture").write_text("before\n")
        candidate.command(["git", "add", "."], cwd=self.repository)
        candidate.command(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                           "commit", "--quiet", "-m", "fixture"], cwd=self.repository)
        self.patch = self.root / "change.patch"
        self.patch.write_text("--- a/fixture\n+++ b/fixture\n@@ -1 +1 @@\n-before\n+after\n")

    def test_patch_applies_and_is_idempotent(self):
        self.assertEqual(candidate.apply_patch(self.repository, self.patch), "applied")
        self.assertEqual(candidate.apply_patch(self.repository, self.patch), "already-present")

    def test_conflict_does_not_overwrite(self):
        (self.repository / "fixture").write_text("user change\n")
        with self.assertRaisesRegex(RuntimeError, "Patch conflict"):
            candidate.apply_patch(self.repository, self.patch)
        self.assertEqual((self.repository / "fixture").read_text(), "user change\n")

    def test_isolated_prepare_leaves_dirty_source_untouched(self):
        patches = self.root / "patches"
        patches.mkdir()
        for name in candidate.PATCHES:
            (patches / name).write_bytes(self.patch.read_bytes())
        (self.repository / "fixture").write_text("uncommitted user change\n")
        prepared = candidate.prepare(str(self.repository), "HEAD", self.root / "candidates", patches,
                                     self.root / "no-jdk", source_only=True)
        record = json.loads((prepared / "candidate.json").read_text())
        self.assertEqual(record["state"], "source-prepared")
        self.assertFalse(record["runtimeSwitch"])
        self.assertEqual((prepared / "source/fixture").read_text(), "after\n")
        self.assertEqual((self.repository / "fixture").read_text(), "uncommitted user change\n")

    def test_failure_is_recorded(self):
        patches = self.root / "patches"
        patches.mkdir()
        for name in candidate.PATCHES:
            (patches / name).write_text("invalid patch\n")
        with self.assertRaisesRegex(RuntimeError, "Patch conflict"):
            candidate.prepare(str(self.repository), "HEAD", self.root / "candidates", patches,
                              self.root / "no-jdk", source_only=True)
        records = list((self.root / "candidates").glob("*/candidate.json"))
        self.assertEqual(len(records), 1)
        self.assertEqual(json.loads(records[0].read_text())["state"], "failed")

    def test_option_like_ref_is_rejected(self):
        with self.assertRaises(ValueError):
            candidate.prepare(str(self.repository), "--help", self.root / "candidates", self.root,
                              self.root, source_only=True)


if __name__ == "__main__":
    unittest.main()
