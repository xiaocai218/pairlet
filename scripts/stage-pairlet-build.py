#!/usr/bin/env python3
"""Copy only approved upgrade files into a detached, patched candidate; never commit or push implicitly."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess


FILES = (
    ".github/workflows/build-windows.yml",
    "docs/analysis/codex-pairlet-upgrade.md", "docs/analysis/codex-stable-mode.md",
    "scripts/update-codex-pairlet.py", "scripts/update-codex-pairlet-launcher.py",
    "scripts/install-codex-pairlet-tools.py", "scripts/prepare-pairlet-candidate.py",
    "scripts/probe-codex-wire.py", "scripts/probe-codex-stable.py",
    "scripts/pairlet-artifacts.py", "scripts/pairlet-git-askpass.py", "scripts/pairlet_stack.py",
    "scripts/test-record-windows-provenance.ps1", "scripts/tests/test_pairlet_stack.py",
    "scripts/tests/test_pairlet_artifacts.py",
    "scripts/record-windows-provenance.ps1", "scripts/stage-pairlet-build.py",
    "scripts/patches/codex-stable.patch", "scripts/patches/selfhost-relay.patch",
    "scripts/tests/test_update_codex_pairlet.py", "scripts/tests/test_install_codex_pairlet_tools.py",
    "scripts/tests/test_prepare_pairlet_candidate.py",
)


def stage(candidate, repository):
    record = json.loads((candidate / "candidate.json").read_text())
    if record["state"] != "daemon-built":
        raise RuntimeError("A successfully built isolated candidate is required")
    source = candidate / "source"
    if source.resolve() == repository.resolve():
        raise RuntimeError("Refusing original checkout")
    for name in FILES:
        destination = source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repository / name, destination)
    subprocess.run(["git", "diff", "--check"], cwd=source, check=True)
    subprocess.run(["python3", "scripts/check-brand-compatibility.py"], cwd=source, check=True)
    print(f"Staged approved files in {source}; no commit/push performed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    stage(args.candidate, Path(__file__).resolve().parents[1])
