#!/usr/bin/env python3
"""
Materialize full-text AI candidate JSONL from the original legal corpus.

This mirrors build_fulltext_candidates.py with artificial-intelligence defaults.
It does not run unless invoked explicitly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from build_fulltext_candidates import build_fulltext_candidates  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(PROJECT_ROOT / "法律法规文件库.json"))
    parser.add_argument(
        "--candidates",
        default=str(PROJECT_ROOT / "outputs/policy_packages/artificial_intelligence/candidates.jsonl"),
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "outputs/policy_packages/artificial_intelligence_fulltext/candidates_fulltext.jsonl"),
    )
    parser.add_argument("--json-prefix", default="item")
    parser.add_argument("--progress-every", type=int, default=100000)
    parser.add_argument("--found-progress-every", type=int, default=500)
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument(
        "--sample-output",
        default=str(PROJECT_ROOT / "outputs/policy_packages/artificial_intelligence_fulltext/candidates_random100_fulltext.jsonl"),
    )
    parser.add_argument("--sample-seed", type=int, default=20260623)
    return parser.parse_args()


def main() -> None:
    build_fulltext_candidates(parse_args())


if __name__ == "__main__":
    main()
