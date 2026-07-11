#!/usr/bin/env python3
"""Culture-industry wrapper for the generic domain policy pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import domain_policy_pipeline  # noqa: E402


if __name__ == "__main__":
    domain_policy_pipeline.main(domain_key="culture_industry", argv=sys.argv[1:])
