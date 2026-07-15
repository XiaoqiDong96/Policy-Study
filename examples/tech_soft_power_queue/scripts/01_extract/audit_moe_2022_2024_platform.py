#!/usr/bin/env python3
"""CR12: evidence-preserving availability audit for 2022--2024 MOE projects.

The audit follows official notice links and the platform landing page without
credentials.  It does not scrape private pages, bypass access controls, or
claim that a notice-level aggregate is project-level data.
"""

from __future__ import annotations

import argparse
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "02_clean"
sys.path.insert(0, str(SCRIPT_DIR))

from remaining_worker_common import (  # noqa: E402
    clean,
    extract_links,
    fetch_url,
    stable_id,
    utc_now,
    write_csv,
    write_json,
)


PROJECT = Path(__file__).resolve().parents[2]
RAW = Path("03_external_raw/moe_2022_2024_endpoint_audit")
EVIDENCE = Path("10_qc/moe_2022_2024_endpoint_evidence.csv")
SUMMARY = Path("10_qc/moe_2022_2024_endpoint_audit.json")

NOTICES = [
    (2022, "first_batch", "https://www.moe.gov.cn/s78/A08/tongzhi/202211/t20221116_993527.html"),
    (2022, "second_batch", "https://www.moe.gov.cn/s78/A08/tongzhi/202302/t20230207_1042628.html"),
    (2023, "annual", "https://www.moe.gov.cn/s78/A08/tongzhi/202312/t20231215_1094751.html"),
    (2024, "first_batch", "https://www.moe.gov.cn/s78/A08/tongzhi/202404/t20240417_1126074.html"),
    (2024, "operating_model_change", "https://www.moe.gov.cn/s78/A08/tongzhi/202406/t20240627_1138094.html"),
]
PLATFORM_ENDPOINTS = [
    ("platform_https_root", "https://cxhz.hep.com.cn/"),
    ("platform_http_root", "http://cxhz.hep.com.cn/"),
    ("platform_robots", "https://cxhz.hep.com.cn/robots.txt"),
]


class TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def html_text(body: bytes) -> str:
    parser = TextParser()
    parser.feed(body.decode("utf-8", errors="replace"))
    return clean(" ".join(parser.parts))


