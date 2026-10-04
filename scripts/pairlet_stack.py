"""Content-addressed local runtime combinations and reversible systemd overrides."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


DROPIN = "90-codex-pairlet-stack.conf"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tree_digest(root):
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError("Runtime bundle must not contain symlinks")
        if path.is_file():
            entries.append((str(path.relative_to(root)), path.stat().st_mode & 0o777, digest(path)))
    if not entries:
        raise RuntimeError("Empty runtime bundle")
    return hashlib.sha256(json.dumps(entries).encode()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def assemble(home, codex, pairlet_candidate, clients_file):
    candidate = json.loads((pairlet_candidate / "candidate.json").read_text())
    clients = json.loads(clients_file.read_text())
    if candidate["state"] != "daemon-built":
        raise RuntimeError("Pairlet candidate was not built")
    for name in ("android", "windows"):
        item = clients.get(name, {})
        delivery = item.get("delivery", {})
        if not delivery.get("verified") or delivery.get("sha256") != item.get("sha256") or delivery.get("size") != item.get("size"):
            raise RuntimeError(f"Verified NAS delivery required: {name}")
        path = clients_file.parent / item["file"]
        if path.parent != clients_file.parent or digest(path) != item["sha256"] or path.stat().st_size != item["size"]:
            raise RuntimeError("Client artifact changed")
    if clients["windows"]["sourceCommit"] != candidate["sourceCommit"]:
        raise RuntimeError("MSI and daemon must share the same commit")
    if clients["android"]["version"] != clients["windows"]["version"]:
        raise RuntimeError("Client versions differ")
    source = pairlet_candidate / "source"
    result = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=source,
                            capture_output=True, text=True, check=True)
    if result.stdout.strip():
        raise RuntimeError("Daemon source must be a clean committed selfhost build")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source, capture_output=True, text=True, check=True).stdout.strip()
    if head != candidate["sourceCommit"]:
        raise RuntimeError("Daemon commit changed after build")
    distribution = source / "daemon/build/install/cc-pocket-daemon"
    jars = list((distribution / "lib").glob("daemon-*.jar"))
    if len(jars) != 1 or digest(jars[0]) != candidate["daemonSha256"]:
        raise RuntimeError("Daemon artifact changed after build")
    root = home / ".local/opt/codex-pairlet"
    daemon_hash = tree_digest(distribution)
    installed = root / "runtimes" / daemon_hash / "cc-pocket-daemon"
    if installed.exists():
        if tree_digest(installed) != daemon_hash:
            raise RuntimeError("Existing staged runtime changed")
    else:
        installed.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copytree(distribution, installed)
    validated = codex.parent.parent.parent / "validated.json"
    validation = json.loads(validated.read_text())
    record = dict(schema=1, state="ready-for-local-switch", codex=str(codex.resolve()),
                  codexVersion=validation["version"], codexSha256=tree_digest(codex),
                  daemon=str(installed), daemonSha256=daemon_hash, pairletVersion=clients["version"],
                  sourceCommit=head, clients=clients, clientInstallation="not-verified",
                  mobileWorkflow="not-verified")
    identifier = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:20]
    path = root / "stacks" / (identifier + ".json")
    save(path, record)
    return path


def validate(record):
    if record.get("schema") != 1 or record.get("state") != "ready-for-local-switch":
        raise RuntimeError("Unknown stack state")
    if tree_digest(Path(record["codex"])) != record["codexSha256"]:
        raise RuntimeError("Staged Codex changed")
    if tree_digest(Path(record["daemon"])) != record["daemonSha256"]:
        raise RuntimeError("Staged daemon changed")


def unit_override(unit, previous, target):
    lines = [line for line in unit.splitlines() if line.startswith("ExecStart=") and line != "ExecStart="]
    if len(lines) != 1 or not lines[0].startswith("ExecStart=" + str(previous / "bin/cc-pocket-daemon") + " "):
        raise RuntimeError("Unknown service command; refusing override")
    updated = lines[0].replace(str(previous / "bin/cc-pocket-daemon"), str(target / "bin/cc-pocket-daemon"), 1)
    return "[Service]\nExecStart=\n" + updated + "\n"


def backup_state(home, destination):
    source = home / ".local/share/cc-pocket-selfhost"
    if not source.is_dir():
        raise RuntimeError("Expected paired-state directory missing; refusing live switch")
    shutil.copytree(source, destination)
    destination.chmod(0o700)
    for path in destination.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("Unexpected paired-state symlink")
        path.chmod(0o700 if path.is_dir() else 0o600)


def atomic_text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".new")
    temporary.write_text(value)
    temporary.chmod(0o600)
    os.replace(temporary, path)
