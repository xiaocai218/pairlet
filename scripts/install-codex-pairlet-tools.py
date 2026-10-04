#!/usr/bin/env python3
"""Install a self-contained, content-addressed maintenance bundle, without restarting services."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import time


FILES = ("update-codex-pairlet.py", "probe-codex-wire.py", "probe-codex-stable.py",
         "prepare-pairlet-candidate.py", "patches/selfhost-relay.patch", "patches/codex-stable.patch")
LAUNCHER = "update-codex-pairlet-launcher.py"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install(source, home):
    hashes = {name: digest(source / name) for name in (*FILES, LAUNCHER)}
    release_id = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:20]
    root = home / ".local/opt/codex-pairlet/tools"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    release = root / "releases" / release_id
    release.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in FILES:
        destination = release / name
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists() and digest(destination) != hashes[name]:
            raise RuntimeError(f"Installed release was modified: {name}; refusing overwrite")
        if not destination.exists():
            shutil.copy2(source / name, destination)
            destination.chmod(0o700)
        if digest(destination) != hashes[name]:
            raise RuntimeError(f"Install verification failed: {name}")
    manifest = release / "manifest.json"
    manifest.write_text(json.dumps(dict(release=release_id, sha256=hashes), indent=2) + "\n")
    manifest.chmod(0o600)
    temporary = root / f"current.new-{os.getpid()}"
    temporary.symlink_to(Path("releases") / release_id, target_is_directory=True)
    os.replace(temporary, root / "current")
    binary = home / "bin/update-codex-pairlet"
    binary.parent.mkdir(parents=True, exist_ok=True)
    if binary.exists() and digest(binary) != hashes[LAUNCHER]:
        shutil.copy2(binary, root / f"launcher.backup-{time.time_ns()}")
    temporary_binary = binary.with_name(f"update-codex-pairlet.new-{os.getpid()}")
    shutil.copy2(source / LAUNCHER, temporary_binary)
    temporary_binary.chmod(0o700)
    os.replace(temporary_binary, binary)
    if digest(binary) != hashes[LAUNCHER]:
        raise RuntimeError("Launcher verification failed")
    return release


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home())
    args = parser.parse_args()
    release = install(Path(__file__).resolve().parent, args.home)
    print(f"Installed independent maintenance tools: {release}")
    print("No packages or services were upgraded/restarted")


if __name__ == "__main__":
    main()