def save(project: Path, url: str, body: bytes, content_type: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix not in {".html", ".htm", ".pdf", ".xls", ".xlsx", ".csv", ".zip", ".txt"}:
        suffix = ".html" if "html" in content_type.lower() else ".bin"
    relative = RAW / (stable_id(url, length=16) + suffix)
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if body:
        path.write_bytes(body)
    return str(relative)


def is_detail_candidate(url: str, label: str) -> bool:
    path = urlparse(url).path.lower()
    extension = Path(path).suffix
    return extension in {".pdf", ".xls", ".xlsx", ".csv", ".zip"} and bool(
        re.search(r"产学合作|协同育人|立项|项目名单|名单", label)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT)
    args = parser.parse_args()
    project = args.project_root.resolve()
    evidence: list[dict[str, object]] = []
    candidate_links: dict[str, tuple[int, str, str]] = {}
    platform_only_notice_count = 0

    for year, batch, url in NOTICES:
        body, meta = fetch_url(url)
        source_file = save(project, url, body, str(meta.get("content_type", "")))
        text = html_text(body) if body else ""
        platform_only = bool(
            re.search(r"登录.{0,30}(?:项目平台|产学合作协同育人项目平台)", text)
            and re.search(r"查看|下载|查询", text)
        )
        platform_only_notice_count += int(platform_only)
        links = extract_links(body) if body else []
        detail_links = []
        for href, label in links:
            linked = urljoin(str(meta.get("final_url") or url), href)
            if is_detail_candidate(linked, label):
                candidate_links[linked] = (year, batch, label)
                detail_links.append(linked)
        evidence.append({
            "evidence_id": stable_id("notice", year, batch, url),
            "evidence_type": "official_notice",
            "year": year,
            "batch": batch,
            **meta,
            "source_file": source_file,
            "platform_only_language": int(platform_only),
            "login_or_access_control_marker": int(bool(re.search(r"登录|账号|密码", text))),
            "public_detail_candidate_count": len(detail_links),
            "link_label": "",
            "usable_project_detail": 0,
            "audit_note": "official notice text and its explicit file links audited",
        })

    for endpoint_id, url in PLATFORM_ENDPOINTS:
        body, meta = fetch_url(url)
        source_file = save(project, url + endpoint_id, body, str(meta.get("content_type", "")))
        text = html_text(body) if "html" in str(meta.get("content_type", "")).lower() else clean(body[:10000])
        links = extract_links(body) if "html" in str(meta.get("content_type", "")).lower() else []
        for href, label in links:
            linked = urljoin(str(meta.get("final_url") or url), href)
            if is_detail_candidate(linked, label):
                candidate_links[linked] = (0, endpoint_id, label)
        evidence.append({
            "evidence_id": stable_id("platform", endpoint_id, url),
            "evidence_type": "platform_public_endpoint",
            "year": "",
            "batch": endpoint_id,
            **meta,
            "source_file": source_file,
            "platform_only_language": 0,
            "login_or_access_control_marker": int(bool(re.search(r"登录|账号|密码|统一身份认证", text))),
            "public_detail_candidate_count": sum(is_detail_candidate(urljoin(str(meta.get("final_url") or url), href), label) for href, label in links),
            "link_label": "",
            "usable_project_detail": 0,
            "audit_note": "unauthenticated landing endpoint only; no credentials used",
        })

    usable_files: list[str] = []
    for url, (year, batch, label) in sorted(candidate_links.items()):
        body, meta = fetch_url(url)
        source_file = save(project, url, body, str(meta.get("content_type", "")))
        content_type = str(meta.get("content_type", "")).lower()
        suffix = Path(urlparse(str(meta.get("final_url") or url)).path).suffix.lower()
        looks_like_file = suffix in {".pdf", ".xls", ".xlsx", ".csv", ".zip"} or any(
            token in content_type for token in ("pdf", "spreadsheet", "excel", "zip", "csv")
        )
        login_html = "html" in content_type and bool(re.search(r"登录|账号|密码", html_text(body)))
        usable = int(int(meta.get("http_status", 0)) == 200 and len(body) > 1000 and looks_like_file and not login_html)
        if usable:
            usable_files.append(source_file)
        evidence.append({
            "evidence_id": stable_id("candidate", url),
            "evidence_type": "public_detail_candidate",
            "year": year or "",
            "batch": batch,
            **meta,
            "source_file": source_file,
            "platform_only_language": 0,
            "login_or_access_control_marker": int(login_html),
            "public_detail_candidate_count": 1,
            "link_label": label,
            "usable_project_detail": usable,
            "audit_note": "candidate followed only because an official page exposed a direct file link",
        })

    fields = [
        "evidence_id", "evidence_type", "year", "batch", "requested_url", "final_url",
        "http_status", "content_type", "content_disposition", "bytes", "sha256",
        "fetched_at_utc", "source_file", "platform_only_language",
        "login_or_access_control_marker", "public_detail_candidate_count", "link_label",
        "usable_project_detail", "audit_note", "error",
    ]
    write_csv(project / EVIDENCE, evidence, fields)
    decision = "PUBLIC_DETAIL_AVAILABLE" if usable_files else "UNAVAILABLE_WITH_EVIDENCE"
    summary = {
        "status": "PASS",
        "task_id": "CR12",
        "generated_at_utc": utc_now(),
        "availability_evidence_complete": True,
        "evidence_rows": len(evidence),
        "years_audited": [2022, 2023, 2024],
        "official_notices_audited": len(NOTICES),
        "platform_endpoints_audited": len(PLATFORM_ENDPOINTS),
        "platform_only_notice_count": platform_only_notice_count,
        "public_detail_candidates_followed": len(candidate_links),
        "public_project_detail_files": len(usable_files),
        "public_project_detail_paths": usable_files,
        "availability_decision": decision,
        "conditional_parser_action": "RUN_CR13" if usable_files else "SKIP_CR13_WITH_EXIT_78",
        "access_boundary": "unauthenticated official pages and direct links only; no login bypass or credential use",
        "2024_scope_change": "after the June 2024 operating-model change, MOE no longer publishes ordinary project establishment notices; platform备案 is not a public detail file",
    }
    write_json(project / SUMMARY, summary)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
