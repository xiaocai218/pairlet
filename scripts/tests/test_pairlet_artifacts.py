import importlib.util
from pathlib import Path
import tempfile
import unittest
import urllib.request


SPEC = importlib.util.spec_from_file_location("artifacts", Path(__file__).resolve().parents[1] / "pairlet-artifacts.py")
artifacts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(artifacts)


class ArtifactTests(unittest.TestCase):
    def test_redirect_does_not_forward_token(self):
        request = urllib.request.Request("https://api.github.com/artifact", headers={"Authorization": "Bearer fixture"})
        redirected = artifacts.SafeRedirect().redirect_request(request, None, 302, "Found", {}, "https://example.invalid/signed")
        self.assertFalse(redirected.has_header("Authorization"))

    def test_msi_requires_exact_commit_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "test.msi"
            path.write_bytes(b"fixture")
            record = dict(file=path.name, sourceCommit="a" * 40, version="2.2.0", runId="123",
                          size=path.stat().st_size, sha256=artifacts.sha(path))
            artifacts.verify_msi(root, record, "a" * 40, "2.2.0", 123)
            with self.assertRaisesRegex(RuntimeError, "source/version"):
                artifacts.verify_msi(root, record, "b" * 40, "2.2.0", 123)
            path.write_bytes(b"modified")
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                artifacts.verify_msi(root, record, "a" * 40, "2.2.0", 123)

    def test_msi_path_traversal_rejected(self):
        record = dict(file="../test.msi", sourceCommit="a" * 40, version="2.2.0", runId=123)
        with self.assertRaisesRegex(RuntimeError, "Unsafe"):
            artifacts.verify_msi(Path("/tmp"), record, "a" * 40, "2.2.0", 123)


if __name__ == "__main__":
    unittest.main()
