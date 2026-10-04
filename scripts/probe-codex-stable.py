#!/usr/bin/env python3
"""Read-only terminal, interrupt and process-restart regression probe for stable mode."""
import importlib.util
import json
import os
from pathlib import Path
import queue
import shutil
import sys
import tempfile
import time


module_spec = importlib.util.spec_from_file_location(
    "codex_wire", Path(__file__).with_name("probe-codex-wire.py")
)
wire = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(wire)

STABLE_ARGS = [
    "--disable", "multi_agent",
    "--disable", "multi_agent_v2",
    "--enable", "shell_tool",
    "--enable", "unified_exec",
]


def request(server, method, params):
    _, response = server.request(method, params)
    if response is None or "error" in response:
        raise RuntimeError("%s rejected or timed out; inspect private wire log" % method)
    return response["result"]


def connect(workdir, log, thread_id=None):
    server = wire.Server(str(workdir), log, args=STABLE_ARGS)
    try:
        request(server, "initialize", {
            "clientInfo": {"name": "pairlet-stable-probe", "version": "1"},
            "capabilities": {"experimentalApi": False},
        })
        server.notify("initialized")
        params = {"cwd": str(workdir), "approvalPolicy": "never", "sandbox": "read-only"}
        if thread_id:
            params["threadId"] = thread_id
        response = request(server, "thread/resume" if thread_id else "thread/start", params)
        thread = response["thread"]
        if thread_id and thread["id"] != thread_id:
            raise RuntimeError("resume returned a different thread")
        return server, thread
    except Exception:
        server.kill()
        raise


def run_turn(server, thread_id, workdir, marker, interrupt=False):
    command = "printf '%s\\n'" % marker
    if interrupt:
        command += "; sleep 30"
    response = request(server, "turn/start", {
        "threadId": thread_id,
        "input": [{"type": "text", "text": (
            "Use the terminal tool to run exactly this read-only shell command: " + command
            + ". Do not create files or delegate. After the command finishes, reply briefly."
        )}],
        "cwd": str(workdir),
        "approvalPolicy": "never",
        "sandboxPolicy": {"type": "readOnly"},
    })
    turn_id = response["turn"]["id"]
    completed_commands = []
    output_seen = False
    interrupted = False
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            note = server.notes.get(timeout=1)
        except queue.Empty:
            continue
        params = note.get("params") or {}
        if params.get("threadId") != thread_id:
            continue
        if params.get("turnId") and params["turnId"] != turn_id:
            continue
        method = note.get("method")
        if method == "item/commandExecution/outputDelta" and marker in params.get("delta", ""):
            output_seen = True
        if method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "commandExecution":
                completed_commands.append(item)
                if marker in item.get("aggregatedOutput", ""):
                    output_seen = True
        if interrupt and output_seen and not interrupted:
            request(server, "turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
            interrupted = True
        if method == "turn/completed" and (params.get("turn") or {}).get("id") == turn_id:
            status = params["turn"].get("status")
            if interrupt:
                if not interrupted or status != "interrupted":
                    raise RuntimeError("running terminal turn was not interrupted")
            elif status != "completed" or not any(
                item.get("exitCode") == 0 and marker in item.get("aggregatedOutput", "")
                for item in completed_commands
            ):
                raise RuntimeError("turn finished without successful terminal output")
            print("PASS %s: %s" % (marker, status), flush=True)
            return
    raise RuntimeError("terminal turn timed out")


def close(server):
    if server.close_input() != 0:
        raise RuntimeError("app-server did not release the session cleanly")


def audit_rollout(path):
    names = set()
    with open(path, encoding="utf-8") as source:
        for line in source:
            entry = json.loads(line)
            payload = entry.get("payload") or {}
            if entry.get("type") == "response_item" and payload.get("type") in (
                "function_call", "custom_tool_call"
            ):
                names.add(payload.get("name", ""))
    forbidden = {"send_message", "spawn_agent", "list_agents", "followup_task"}
    if any(name.rsplit(".", 1)[-1] in forbidden for name in names):
        raise RuntimeError("stable thread still invoked delegation tools")
    if not any(name.rsplit(".", 1)[-1] in {"exec", "exec_command"} for name in names):
        raise RuntimeError("terminal execution tool was not observed")
    print("PASS execution tools without delegation: " + ", ".join(sorted(names)), flush=True)


def main():
    wire.CODEX = shutil.which(wire.CODEX) if wire.CODEX else None
    if not wire.CODEX:
        print("Codex executable not found", file=sys.stderr)
        return 2
    workdir = Path(tempfile.mkdtemp(prefix="codex-stable-", dir=os.getenv("PAIRLET_PROBE_DIR")))
    log_path = workdir / "wire.jsonl"
    server = None
    with open(log_path, "w", encoding="utf-8") as log:
        log_path.chmod(0o600)
        try:
            server, thread = connect(workdir, log)
            thread_id = thread["id"]
            run_turn(server, thread_id, workdir, "PAIRLET_FIRST_OK")
            run_turn(server, thread_id, workdir, "PAIRLET_INTERRUPT_READY", interrupt=True)
            close(server)
            server, thread = connect(workdir, log, thread_id)
            run_turn(server, thread_id, workdir, "PAIRLET_RESUME_OK")
            close(server)
            server, thread = connect(workdir, log, thread_id)
            run_turn(server, thread_id, workdir, "PAIRLET_SECOND_RESUME_OK")
            close(server)
            audit_rollout(thread["path"])
            print("PASS interrupted session recovered across two process restarts", flush=True)
            return 0
        except Exception as error:
            print("FAIL %s" % error, file=sys.stderr, flush=True)
            print("Private wire log: %s" % log_path, file=sys.stderr, flush=True)
            return 1
        finally:
            if server is not None:
                server.kill()


if __name__ == "__main__":
    sys.exit(main())
