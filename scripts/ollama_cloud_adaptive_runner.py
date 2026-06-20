#!/usr/bin/env python3
"""
Adaptive supervisor for long Ollama Cloud classification runs.

This wrapper runs nev_policy_pipeline.py in resumable chunks. After each chunk
it reads the log, detects Ollama Cloud 429/session/weekly limit signals, and
adjusts the next chunk:

- short 429 / queue-full: reduce document concurrency and cool down briefly
- 5-hour session limit: sleep until the next session window, then resume
- 7-day weekly limit: stop by default, or sleep if explicitly requested
- clean chunks: cautiously increase document concurrency up to a cap

The wrapped classifier remains responsible for per-request JSON parsing,
model retries, and resume-safe JSONL output.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE = PROJECT_ROOT / "scripts" / "nev_policy_pipeline.py"


@dataclass
class LogSignals:
    http_429: int = 0
    short_rate_limit: int = 0
    session_limit: int = 0
    weekly_limit: int = 0
    all_failed_rows: int = 0

    @property
    def any_limit(self) -> bool:
        return bool(self.http_429 or self.short_rate_limit or self.session_limit or self.weekly_limit)


def count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def read_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def sleep_with_countdown(seconds: float, reason: str, state_path: Path, state: Dict[str, Any]) -> None:
    until = time.time() + max(0, seconds)
    state.update({"status": "cooldown", "cooldown_reason": reason, "cooldown_until": until})
    write_state(state_path, state)
    while True:
        remaining = int(until - time.time())
        if remaining <= 0:
            break
        print(f"[ADAPTIVE COOLDOWN] reason={reason} remaining={remaining}s", flush=True)
        time.sleep(min(300, remaining))


def scan_log(path: Path, offset: int) -> tuple[LogSignals, int]:
    signals = LogSignals()
    if not path.exists():
        return signals, offset
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        fh.seek(offset)
        text = fh.read()
        new_offset = fh.tell()

    lower = text.lower()
    signals.http_429 = len(re.findall(r"http(?:=|error )?429|too many requests", lower))
    signals.short_rate_limit = len(re.findall(r"limit_kind=short_rate_limit|rate limit|queue is full", lower))
    signals.session_limit = len(re.findall(r"limit_kind=session_limit|session limit|5[- ]?hour|five hour", lower))
    signals.weekly_limit = len(re.findall(r"limit_kind=weekly_limit|weekly limit|7[- ]?day|seven day", lower))
    signals.all_failed_rows = len(re.findall(r"all_failed=[1-9]", lower))
    return signals, new_offset


def build_classify_command(args: argparse.Namespace, max_candidates: int, parallel_docs: int) -> List[str]:
    cmd = [
        sys.executable,
        str(PIPELINE),
        "classify",
        "--input",
        "dummy",
        "--existing-candidates",
        str(Path(args.candidates)),
        "--output-dir",
        str(Path(args.output_dir)),
        "--candidates-name",
        args.candidates_name,
        "--classified-name",
        args.classified_name,
        "--prompt-mode",
        args.prompt_mode,
        "--parallel-docs",
        str(parallel_docs),
        "--ollama-format",
        args.ollama_format,
        "--max-body-chars",
        str(args.max_body_chars),
        "--long-doc-mode",
        args.long_doc_mode,
        "--num-ctx",
        str(args.num_ctx),
        "--llm-timeout",
        str(args.llm_timeout),
        "--llm-retries",
        str(args.llm_retries),
        "--retry-base-sleep",
        str(args.retry_base_sleep),
        "--progress-every",
        str(args.progress_every),
        "--max-candidates",
        str(max_candidates),
        "--resume",
    ]
    if args.models:
        cmd.extend(["--models", args.models])
    else:
        cmd.extend(["--model", args.model])
    if args.parallel_models:
        cmd.append("--parallel-models")
    return cmd


def run_chunk(args: argparse.Namespace, max_candidates: int, parallel_docs: int, log_path: Path) -> int:
    cmd = build_classify_command(args, max_candidates=max_candidates, parallel_docs=parallel_docs)
    print(
        "[ADAPTIVE RUN] "
        + json.dumps(
            {
                "max_candidates": max_candidates,
                "parallel_docs": parallel_docs,
                "cmd": " ".join(cmd),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_fh:
        log_fh.write("\n\n=== ADAPTIVE CHUNK START ===\n")
        log_fh.write(" ".join(cmd) + "\n")
        log_fh.flush()
        proc = subprocess.run(cmd, cwd=PROJECT_ROOT, stdout=log_fh, stderr=subprocess.STDOUT)
        log_fh.write(f"\n=== ADAPTIVE CHUNK END returncode={proc.returncode} ===\n")
    return int(proc.returncode)


def estimate_eta(done: int, total: int, started: float) -> str:
    elapsed = max(0.1, time.time() - started)
    rate = done / elapsed if done else 0.0
    if rate <= 0:
        return "unknown"
    remaining = max(0, total - done)
    seconds = int(remaining / rate)
    hours, rem = divmod(seconds, 3600)
    minutes, _ = divmod(rem, 60)
    return f"{hours}h{minutes:02d}m"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--candidates-name", default="nev_candidates_adaptive_norm.jsonl")
    parser.add_argument("--classified-name", default="nev_classified_adaptive.jsonl")
    parser.add_argument("--model", default="minimax-m2.5:cloud")
    parser.add_argument("--models", default="")
    parser.add_argument("--prompt-mode", default="standard", choices=["standard", "adversarial"])
    parser.add_argument("--parallel-models", action="store_true")
    parser.add_argument("--initial-parallel-docs", type=int, default=4)
    parser.add_argument("--min-parallel-docs", type=int, default=1)
    parser.add_argument("--max-parallel-docs", type=int, default=5)
    parser.add_argument("--chunk-size", type=int, default=1000)
    parser.add_argument("--max-total", type=int, default=0)
    parser.add_argument("--ollama-format", default="auto")
    parser.add_argument("--max-body-chars", type=int, default=8000)
    parser.add_argument("--long-doc-mode", default="evidence_pack")
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--llm-timeout", type=int, default=600)
    parser.add_argument("--llm-retries", type=int, default=4)
    parser.add_argument("--retry-base-sleep", type=float, default=5.0)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--state-path", default="")
    parser.add_argument("--log-path", default="")
    parser.add_argument("--short-cooldown-seconds", type=int, default=900)
    parser.add_argument("--session-cooldown-seconds", type=int, default=5 * 3600 + 300)
    parser.add_argument("--weekly-cooldown-seconds", type=int, default=7 * 24 * 3600 + 900)
    parser.add_argument("--weekly-limit-action", choices=["stop", "sleep"], default="stop")
    parser.add_argument("--session-limit-action", choices=["sleep", "stop"], default="sleep")
    parser.add_argument("--rate-limit-threshold", type=int, default=3)
    parser.add_argument("--increase-after-clean-chunks", type=int, default=2)
    parser.add_argument("--proactive-session-break-minutes", type=int, default=0)
    args = parser.parse_args()

    candidates_path = Path(args.candidates)
    total = count_jsonl(candidates_path)
    if args.max_total:
        total = min(total, args.max_total)
    out_dir = Path(args.output_dir)
    classified_path = out_dir / args.classified_name
    state_path = Path(args.state_path) if args.state_path else out_dir / "adaptive_state.json"
    log_path = Path(args.log_path) if args.log_path else out_dir / "adaptive_runner.log"

    state = read_state(state_path)
    parallel_docs = int(state.get("parallel_docs") or args.initial_parallel_docs)
    parallel_docs = max(args.min_parallel_docs, min(args.max_parallel_docs, parallel_docs))
    clean_chunks = int(state.get("clean_chunks") or 0)
    if clean_chunks < 0 or clean_chunks > 100:
        clean_chunks = 0
    log_offset = int(state.get("log_offset") or 0)
    started = time.time()
    session_started = float(state.get("session_started") or started)

    while True:
        done = count_jsonl(classified_path)
        if done >= total:
            state.update({"status": "complete", "done": done, "total": total, "parallel_docs": parallel_docs})
            write_state(state_path, state)
            print(f"[ADAPTIVE COMPLETE] done={done:,}/{total:,}", flush=True)
            return

        if args.proactive_session_break_minutes > 0:
            session_elapsed = time.time() - session_started
            safety = args.proactive_session_break_minutes * 60
            if session_elapsed >= safety:
                sleep_with_countdown(
                    args.session_cooldown_seconds,
                    "proactive_session_break",
                    state_path,
                    state,
                )
                session_started = time.time()

        target = min(total, max(done + args.chunk_size, done + 1))
        rc = run_chunk(args, max_candidates=target, parallel_docs=parallel_docs, log_path=log_path)
        signals, log_offset = scan_log(log_path, log_offset)
        new_done = count_jsonl(classified_path)

        status = {
            "status": "running",
            "done": new_done,
            "total": total,
            "target": target,
            "parallel_docs": parallel_docs,
            "returncode": rc,
            "signals": signals.__dict__,
            "eta": estimate_eta(new_done, total, started),
            "updated_at": time.time(),
            "session_started": session_started,
            "log_offset": log_offset,
        }
        state.update(status)
        write_state(state_path, state)
        print("[ADAPTIVE STATUS] " + json.dumps(status, ensure_ascii=False), flush=True)

        if signals.weekly_limit:
            if args.weekly_limit_action == "sleep":
                sleep_with_countdown(args.weekly_cooldown_seconds, "weekly_limit", state_path, state)
                session_started = time.time()
            else:
                state.update({"status": "stopped_weekly_limit"})
                write_state(state_path, state)
                print("[ADAPTIVE STOP] weekly limit detected; stopping for manual review.", flush=True)
                return
        elif signals.session_limit:
            if args.session_limit_action == "sleep":
                sleep_with_countdown(args.session_cooldown_seconds, "session_limit", state_path, state)
                session_started = time.time()
            else:
                state.update({"status": "stopped_session_limit"})
                write_state(state_path, state)
                print("[ADAPTIVE STOP] session limit detected; stopping for manual review.", flush=True)
                return
        elif signals.http_429 >= args.rate_limit_threshold or signals.short_rate_limit >= args.rate_limit_threshold:
            old = parallel_docs
            parallel_docs = max(args.min_parallel_docs, math.floor(parallel_docs / 2))
            clean_chunks = 0
            sleep_for = args.short_cooldown_seconds + random.randint(0, 60)
            print(
                f"[ADAPTIVE RATE LIMIT] 429={signals.http_429} short={signals.short_rate_limit} "
                f"parallel_docs {old}->{parallel_docs}; cooldown={sleep_for}s",
                flush=True,
            )
            sleep_with_countdown(sleep_for, "short_rate_limit", state_path, state)
        elif rc != 0 or signals.all_failed_rows:
            old = parallel_docs
            parallel_docs = max(args.min_parallel_docs, parallel_docs - 1)
            clean_chunks = 0
            print(f"[ADAPTIVE DEGRADE] rc={rc} all_failed={signals.all_failed_rows}; {old}->{parallel_docs}", flush=True)
            sleep_with_countdown(args.short_cooldown_seconds, "chunk_error", state_path, state)
        else:
            clean_chunks += 1
            if clean_chunks >= args.increase_after_clean_chunks and parallel_docs < args.max_parallel_docs:
                parallel_docs += 1
                clean_chunks = 0
                print(f"[ADAPTIVE INCREASE] parallel_docs={parallel_docs}", flush=True)
            state.update({"parallel_docs": parallel_docs, "clean_chunks": clean_chunks})
            write_state(state_path, state)


if __name__ == "__main__":
    main()
