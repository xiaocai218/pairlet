#!/usr/bin/env python3
"""Stage and validate Codex before switching the local self-hosted Pairlet runtime."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import zipfile


SERVICE = "cc-pocket-daemon.service"
SCRIPTS = Path(__file__).resolve().parent


def run(*args, env=None, timeout=180):
    result = subprocess.run(args, text=True, capture_output=True, env=env, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Command failed: {args[0]} (exit {result.returncode})")
    return result.stdout.strip()


def version(binary):
    output = run(str(binary), "--version")
    match = re.fullmatch(r"codex-cli (\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?)", output)
    if not match:
        raise RuntimeError("Unrecognized Codex version")
    return match.group(1)


def validate_version(value):
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", value):
        raise ValueError("Expected an exact Codex version")
    return value


def atomic_json(path, data):
    temporary = path.with_suffix(".new")
    with temporary.open("w") as output:
        temporary.chmod(0o600)
        output.write(json.dumps(data, indent=2) + "\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


class Updater:
    def __init__(self, home):
        self.home = home
        self.root = home / ".local/opt/codex-pairlet"
        self.package = home / ".local/lib/node_modules/@openai/codex"
        self.cli = self.package / "bin/codex.js"
        self.journal = self.root / "pending.json"

    def status(self):
        state = json.loads(run(str(self.cli), "app-server", "daemon", "version"))
        current = version(self.cli)
        if state.get("cliVersion") != current:
            raise RuntimeError("CLI version inspection disagrees")
        if state.get("managedCodexVersion") != current:
            raise RuntimeError("CLI and managed app-server differ; resolve drift before upgrading")
        if state.get("status") == "running" and state.get("appServerVersion") != current:
            raise RuntimeError("Running app-server differs from CLI")
        unit = run("systemctl", "--user", "show", SERVICE, "-p", "MainPID", "-p", "Environment", "-p", "ExecStart")
        if "CC_POCKET_CODEX_STABLE_MODE=1" not in unit or str(self.cli) not in unit:
            raise RuntimeError("Pairlet stable mode or canonical Codex path is missing")
        match = re.search(r"path=([^ ;]+/bin/cc-pocket-daemon)", unit)
        if not match:
            raise RuntimeError("Unknown Pairlet installation")
        installation = Path(match.group(1)).parent.parent
        jars = list((installation / "lib").glob("daemon-*.jar"))
        if len(jars) != 1:
            raise RuntimeError("Cannot identify installed Pairlet daemon JAR")
        with zipfile.ZipFile(jars[0]) as archive:
            launcher = archive.read("dev/ccpocket/daemon/codex/CodexLauncher.class")
        for marker in (b"CC_POCKET_CODEX_STABLE_MODE", b"multi_agent_v2", b"unified_exec"):
            if marker not in launcher:
                raise RuntimeError("Installed Pairlet lacks the stable-mode launcher patch")
        if b"code_mode_host" in launcher:
            raise RuntimeError("Launcher modifies code-mode host; requires compatibility review")
        main_pid = int(re.search(r"^MainPID=(\d+)$", unit, re.MULTILINE).group(1))
        if main_pid:
            environment = Path(f"/proc/{main_pid}/environ").read_bytes().split(b"\0")
            if b"CC_POCKET_CODEX_STABLE_MODE=1" not in environment:
                raise RuntimeError("Running Pairlet has not activated stable mode")
        return dict(version=current, managed=state, pairletPid=main_pid,
                    pairletInstallation=str(installation))

    def idle(self, state):
        if state["managed"].get("status") != "stopped":
            raise RuntimeError("Close Agent sessions, then run: codex app-server daemon stop. Running or unknown managed state blocks upgrade")
        blocked = []
        for process in Path("/proc").iterdir():
            if not process.name.isdigit():
                continue
            try:
                if process.stat().st_uid != os.getuid():
                    continue
                arguments = (process / "cmdline").read_bytes().split(b"\0")
                executable = str((process / "exe").resolve())
                parent = int((process / "stat").read_text().rsplit(")", 1)[1].split()[1])
            except FileNotFoundError:
                continue
            codex = Path(executable).name == "codex" or any(argument.endswith(b"/codex.js") for argument in arguments)
            if codex or (state["pairletPid"] and parent == state["pairletPid"]):
                blocked.append(int(process.name))
        if blocked:
            raise RuntimeError(f"Close Agent sessions first (process IDs: {sorted(blocked)})")

    def prepare(self, target):
        directory = self.root / "versions" / validate_version(target)
        if not directory.exists():
            directory.mkdir(parents=True, mode=0o700)
            run("npm", "install", "--prefix", str(directory), "--no-audit", "--no-fund", f"@openai/codex@{target}")
        candidate = directory / "node_modules/@openai/codex"
        binary = candidate / "bin/codex.js"
        if version(binary) != target:
            raise RuntimeError("Candidate version differs from requested version")
        diagnostics = directory / "diagnostics"
        diagnostics.mkdir(mode=0o700, exist_ok=True)
        environment = dict(os.environ, CC_POCKET_CODEX_BIN=str(binary),
                           PAIRLET_PROBE_DIR=str(diagnostics), TMPDIR=str(diagnostics))
        for name in ("probe-codex-wire.py", "probe-codex-stable.py"):
            print(f"Validating {name}; private logs: {diagnostics}", flush=True)
            with (diagnostics / (name + ".log")).open("w") as output:
                result = subprocess.run([sys.executable, str(SCRIPTS / name)], env=environment,
                                        stdout=output, stderr=subprocess.STDOUT, timeout=900)
            if result.returncode:
                raise RuntimeError(f"{name} failed; current runtime was not changed")
        atomic_json(directory / "validated.json", dict(version=target))
        return candidate

    def promote(self, candidate, state):
        if self.journal.exists():
            raise RuntimeError("An interrupted upgrade requires --rollback first")
        if version(candidate / "bin/codex.js") != candidate.parent.parent.parent.name:
            raise RuntimeError("Unexpected staged package layout")
        self.idle(state)
        backup = self.package.with_name(f"codex.backup-{time.time_ns()}")
        record = dict(backup=str(backup), oldVersion=state["version"],
                      managedRunning=state["managed"].get("status") == "running",
                      pairletRunning=bool(state["pairletPid"]))
        atomic_json(self.journal, record)
        try:
            if record["pairletRunning"]:
                run("systemctl", "--user", "stop", SERVICE)
            self.idle(dict(state, pairletPid=0))
            if record["managedRunning"]:
                run(str(self.cli), "app-server", "daemon", "stop")
            os.rename(self.package, backup)
            self.package.symlink_to(candidate, target_is_directory=True)
            run(str(self.cli), "app-server", "daemon", "update", "--from-cli", "--yes")
            if record["managedRunning"]:
                run(str(self.cli), "app-server", "daemon", "start")
            else:
                run(str(self.cli), "app-server", "daemon", "stop")
            if record["pairletRunning"]:
                run("systemctl", "--user", "start", SERVICE)
            updated = self.status()
            if updated["version"] != version(candidate / "bin/codex.js"):
                raise RuntimeError("Post-switch version check failed")
            if record["pairletRunning"] and not updated["pairletPid"]:
                raise RuntimeError("Pairlet did not restart")
            atomic_json(self.root / "last-good.json", dict(updated, backup=str(backup)))
            self.journal.unlink()
            print(f"Upgraded Codex and managed app-server to {updated['version']}; Pairlet patch retained")
        except BaseException:
            self.rollback()
            raise

    def rollback(self):
        record = json.loads(self.journal.read_text())
        backup = Path(record["backup"])
        if backup.exists():
            run("systemctl", "--user", "stop", SERVICE)
            rollback_cli = self.cli if self.cli.exists() else backup / "bin/codex.js"
            run(str(rollback_cli), "app-server", "daemon", "stop")
            if self.package.exists() and not self.package.is_symlink():
                raise RuntimeError("Canonical package changed externally; refusing overwrite")
            if self.package.is_symlink():
                self.package.unlink()
            os.rename(backup, self.package)
            run(str(self.cli), "app-server", "daemon", "update", "--from-cli", "--yes")
        if record["managedRunning"]:
            run(str(self.cli), "app-server", "daemon", "start")
        else:
            run(str(self.cli), "app-server", "daemon", "stop")
        if record["pairletRunning"]:
            run("systemctl", "--user", "start", SERVICE)
        if self.status()["version"] != record["oldVersion"]:
            raise RuntimeError("Rollback verification failed; pending journal retained")
        self.journal.unlink()
        print("Previous runtime restored")


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--prepare-only", action="store_true")
    mode.add_argument("--prepare-stack", action="store_true")
    mode.add_argument("--rollback", action="store_true")
    parser.add_argument("--version", type=validate_version)
    parser.add_argument("--pairlet-ref", default="main")
    args = parser.parse_args()
    updater = Updater(Path.home())
    if args.check:
        print(json.dumps(updater.status(), indent=2))
        if updater.journal.exists():
            raise RuntimeError("Interrupted upgrade: inspect pending.json before --rollback")
        return
    updater.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (updater.root / "upgrade.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.rollback:
            record = json.loads(updater.journal.read_text())
            binary = updater.cli if updater.cli.exists() else Path(record["backup"]) / "bin/codex.js"
            managed = json.loads(run(str(binary), "app-server", "daemon", "version"))
            main_pid = int(run("systemctl", "--user", "show", SERVICE, "-p", "MainPID", "--value"))
            updater.idle(dict(managed=managed, pairletPid=main_pid))
            updater.rollback()
            return
        if updater.journal.exists():
            raise RuntimeError("Interrupted upgrade: inspect pending.json before --rollback")
        state = updater.status()
        target = args.version or validate_version(json.loads(run("npm", "view", "@openai/codex", "version", "--json")))
        if target == state["version"] and not (args.prepare_only or args.prepare_stack):
            print(f"Already consistent at Codex {target}; no restart")
            return
        if not (args.prepare_only or args.prepare_stack):
            updater.idle(state)
        candidate = updater.prepare(target)
        if args.prepare_stack:
            print(run(sys.executable, str(Path(__file__).resolve().parent / "prepare-pairlet-candidate.py"),
                      "--ref", args.pairlet_ref, timeout=2100))
            print(f"Codex candidate: {candidate}; no runtime switch. Client delivery and final approval remain required.")
            return
        if args.prepare_only:
            print("Candidate validated; no runtime switch. Run the command again to apply.")
            return
        updater.promote(candidate, updater.status())


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print(f"Upgrade stopped: {error}", file=sys.stderr)
        sys.exit(1)
