#!/usr/bin/env python3
"""Tiny HTTP status server for future-industry and low-altitude workflows."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]


DOMAINS = {
    "future_industries": {
        "label": "六大未来产业",
        "output_root": "outputs/future_industries_policy_panel",
        "candidates": "outputs/policy_packages_future_lowalt/future_industries/candidates.jsonl",
    },
    "low_altitude_economy": {
        "label": "低空经济",
        "output_root": "outputs/low_altitude_policy_panel",
        "candidates": "outputs/policy_packages_future_lowalt/low_altitude_economy/candidates.jsonl",
    },
    "culture_industry": {
        "label": "文化产业",
        "output_root": "outputs/culture_industry_policy_panel",
        "candidates": "outputs/policy_packages_culture/culture_industry/candidates.jsonl",
    },
}


def count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return sum(1 for line in fh if line.strip())


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_read_error": repr(exc)[:200]}


def tmux_ls() -> str:
    try:
        proc = subprocess.run(["tmux", "ls"], cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=8)
    except Exception as exc:
        return repr(exc)
    if proc.returncode:
        return proc.stderr.strip()
    return proc.stdout.strip()


def pct(done: int, total: int) -> float:
    return round(done / total * 100, 3) if total else 0.0


def domain_status(key: str, cfg: Dict[str, str]) -> Dict[str, Any]:
    root = PROJECT_ROOT / cfg["output_root"]
    candidates = PROJECT_ROOT / cfg["candidates"]
    stage1 = root / "stage1_minimax_adaptive" / f"{key}_stage1_minimax.jsonl"
    stage1_state = root / "stage1_minimax_adaptive" / "adaptive_state.json"
    boundary = root / "stage2_dual_vote_boundary" / "qwen_full" / "boundary_0p2_0p8_candidates.jsonl"
    boundary_summary = root / "stage2_dual_vote_boundary" / "qwen_full" / "boundary_0p2_0p8_summary.json"
    second = root / "stage2_dual_vote_boundary" / "qwen_full" / "second_vote" / "qwen_boundary_full.jsonl"
    second_state = root / "stage2_dual_vote_boundary" / "qwen_full" / "second_vote" / "adaptive_state.json"
    final_full = root / "stage2_dual_vote_boundary" / "qwen_full" / "final" / f"{key}_dual_vote_final_qwen.jsonl"
    final_yes = root / "stage2_dual_vote_boundary" / "qwen_full" / "final" / f"{key}_dual_vote_final_qwen_yes.jsonl"
    final_split = root / "stage2_dual_vote_boundary" / "qwen_full" / "final" / f"{key}_dual_vote_final_qwen_split.jsonl"
    tool = root / "tool_refinement" / f"{key}_tool_refined.jsonl"
    panels = root / "final_panels" / f"{key}_policy_summary.json"
    complete = root / f"{key}_full_workflow_complete.flag"

    total = count_jsonl(candidates)
    stage1_done = count_jsonl(stage1)
    boundary_total = count_jsonl(boundary)
    second_done = count_jsonl(second)
    final_done = count_jsonl(final_full)
    yes_total = count_jsonl(final_yes)
    tool_done = count_jsonl(tool)
    stage1_state_data = read_json(stage1_state)
    second_state_data = read_json(second_state)
    if stage1_done > int(stage1_state_data.get("done") or 0):
        stage1_state_data["live_done"] = stage1_done
        stage1_state_data["state_note"] = "adaptive_state refreshes after the current chunk exits; live line count is ahead."
    if second_done > int(second_state_data.get("done") or 0):
        second_state_data["live_done"] = second_done
        second_state_data["state_note"] = "adaptive_state refreshes after the current chunk exits; live line count is ahead."

    if final_done >= total and total:
        phase = "tool_refinement_or_panel"
    elif stage1_done >= total and total:
        phase = "stage2_boundary_vote"
    elif stage1_done:
        phase = "stage1_minimax"
    else:
        phase = "waiting_or_not_started"
    if complete.exists():
        phase = "complete"

    return {
        "domain_key": key,
        "domain_label": cfg["label"],
        "phase": phase,
        "total_candidates": total,
        "stage1_done": stage1_done,
        "stage1_percent": pct(stage1_done, total),
        "boundary_total": boundary_total,
        "second_vote_done": second_done,
        "second_vote_percent": pct(second_done, boundary_total),
        "final_done": final_done,
        "final_percent": pct(final_done, total),
        "final_yes": yes_total,
        "final_split": count_jsonl(final_split),
        "tool_done": tool_done,
        "tool_percent": pct(tool_done, yes_total),
        "panel_complete": panels.exists(),
        "complete": complete.exists(),
        "paths": {
            "candidates": str(candidates),
            "stage1": str(stage1),
            "boundary": str(boundary),
            "second_vote": str(second),
            "final_full": str(final_full),
            "final_yes": str(final_yes),
            "tool_refined": str(tool),
            "panel_summary": str(panels),
        },
        "stage1_state": stage1_state_data,
        "stage2_state": second_state_data,
        "boundary_summary": read_json(boundary_summary),
    }


def full_status() -> Dict[str, Any]:
    return {
        "updated_at_epoch": time.time(),
        "server_time_utc": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "tmux": tmux_ls(),
        "domains": {key: domain_status(key, cfg) for key, cfg in DOMAINS.items()},
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path not in {"/", "/status.json", "/future_lowalt.json"}:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(full_status(), ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving workflow status on http://{args.host}:{args.port}/status.json", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
