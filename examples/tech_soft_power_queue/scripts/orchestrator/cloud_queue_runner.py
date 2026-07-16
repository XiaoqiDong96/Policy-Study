#!/usr/bin/env python3
"""Persistent, resumable task runner for the tech-soft-power data queue.

The runner uses a JSON manifest as the executable contract.  It never treats a
zero exit code alone as completion: every configured output gate must pass.
Failed commands are retried indefinitely with capped exponential backoff, while
independent tasks remain eligible to run.  Tasks whose source-specific worker
has not been implemented are reported as ``AWAITING_WORKER`` instead of being
silently skipped or falsely completed.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


EXIT_WAITING_EXTERNAL = 75
EXIT_SKIPPED_WITH_EVIDENCE = 78
SUCCESS_STATES = {"COMPLETE", "SKIPPED_WITH_EVIDENCE"}
ACTIVE_STATES = {"RUNNING"}
RETRY_DELAYS = [60, 300, 900, 3600, 21600]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected a JSON object: {path}")
    return value


def nested_value(value: Any, dotted_key: str) -> Any:
    current = value
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(dotted_key)
        current = current[part]
    return current


def norm_code(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6) if text.isdigit() else text


class QueueRunner:
    def __init__(
        self,
        project_root: Path,
        manifest_path: Path,
        *,
        recover_stale_running: bool = True,
    ) -> None:
        self.project_root = project_root.resolve()
        self.manifest_path = manifest_path.resolve()
        self.manifest = read_json(self.manifest_path)
        self.runtime_dir = self.project_root / "10_qc" / "orchestrator"
        self.state_path = self.runtime_dir / "state.json"
        self.status_csv = self.runtime_dir / "task_status.csv"
        self.summary_md = self.runtime_dir / "STATUS.md"
        self.complete_flag = self.runtime_dir / "all_tasks_complete.flag"
        self.log_dir = self.runtime_dir / "logs"
        self.lock_path = self.runtime_dir / "runner.lock"
        self.stop_requested = False
        self.recover_stale_running = recover_stale_running
        self.task_map = {
            task["task_id"]: task for task in self.manifest.get("tasks", [])
        }
        self._validate_manifest()
        self.state = self._load_state()

    def _validate_manifest(self) -> None:
        tasks = self.manifest.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise RuntimeError("Manifest must contain a non-empty tasks list")
        ids = [str(task.get("task_id", "")) for task in tasks]
        if not all(ids) or len(ids) != len(set(ids)):
            raise RuntimeError("Task ids must be present and unique")
        known = set(ids)
        for task in tasks:
            worker_status = task.get("worker_status")
            if worker_status not in {"ready", "pending", "external", "verify_only"}:
                raise RuntimeError(
                    f"Invalid worker_status for {task['task_id']}: {worker_status}"
                )
            dependencies = task.get("dependencies", [])
            if not isinstance(dependencies, list):
                raise RuntimeError(f"dependencies must be a list: {task['task_id']}")
            missing = sorted(set(dependencies) - known)
            if missing:
                raise RuntimeError(
                    f"Unknown dependencies for {task['task_id']}: {missing}"
                )
            commands = task.get("commands", [])
            if worker_status in {"ready", "external"} and not commands:
                raise RuntimeError(f"Runnable task lacks commands: {task['task_id']}")
            if worker_status in {"ready", "external", "verify_only"} and not task.get("gates"):
                raise RuntimeError(
                    f"Runnable/verified task lacks output gates: {task['task_id']}"
                )
            for command in commands:
                if not isinstance(command, list) or not command:
                    raise RuntimeError(
                        f"Each command must be a non-empty argv list: {task['task_id']}"
                    )
        post_completion = self.manifest.get("post_completion")
        if post_completion is not None:
            if not isinstance(post_completion, dict):
                raise RuntimeError("post_completion must be an object")
            commands = post_completion.get("commands", [])
            gates = post_completion.get("gates", [])
            if not commands or not gates:
                raise RuntimeError("post_completion requires commands and gates")
            for command in commands:
                if not isinstance(command, list) or not command:
                    raise RuntimeError(
                        "Each post_completion command must be a non-empty argv list"
                    )

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.is_file():
            state = read_json(self.state_path)
        else:
            state = {
                "schema_version": 1,
                "created_at_utc": utc_now(),
                "updated_at_utc": utc_now(),
                "runner_pid": None,
                "overall_status": "IN_PROGRESS",
                "tasks": {},
            }
        task_state = state.setdefault("tasks", {})
        for task_id, task in self.task_map.items():
            entry = task_state.setdefault(
                task_id,
                {
                    "status": task.get("seed_status", "PENDING"),
                    "attempts": 0,
                    "next_run_epoch": 0,
                    "last_started_at_utc": None,
                    "last_finished_at_utc": None,
                    "last_exit_code": None,
                    "last_error": "",
                    "last_gate_results": [],
                    "worker_status": task.get("worker_status"),
                },
            )
            entry["worker_status"] = task.get("worker_status")
            if self.recover_stale_running and entry.get("status") == "RUNNING":
                entry["status"] = "PENDING_RECOVERY"
                entry["last_error"] = "runner restarted while task was RUNNING"
                entry["next_run_epoch"] = 0
        if self.manifest.get("post_completion"):
            post = state.setdefault(
                "post_completion",
                {
                    "status": "PENDING",
                    "attempts": 0,
                    "next_run_epoch": 0,
                    "last_started_at_utc": None,
                    "last_finished_at_utc": None,
                    "last_exit_code": None,
                    "last_error": "",
                    "last_gate_results": [],
                },
            )
            if self.recover_stale_running and post.get("status") == "RUNNING":
                post["status"] = "PENDING_RECOVERY"
                post["last_error"] = "runner restarted during post-completion finalization"
                post["next_run_epoch"] = 0
        return state

    def _expand_command(self, command: list[Any]) -> list[str]:
        environment = {
            "PROJECT_ROOT": str(self.project_root),
            "POLICY_PROJECT": os.environ.get(
                "POLICY_PROJECT", str(self.project_root.parent / "nev_policy_project")
            ),
            "PYTHON": os.environ.get("PYTHON", sys.executable),
        }
        expanded = []
        for token in command:
            text = str(token)
            for key, value in environment.items():
                text = text.replace("${" + key + "}", value)
            expanded.append(text)
        return expanded

    def _gate_path(self, gate: dict[str, Any]) -> Path:
        raw = str(gate.get("path", ""))
        expanded = self._expand_command([raw])[0]
        path = Path(expanded)
        return path if path.is_absolute() else self.project_root / path

    def _evaluate_gate(self, gate: dict[str, Any]) -> dict[str, Any]:
        kind = gate.get("type")
        path = self._gate_path(gate)
        result: dict[str, Any] = {
            "type": kind,
            "path": str(path),
            "passed": False,
            "detail": "",
        }
        try:
            if kind == "file_nonempty":
                result["passed"] = path.is_file() and path.stat().st_size > 0
                result["detail"] = (
                    f"bytes={path.stat().st_size}" if path.is_file() else "missing"
                )
            elif kind == "json_equals":
                value = nested_value(read_json(path), str(gate["key"]))
                result["passed"] = value == gate.get("value")
                result["detail"] = f"actual={value!r} expected={gate.get('value')!r}"
            elif kind == "json_min":
                value = nested_value(read_json(path), str(gate["key"]))
                result["passed"] = float(value) >= float(gate["value"])
                result["detail"] = f"actual={value!r} minimum={gate['value']!r}"
            elif kind == "csv_shape":
                with path.open(encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    rows = list(reader)
                    columns = set(reader.fieldnames or [])
                passed = True
                details = [f"rows={len(rows)}"]
                if "rows" in gate:
                    passed = passed and len(rows) == int(gate["rows"])
                    details.append(f"expected_rows={gate['rows']}")
                if gate.get("unique_city_codes") is not None:
                    codes = {norm_code(row.get("city_code")) for row in rows}
                    codes.discard("")
                    passed = passed and len(codes) == int(gate["unique_city_codes"])
                    details.append(f"unique_city_codes={len(codes)}")
                if gate.get("unique_key"):
                    fields = [str(field) for field in gate["unique_key"]]
                    keys = {tuple(row.get(field, "") for field in fields) for row in rows}
                    passed = passed and len(keys) == len(rows)
                    details.append(f"unique_keys={len(keys)}")
                if gate.get("required_columns"):
                    required = {str(field) for field in gate["required_columns"]}
                    missing_columns = sorted(required - columns)
                    passed = passed and not missing_columns
                    details.append(
                        "missing_columns=" + (",".join(missing_columns) if missing_columns else "none")
                    )
                result["passed"] = passed
                result["detail"] = " ".join(details)
            else:
                result["detail"] = f"unknown gate type: {kind}"
        except Exception as exc:  # gate failure is recorded, not hidden
            result["detail"] = f"{type(exc).__name__}: {exc}"
        return result

    def evaluate_gates(self, task: dict[str, Any]) -> list[dict[str, Any]]:
        return [self._evaluate_gate(gate) for gate in task.get("gates", [])]

    def _dependencies_complete(self, task: dict[str, Any]) -> bool:
        for dependency in task.get("dependencies", []):
            status = self.state["tasks"][dependency]["status"]
            if status not in SUCCESS_STATES:
                return False
        return True

    def _sync_manifest_state(self) -> None:
        for task_id, task in self.task_map.items():
            entry = self.state["tasks"][task_id]
            worker_status = task.get("worker_status")
            entry["worker_status"] = worker_status
            if worker_status == "pending" and entry["status"] not in SUCCESS_STATES:
                entry["status"] = "AWAITING_WORKER"
            elif (
                worker_status in {"ready", "external", "verify_only"}
                and entry["status"] == "AWAITING_WORKER"
            ):
                # A worker may be deployed after the state file was created.
                # Promote the stale placeholder state so the queue can execute
                # it without any manual state-file edit.
                entry["status"] = "PENDING"
                entry["next_run_epoch"] = 0
                entry["last_error"] = ""

    def _preflight_completed_tasks(self) -> None:
        for task_id, task in self.task_map.items():
            entry = self.state["tasks"][task_id]
            if entry.get("status") != "COMPLETE":
                continue
            gate_results = self.evaluate_gates(task)
            entry["last_gate_results"] = gate_results
            if gate_results and not all(item["passed"] for item in gate_results):
                entry["status"] = "PENDING_RECOVERY"
                entry["last_error"] = "previous COMPLETE state failed current output gates"
                entry["next_run_epoch"] = 0
        post_spec = self.manifest.get("post_completion")
        post = self.state.get("post_completion")
        if post_spec and post:
            if not self._tasks_complete() and post.get("status") == "COMPLETE":
                post["status"] = "PENDING_RECOVERY"
                post["last_error"] = "an upstream task returned to recovery"
                post["next_run_epoch"] = 0
            elif post.get("status") == "COMPLETE":
                gates = self.evaluate_gates(post_spec)
                post["last_gate_results"] = gates
                if not gates or not all(item["passed"] for item in gates):
                    post["status"] = "PENDING_RECOVERY"
                    post["last_error"] = "post-completion output gates no longer pass"
                    post["next_run_epoch"] = 0

    def _tasks_complete(self) -> bool:
        statuses = [entry["status"] for entry in self.state["tasks"].values()]
        return bool(statuses) and all(status in SUCCESS_STATES for status in statuses)

    def _save_state(self) -> None:
        self._sync_manifest_state()
        statuses = [entry["status"] for entry in self.state["tasks"].values()]
        post = self.state.get("post_completion")
        post_complete = not self.manifest.get("post_completion") or (
            post and post.get("status") == "COMPLETE"
        )
        if statuses and all(status in SUCCESS_STATES for status in statuses) and post_complete:
            overall = "COMPLETE"
        elif any(status in ACTIVE_STATES for status in statuses) or (
            post and post.get("status") == "RUNNING"
        ):
            overall = "RUNNING"
        elif statuses and all(status in SUCCESS_STATES for status in statuses):
            overall = "FINALIZING"
        else:
            overall = "IN_PROGRESS"
        self.state["overall_status"] = overall
        self.state["updated_at_utc"] = utc_now()
        self.state["runner_pid"] = os.getpid()
        atomic_write_json(self.state_path, self.state)
        self._write_status_files()

    def _write_status_files(self) -> None:
        fields = [
            "task_id",
            "task_name",
            "status",
            "worker_status",
            "attempts",
            "last_started_at_utc",
            "last_finished_at_utc",
            "last_exit_code",
            "last_error",
        ]
        self.status_csv.parent.mkdir(parents=True, exist_ok=True)
        with self.status_csv.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for task_id, task in sorted(
                self.task_map.items(), key=lambda item: int(item[1].get("order", 999))
            ):
                entry = self.state["tasks"][task_id]
                writer.writerow(
                    {
                        "task_id": task_id,
                        "task_name": task.get("task_name", ""),
                        **{field: entry.get(field, "") for field in fields if field not in {"task_id", "task_name"}},
                    }
                )
        counts: dict[str, int] = {}
        for entry in self.state["tasks"].values():
            counts[entry["status"]] = counts.get(entry["status"], 0) + 1
        lines = [
            "# Autonomous queue status",
            "",
            f"- Overall: **{self.state['overall_status']}**",
            f"- Updated (UTC): {self.state['updated_at_utc']}",
            f"- Runner PID: {self.state['runner_pid']}",
            f"- Counts: {json.dumps(counts, ensure_ascii=False, sort_keys=True)}",
            f"- Post-completion: {self.state.get('post_completion', {}).get('status', 'not_configured')}",
            "",
            "| Task | Status | Worker | Attempts | Last error |",
            "|---|---|---|---:|---|",
        ]
        for task_id, task in sorted(
            self.task_map.items(), key=lambda item: int(item[1].get("order", 999))
        ):
            entry = self.state["tasks"][task_id]
            error = str(entry.get("last_error", "")).replace("|", "\\|")[:180]
            lines.append(
                f"| {task_id} {task.get('task_name', '')} | {entry['status']} | "
                f"{entry.get('worker_status', '')} | {entry.get('attempts', 0)} | {error} |"
            )
        self.summary_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _task_log_path(self, task_id: str) -> Path:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        return self.log_dir / f"{task_id}.log"

    def _run_command(self, task_id: str, argv: list[str], timeout: int) -> int:
        log_path = self._task_log_path(task_id)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n[{utc_now()}] START argv={json.dumps(argv, ensure_ascii=False)}\n")
            log.flush()
            process = subprocess.Popen(
                argv,
                cwd=self.project_root,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                env={**os.environ, "PROJECT_ROOT": str(self.project_root)},
            )
            started = time.monotonic()
            while True:
                return_code = process.poll()
                if return_code is not None:
                    log.write(f"[{utc_now()}] END exit={return_code}\n")
                    return return_code
                if self.stop_requested:
                    process.terminate()
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    return 143
                if timeout and time.monotonic() - started > timeout:
                    process.terminate()
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    log.write(f"[{utc_now()}] TIMEOUT seconds={timeout}\n")
                    return 124
                time.sleep(10)
                self._save_state()

    def run_task(self, task_id: str) -> None:
        task = self.task_map[task_id]
        entry = self.state["tasks"][task_id]
        entry["status"] = "RUNNING"
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        entry["last_started_at_utc"] = utc_now()
        entry["last_error"] = ""
        self._save_state()
        exit_code = 0
        for raw_command in task.get("commands", []):
            argv = self._expand_command(raw_command)
            exit_code = self._run_command(
                task_id, argv, int(task.get("timeout_seconds", 0) or 0)
            )
            if exit_code != 0:
                break
        entry["last_exit_code"] = exit_code
        entry["last_finished_at_utc"] = utc_now()
        if exit_code == EXIT_WAITING_EXTERNAL:
            entry["status"] = "WAITING_EXTERNAL"
            entry["last_error"] = "external prerequisite is not complete"
            entry["next_run_epoch"] = time.time() + int(
                task.get("external_poll_seconds", 300)
            )
        elif exit_code == EXIT_SKIPPED_WITH_EVIDENCE:
            gate_results = self.evaluate_gates(task)
            entry["last_gate_results"] = gate_results
            if gate_results and all(item["passed"] for item in gate_results):
                entry["status"] = "SKIPPED_WITH_EVIDENCE"
                entry["last_error"] = ""
            else:
                self._schedule_retry(entry, "conditional skip lacked required evidence gates")
        elif exit_code != 0:
            self._schedule_retry(entry, f"command exited {exit_code}")
        else:
            gate_results = self.evaluate_gates(task)
            entry["last_gate_results"] = gate_results
            if all(item["passed"] for item in gate_results):
                entry["status"] = "COMPLETE"
                entry["last_error"] = ""
                entry["next_run_epoch"] = 0
            else:
                failed = [item["detail"] for item in gate_results if not item["passed"]]
                self._schedule_retry(entry, "output gate failed: " + "; ".join(failed))
        self._save_state()

    def _schedule_retry(self, entry: dict[str, Any], error: str) -> None:
        attempts = max(int(entry.get("attempts", 1)), 1)
        delay = RETRY_DELAYS[min(attempts - 1, len(RETRY_DELAYS) - 1)]
        entry["status"] = "RETRY_WAIT"
        entry["last_error"] = error
        entry["next_run_epoch"] = time.time() + delay

    def run_post_completion(self) -> bool:
        spec = self.manifest.get("post_completion")
        entry = self.state.get("post_completion")
        if not spec or not entry or not self._tasks_complete():
            return False
        if entry.get("status") == "COMPLETE":
            return False
        if float(entry.get("next_run_epoch", 0) or 0) > time.time():
            return False
        entry["status"] = "RUNNING"
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        entry["last_started_at_utc"] = utc_now()
        entry["last_error"] = ""
        self._save_state()
        exit_code = 0
        for raw_command in spec.get("commands", []):
            exit_code = self._run_command(
                "POST_COMPLETION",
                self._expand_command(raw_command),
                int(spec.get("timeout_seconds", 0) or 0),
            )
            if exit_code != 0:
                break
        entry["last_exit_code"] = exit_code
        entry["last_finished_at_utc"] = utc_now()
        if exit_code != 0:
            self._schedule_retry(entry, f"post-completion command exited {exit_code}")
        else:
            gates = self.evaluate_gates(spec)
            entry["last_gate_results"] = gates
            if gates and all(item["passed"] for item in gates):
                entry["status"] = "COMPLETE"
                entry["last_error"] = ""
                entry["next_run_epoch"] = 0
            else:
                failed = [item["detail"] for item in gates if not item["passed"]]
                self._schedule_retry(
                    entry,
                    "post-completion output gate failed: " + "; ".join(failed),
                )
        self._save_state()
        return True

    def next_runnable_task(self) -> str | None:
        now = time.time()
        for task_id, task in sorted(
            self.task_map.items(), key=lambda item: int(item[1].get("order", 999))
        ):
            entry = self.state["tasks"][task_id]
            if entry["status"] in SUCCESS_STATES or entry["status"] == "RUNNING":
                continue
            worker_status = task.get("worker_status")
            if worker_status == "pending":
                entry["status"] = "AWAITING_WORKER"
                continue
            if not self._dependencies_complete(task):
                if entry["status"] not in {"WAITING_EXTERNAL", "RETRY_WAIT"}:
                    entry["status"] = "WAITING_DEPENDENCY"
                continue
            if float(entry.get("next_run_epoch", 0) or 0) > now:
                continue
            if worker_status == "verify_only":
                gates = self.evaluate_gates(task)
                entry["last_gate_results"] = gates
                if gates and all(result["passed"] for result in gates):
                    entry["status"] = "COMPLETE"
                    entry["last_error"] = ""
                    continue
                entry["status"] = "PENDING_RECOVERY"
                entry["last_error"] = "verify-only task failed output gates"
                continue
            return task_id
        return None

    def run_once(self) -> bool:
        self._preflight_completed_tasks()
        task_id = self.next_runnable_task()
        self._save_state()
        if task_id is None:
            return False
        self.run_task(task_id)
        return True

    def run_forever(self) -> int:
        poll_seconds = int(self.manifest.get("poll_seconds", 60))
        while not self.stop_requested:
            ran = self.run_once()
            if self.state["overall_status"] != "COMPLETE" and self.complete_flag.exists():
                self.complete_flag.unlink()
            finalized = self.run_post_completion()
            if self.state["overall_status"] == "COMPLETE":
                self.complete_flag.write_text(utc_now() + "\n", encoding="utf-8")
                self._save_state()
                return 0
            if not ran and not finalized:
                # Keep systemd stop/restart responsive even though Python may
                # transparently resume a long sleep after the signal handler.
                for _ in range(max(1, poll_seconds)):
                    if self.stop_requested:
                        break
                    time.sleep(1)
        self._save_state()
        return 0


def parse_args() -> argparse.Namespace:
    default_project = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(os.environ.get("PROJECT_ROOT", default_project)),
    )
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--validate-manifest", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    manifest = args.manifest or (
        project_root / "scripts" / "orchestrator" / "task_manifest.json"
    )
    runner = QueueRunner(project_root, manifest)
    runner.runtime_dir.mkdir(parents=True, exist_ok=True)
    if args.validate_manifest:
        print(f"PASS: {manifest}")
        return 0
    if args.status:
        if runner.summary_md.is_file():
            print(runner.summary_md.read_text(encoding="utf-8"))
        else:
            print(json.dumps(runner.state, ensure_ascii=False, indent=2))
        return 0

    lock_handle = runner.lock_path.open("a+")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"Another queue runner holds {runner.lock_path}", file=sys.stderr)
        return 73

    def stop_handler(signum: int, frame: Any) -> None:
        del signum, frame
        runner.stop_requested = True

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)

    if args.once:
        runner.run_once()
        print(json.dumps(runner.state, ensure_ascii=False, indent=2))
        return 0
    return runner.run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
