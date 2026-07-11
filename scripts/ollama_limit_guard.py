#!/usr/bin/env python3
"""Pause and resume every Ollama-consuming job owned by the server user.

The guard freezes process groups with SIGSTOP so in-flight JSONL outputs and
resume state stay intact.  It deliberately leaves the Ollama daemon, SSH, tmux
server, and status dashboards running.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parent.parent
STATE_DIR = PROJECT_ROOT / "outputs" / "ollama_limit_guard_all"
STATE_PATH = STATE_DIR / "state.json"
EVENTS_PATH = STATE_DIR / "events.jsonl"
ARCHIVE_DIR = STATE_DIR / "archive"
AUTO_RESUME_LOG = STATE_DIR / "auto_resume.log"
AUTO_ENFORCE_LOG = STATE_DIR / "auto_enforce.log"

MARKER_RE = re.compile(
    r"(?:"
    r"\bollama\b|:cloud\b|\bqwen(?:2|3)?\b|\bgemma\b|\bllama\b|"
    r"\bminimax\b|\bdeepseek\b|\bmistral\b|\bgpt-oss\b|\bglm(?:-|\b)|"
    r"\bkimi\b|\bllm\b|policy_(?:pipeline|tool_refiner)|"
    r"stage[12].*(?:qwen|minimax|ollama)|workflow_orchestrator"
    r")",
    re.IGNORECASE,
)
SKIP_SESSION_RE = re.compile(r"(?:status_dashboard|ollama_limit_guard)", re.IGNORECASE)
SKIP_COMMAND_RE = re.compile(
    r"(?:/usr/local/bin/ollama\s+serve\b|\bollama\s+serve\b|"
    r"ollama_limit_guard\.py|domain_workflow_status_server\.py|status_server\.py)",
    re.IGNORECASE,
)


def run(
    args: list[str], *, check: bool = False, timeout: int = 30
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        if check:
            raise
        return subprocess.CompletedProcess(args=args, returncode=127, stdout="", stderr=str(exc))


def ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def append_event(event: str, **payload: Any) -> None:
    ensure_dirs()
    row = {
        "event": event,
        "epoch": time.time(),
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **payload,
    }
    with EVENTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_state() -> dict[str, Any] | None:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def proc_snapshot() -> dict[int, dict[str, Any]]:
    uid = os.getuid()
    rows: dict[int, dict[str, Any]] = {}
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return rows
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            raw_stat = (entry / "stat").read_text(encoding="utf-8")
            close = raw_stat.rfind(")")
            fields = raw_stat[close + 2 :].split()
            pid = int(entry.name)
            cmd_raw = (entry / "cmdline").read_bytes().replace(b"\0", b" ").strip()
            cmd = cmd_raw.decode("utf-8", errors="replace")
            if not cmd:
                cmd = raw_stat[raw_stat.find("(") + 1 : close]
            environ = (entry / "environ").read_bytes()
            rows[pid] = {
                "pid": pid,
                "state": fields[0],
                "ppid": int(fields[1]),
                "pgid": int(fields[2]),
                "sid": int(fields[3]),
                "start_ticks": int(fields[19]),
                "cmd": cmd,
                "env_marker": b"OLLAMA_" in environ or b":cloud" in environ,
            }
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError, IndexError):
            continue
    return rows


def descendant_map(snapshot: dict[int, dict[str, Any]]) -> dict[int, set[int]]:
    children: dict[int, list[int]] = defaultdict(list)
    for pid, row in snapshot.items():
        children[row["ppid"]].append(pid)
    result: dict[int, set[int]] = {}
    for root in snapshot:
        found: set[int] = set()
        queue: deque[int] = deque([root])
        while queue:
            current = queue.popleft()
            if current in found:
                continue
            found.add(current)
            queue.extend(children.get(current, []))
        result[root] = found
    return result


def tmux_panes() -> list[dict[str, Any]]:
    fmt = "#{session_name}\t#{pane_pid}\t#{pane_current_path}\t#{pane_start_command}"
    result = run(["tmux", "list-panes", "-a", "-F", fmt])
    if result.returncode != 0:
        return []
    panes: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        try:
            pane_pid = int(parts[1])
        except ValueError:
            continue
        panes.append(
            {
                "session": parts[0],
                "pane_pid": pane_pid,
                "cwd": parts[2],
                "start_command": parts[3],
            }
        )
    return panes


def active_local_ollama_pids() -> set[int]:
    result = run(["ss", "-Hntp"], timeout=15)
    if result.returncode != 0:
        return set()
    pids: set[int] = set()
    for line in result.stdout.splitlines():
        if ":11434" not in line:
            continue
        pids.update(int(value) for value in re.findall(r"pid=(\d+)", line))
    return pids


def ancestor_pids(snapshot: dict[int, dict[str, Any]], pid: int) -> set[int]:
    result: set[int] = set()
    current = pid
    while current in snapshot and current not in result:
        result.add(current)
        current = snapshot[current]["ppid"]
    return result


def discover_groups() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    snapshot = proc_snapshot()
    descendants = descendant_map(snapshot)
    connected = active_local_ollama_pids()
    panes = tmux_panes()
    own_ancestors = ancestor_pids(snapshot, os.getpid())
    own_pgid = os.getpgrp()

    selected: dict[int, dict[str, Any]] = {}
    session_manifests: dict[str, dict[str, Any]] = {}

    def add_group(pgid: int, reason: str, candidate_pids: set[int]) -> None:
        if pgid <= 1 or pgid == own_pgid:
            return
        members = [
            row
            for row in snapshot.values()
            if row["pgid"] == pgid and row["pid"] not in own_ancestors
        ]
        if not members:
            return
        commands = " | ".join(row["cmd"] for row in members)
        if SKIP_COMMAND_RE.search(commands) and not any(
            not SKIP_COMMAND_RE.search(row["cmd"]) for row in members
        ):
            return
        item = selected.setdefault(
            pgid,
            {
                "pgid": pgid,
                "reasons": [],
                "identities": [],
                "command_preview": commands[:1200],
                "stopped": False,
            },
        )
        if reason not in item["reasons"]:
            item["reasons"].append(reason)
        known = {entry["pid"] for entry in item["identities"]}
        for row in members:
            if row["pid"] in known:
                continue
            item["identities"].append(
                {
                    "pid": row["pid"],
                    "start_ticks": row["start_ticks"],
                    "cmd": row["cmd"][:500],
                }
            )

    for pane in panes:
        if SKIP_SESSION_RE.search(pane["session"]):
            continue
        pane_desc = descendants.get(pane["pane_pid"], {pane["pane_pid"]})
        text = " ".join(
            [pane["session"], pane["start_command"]]
            + [snapshot[pid]["cmd"] for pid in pane_desc if pid in snapshot]
        )
        env_marker = any(snapshot[pid]["env_marker"] for pid in pane_desc if pid in snapshot)
        connection_marker = bool(pane_desc & connected)
        if not (MARKER_RE.search(text) or env_marker or connection_marker):
            continue
        reasons = []
        if MARKER_RE.search(text):
            reasons.append("command_or_session_marker")
        if env_marker:
            reasons.append("ollama_environment")
        if connection_marker:
            reasons.append("active_local_ollama_connection")
        pgids = {snapshot[pid]["pgid"] for pid in pane_desc if pid in snapshot}
        for pgid in pgids:
            for reason in reasons:
                add_group(pgid, f"tmux:{pane['session']}:{reason}", pane_desc)
        session_manifests[pane["session"]] = {
            "session": pane["session"],
            "cwd": pane["cwd"],
            "start_command": pane["start_command"],
        }

    pane_descendants = set().union(
        *(descendants.get(pane["pane_pid"], set()) for pane in panes)
    ) if panes else set()
    for pid, row in snapshot.items():
        if pid in own_ancestors or pid in pane_descendants:
            continue
        if SKIP_COMMAND_RE.search(row["cmd"]):
            continue
        matched = MARKER_RE.search(row["cmd"]) or row["env_marker"] or pid in connected
        if matched:
            add_group(row["pgid"], "non_tmux_ollama_consumer", {pid})

    return sorted(selected.values(), key=lambda item: item["pgid"]), list(
        session_manifests.values()
    )


def identity_is_alive(group: dict[str, Any]) -> bool:
    snapshot = proc_snapshot()
    pgid = int(group["pgid"])
    for identity in group.get("identities", []):
        row = snapshot.get(int(identity["pid"]))
        if (
            row
            and row["pgid"] == pgid
            and row["start_ticks"] == int(identity["start_ticks"])
        ):
            return True
    return False


def stop_group(group: dict[str, Any]) -> tuple[bool, str]:
    pgid = int(group["pgid"])
    if pgid == os.getpgrp():
        return False, "refused_to_stop_guard_group"
    try:
        os.killpg(pgid, signal.SIGSTOP)
        return True, "stopped"
    except ProcessLookupError:
        return False, "already_exited"
    except PermissionError:
        return False, "permission_denied"


def continue_group(group: dict[str, Any]) -> tuple[bool, str]:
    pgid = int(group["pgid"])
    if not identity_is_alive(group):
        return False, "original_group_not_alive"
    try:
        os.killpg(pgid, signal.SIGCONT)
        return True, "continued"
    except ProcessLookupError:
        return False, "already_exited"
    except PermissionError:
        return False, "permission_denied"


def merge_groups(
    existing: list[dict[str, Any]], discovered: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged = {int(item["pgid"]): item for item in existing}
    for item in discovered:
        pgid = int(item["pgid"])
        if pgid not in merged:
            merged[pgid] = item
            continue
        target = merged[pgid]
        target["reasons"] = sorted(set(target.get("reasons", [])) | set(item["reasons"]))
        known = {(entry["pid"], entry["start_ticks"]) for entry in target["identities"]}
        target["identities"].extend(
            entry
            for entry in item["identities"]
            if (entry["pid"], entry["start_ticks"]) not in known
        )
    return sorted(merged.values(), key=lambda item: int(item["pgid"]))


def schedule_resume(pause_until_epoch: float) -> dict[str, Any]:
    delay = max(1, int(pause_until_epoch - time.time()))
    unit = f"ollama-limit-auto-resume-{int(pause_until_epoch)}"
    command = [
        "systemd-run",
        "--user",
        f"--unit={unit}",
        f"--on-active={delay}s",
        "--collect",
        "/usr/bin/python3",
        str(SCRIPT_PATH),
        "resume",
    ]
    result = run(command, timeout=30)
    if result.returncode == 0:
        return {"method": "systemd_user_timer", "unit": unit, "delay_seconds": delay}

    fallback_command = (
        f"sleep {delay}; /usr/bin/python3 {shlex.quote(str(SCRIPT_PATH))} resume "
        f">> {shlex.quote(str(AUTO_RESUME_LOG))} 2>&1"
    )
    log_handle = AUTO_RESUME_LOG.open("a", encoding="utf-8")
    child = subprocess.Popen(
        ["bash", "-lc", fallback_command],
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_handle.close()
    return {
        "method": "nohup_sleep_fallback",
        "pid": child.pid,
        "delay_seconds": delay,
        "systemd_error": result.stderr.strip()[:1000],
    }


def schedule_enforce(delay: int = 60) -> dict[str, Any]:
    unit = f"ollama-limit-enforce-{time.time_ns()}"
    command = [
        "systemd-run",
        "--user",
        f"--unit={unit}",
        f"--on-active={max(5, int(delay))}s",
        "--collect",
        "/usr/bin/python3",
        str(SCRIPT_PATH),
        "enforce",
    ]
    result = run(command, timeout=30)
    if result.returncode == 0:
        return {
            "method": "systemd_user_timer",
            "unit": unit,
            "delay_seconds": max(5, int(delay)),
        }

    fallback_command = (
        f"sleep {max(5, int(delay))}; "
        f"/usr/bin/python3 {shlex.quote(str(SCRIPT_PATH))} enforce "
        f">> {shlex.quote(str(AUTO_ENFORCE_LOG))} 2>&1"
    )
    log_handle = AUTO_ENFORCE_LOG.open("a", encoding="utf-8")
    child = subprocess.Popen(
        ["bash", "-lc", fallback_command],
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_handle.close()
    return {
        "method": "nohup_sleep_fallback",
        "pid": child.pid,
        "delay_seconds": max(5, int(delay)),
        "systemd_error": result.stderr.strip()[:1000],
    }


def pause(args: argparse.Namespace) -> dict[str, Any]:
    ensure_dirs()
    now = time.time()
    requested_until = float(args.until_epoch)
    if requested_until <= now:
        raise SystemExit("pause_until_epoch must be in the future")

    state = load_state() or {
        "status": "paused",
        "pause_started_epoch": now,
        "groups": [],
        "sessions": [],
        "alerts": [],
    }
    old_until = float(state.get("pause_until_epoch", 0))
    state["pause_until_epoch"] = max(old_until, requested_until)
    state["pause_until_utc"] = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(state["pause_until_epoch"])
    )
    state["status"] = "paused"
    state["updated_epoch"] = now
    state["updated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    alert = {
        "reason": args.reason,
        "email_subject": args.email_subject,
        "email_message_id": args.email_message_id,
        "recorded_epoch": now,
    }
    if alert not in state["alerts"]:
        state["alerts"].append(alert)

    discovered, sessions = discover_groups()
    state["groups"] = merge_groups(state.get("groups", []), discovered)
    known_sessions = {item["session"]: item for item in state.get("sessions", [])}
    known_sessions.update({item["session"]: item for item in sessions})
    state["sessions"] = sorted(known_sessions.values(), key=lambda item: item["session"])
    atomic_write_json(STATE_PATH, state)

    stop_results = []
    for group in state["groups"]:
        stopped, message = stop_group(group)
        group["stopped"] = bool(stopped or message == "already_exited")
        group["stop_result"] = message
        stop_results.append({"pgid": group["pgid"], "result": message})

    state["auto_resume"] = schedule_resume(state["pause_until_epoch"])
    state["next_enforce"] = schedule_enforce()
    state["last_enforced_epoch"] = time.time()
    atomic_write_json(STATE_PATH, state)
    append_event(
        "pause",
        pause_until_epoch=state["pause_until_epoch"],
        groups=stop_results,
        alert=alert,
        auto_resume=state["auto_resume"],
    )
    return state


def enforce() -> dict[str, Any]:
    state = load_state()
    if not state or state.get("status") != "paused":
        return {"status": "not_paused", "state_path": str(STATE_PATH)}
    discovered, sessions = discover_groups()
    before = {int(item["pgid"]) for item in state.get("groups", [])}
    state["groups"] = merge_groups(state.get("groups", []), discovered)
    known_sessions = {item["session"]: item for item in state.get("sessions", [])}
    known_sessions.update({item["session"]: item for item in sessions})
    state["sessions"] = sorted(known_sessions.values(), key=lambda item: item["session"])
    results = []
    for group in state["groups"]:
        stopped, message = stop_group(group)
        group["stopped"] = bool(stopped or message == "already_exited")
        group["stop_result"] = message
        results.append(
            {
                "pgid": group["pgid"],
                "new": int(group["pgid"]) not in before,
                "result": message,
            }
        )
    state["last_enforced_epoch"] = time.time()
    if time.time() < float(state.get("pause_until_epoch", 0)):
        state["next_enforce"] = schedule_enforce()
    atomic_write_json(STATE_PATH, state)
    append_event("enforce", groups=results)
    return state


def tmux_session_exists(name: str) -> bool:
    return run(["tmux", "has-session", "-t", f"={name}"], timeout=10).returncode == 0


def restart_missing_sessions(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for session in sessions:
        name = session.get("session", "")
        command = session.get("start_command", "")
        cwd = session.get("cwd", str(PROJECT_ROOT))
        if len(command) >= 2 and command[0] == command[-1] and command[0] in {"'", '"'}:
            command = command[1:-1]
        if not name or SKIP_SESSION_RE.search(name):
            continue
        if tmux_session_exists(name):
            results.append({"session": name, "result": "still_exists"})
            continue
        if not command:
            results.append({"session": name, "result": "missing_no_start_command"})
            continue
        result = run(["tmux", "new-session", "-d", "-s", name, "-c", cwd, command])
        results.append(
            {
                "session": name,
                "result": "restarted" if result.returncode == 0 else "restart_failed",
                "error": result.stderr.strip()[:1000],
            }
        )
    return results


def resume(force: bool = False) -> dict[str, Any]:
    state = load_state()
    if not state:
        return {"status": "not_paused", "state_path": str(STATE_PATH)}
    now = time.time()
    pause_until = float(state.get("pause_until_epoch", 0))
    if not force and now < pause_until:
        state["auto_resume"] = schedule_resume(pause_until)
        state["last_early_resume_check_epoch"] = now
        atomic_write_json(STATE_PATH, state)
        return {
            "status": "still_paused",
            "pause_until_epoch": pause_until,
            "seconds_remaining": int(pause_until - now),
        }

    group_results = []
    for group in state.get("groups", []):
        resumed, message = continue_group(group)
        group_results.append({"pgid": group["pgid"], "result": message})
    session_results = restart_missing_sessions(state.get("sessions", []))
    state["status"] = "resumed"
    state["resumed_epoch"] = now
    state["resumed_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    state["resume_groups"] = group_results
    state["resume_sessions"] = session_results
    archive_path = ARCHIVE_DIR / f"state_resumed_{int(now)}.json"
    atomic_write_json(archive_path, state)
    try:
        STATE_PATH.unlink()
    except FileNotFoundError:
        pass
    append_event("resume", groups=group_results, sessions=session_results, forced=force)
    return state


def status() -> dict[str, Any]:
    state = load_state()
    groups, sessions = discover_groups()
    return {
        "status": state.get("status", "running") if state else "running",
        "state_path": str(STATE_PATH),
        "state": state,
        "currently_detected_ollama_groups": groups,
        "currently_detected_tmux_sessions": sessions,
        "server_epoch": time.time(),
        "server_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    pause_parser = subparsers.add_parser("pause")
    pause_parser.add_argument("--until-epoch", required=True, type=float)
    pause_parser.add_argument("--reason", default="ollama_limit_email")
    pause_parser.add_argument("--email-subject", default="")
    pause_parser.add_argument("--email-message-id", default="")
    subparsers.add_parser("enforce")
    resume_parser = subparsers.add_parser("resume")
    resume_parser.add_argument("--force", action="store_true")
    subparsers.add_parser("status")
    subparsers.add_parser("inventory")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "pause":
        result = pause(args)
    elif args.command == "enforce":
        result = enforce()
    elif args.command == "resume":
        result = resume(force=args.force)
    else:
        result = status()
        if args.command == "inventory":
            result = {
                "currently_detected_ollama_groups": result[
                    "currently_detected_ollama_groups"
                ],
                "currently_detected_tmux_sessions": result[
                    "currently_detected_tmux_sessions"
                ],
            }
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
