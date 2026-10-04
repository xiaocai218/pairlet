#!/usr/bin/env python3
"""Prepare an isolated patched Pairlet candidate; never switch a live service."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


PATCHES = ("selfhost-relay.patch", "codex-stable.patch")
PAIRING = "mobile/composeApp/src/commonMain/kotlin/dev/ccpocket/app/pairing/Pairing.kt"


def command(arguments, cwd=None, env=None, timeout=180):
    result = subprocess.run(arguments, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {arguments[0]} {arguments[1]}")
    return result.stdout.strip()


def apply_patch(source, patch):
    check = subprocess.run(["git", "apply", "--check", str(patch)], cwd=source, capture_output=True)
    if check.returncode == 0:
        command(["git", "apply", str(patch)], cwd=source)
        return "applied"
    reverse = subprocess.run(["git", "apply", "--reverse", "--check", str(patch)], cwd=source, capture_output=True)
    if reverse.returncode == 0:
        return "already-present"
    raise RuntimeError(f"Patch conflict: {patch.name}; candidate retained, live checkout unchanged")


def write_manifest(path, value):
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(temporary, path)


def prepare(repository, ref, root, patches, java_home, source_only=False):
    if not ref or ref.startswith("-") or not repository or str(repository).startswith("-"):
        raise ValueError("Invalid source/ref")
    if not source_only and not (java_home / "bin/java").is_file():
        raise RuntimeError("An explicit JDK 17 JAVA_HOME is required")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    candidate = Path(tempfile.mkdtemp(prefix="pairlet-", dir=root))
    source = candidate / "source"
    record = dict(state="preparing", ref=ref, patches={}, clients="not-built", runtimeSwitch=False)
    manifest = candidate / "candidate.json"
    write_manifest(manifest, record)
    try:
        command(["git", "init", "--quiet", str(source)])
        command(["git", "-C", str(source), "fetch", "--depth=1", "--no-tags", str(repository), ref], timeout=300)
        commit = command(["git", "-C", str(source), "rev-parse", "FETCH_HEAD"])
        command(["git", "-C", str(source), "checkout", "--detach", commit])
        record["sourceCommit"] = commit
        for name in PATCHES:
            patch = patches / name
            record["patches"][name] = dict(sha256=hashlib.sha256(patch.read_bytes()).hexdigest(),
                                          result=apply_patch(source, patch))
        pairing = source / PAIRING
        if 'const val DEFAULT_RELAY = "wss://nas.xiaocai218.top"' not in pairing.read_text():
            raise RuntimeError("Self-hosted relay verification failed")
        contract = source / "packaging/brand-compatibility.json"
        if contract.exists():
            value = json.loads(contract.read_text())
            for entry in value["frozenFiles"]:
                if entry["path"] == PAIRING:
                    entry["sha256"] = hashlib.sha256(pairing.read_bytes()).hexdigest()
            contract.write_text(json.dumps(value, indent=2) + "\n")
            command(["python3", "scripts/check-brand-compatibility.py"], cwd=source)
        record["state"] = "source-prepared"
        write_manifest(manifest, record)
        if not source_only:
            java_version = subprocess.run([str(java_home / "bin/java"), "-version"], capture_output=True, text=True)
            if java_version.returncode or 'version "17.' not in java_version.stderr:
                raise RuntimeError("Candidate build requires JDK 17")
            environment = dict(os.environ, JAVA_HOME=str(java_home))
            arguments = ["bash", "./gradlew", ":daemon:test", "--tests", "dev.ccpocket.daemon.codex.*",
                         ":daemon:installDist", f"-Dorg.gradle.java.installations.paths={java_home}"]
            with (candidate / "build.log").open("w") as log:
                result = subprocess.run(arguments, cwd=source, env=environment, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=1800)
            if result.returncode:
                raise RuntimeError(f"Candidate build failed: inspect {candidate / 'build.log'}")
            jars = list((source / "daemon/build/install/cc-pocket-daemon/lib").glob("daemon-*.jar"))
            if len(jars) != 1:
                raise RuntimeError("Candidate daemon artifact missing or ambiguous")
            record.update(state="daemon-built", daemonSha256=hashlib.sha256(jars[0].read_bytes()).hexdigest())
        write_manifest(manifest, record)
        return candidate
    except BaseException as error:
        record.update(state="failed", error=str(error))
        write_manifest(manifest, record)
        raise


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="https://github.com/heypandax/pairlet.git")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--root", type=Path, default=Path.home() / ".local/opt/codex-pairlet/candidates")
    parser.add_argument("--java-home", type=Path, default=Path.home() / ".local/opt/cc-pocket/jdk17")
    parser.add_argument("--source-only", action="store_true")
    args = parser.parse_args()
    candidate = prepare(args.repository, args.ref, args.root, Path(__file__).resolve().parent / "patches",
                        args.java_home, args.source_only)
    print(f"Pairlet candidate: {candidate}")
    print("Clients not built or delivered; no runtime switch")


if __name__ == "__main__":
    main()
