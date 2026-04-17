from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, List

CPP_PATTERNS = {
    "raw_pointer": re.compile(r"\b[A-Za-z_]\w*\s*\*\s*[A-Za-z_]\w*", re.MULTILINE),
    "new_expression": re.compile(r"\bnew\s+[A-Za-z_]\w*", re.MULTILINE),
    "delete_expression": re.compile(r"\bdelete\s+[A-Za-z_]\w*", re.MULTILINE),
    "std_container": re.compile(
        r"\bstd::(vector|map|unordered_map|set|unordered_set|string)\b", re.MULTILINE
    ),
    "dynamic_cast": re.compile(r"\bdynamic_cast\s*<", re.MULTILINE),
}

DEFAULT_SOFT_MAX_FILES = 200
DEFAULT_HARD_MAX_FILES = 800
DEFAULT_TIME_BUDGET_MS = 2500


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _scan_patterns(text: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for key, pattern in CPP_PATTERNS.items():
        out[key] = len(pattern.findall(text))
    out["new_delete_imbalance_hint"] = max(
        0, out.get("new_expression", 0) - out.get("delete_expression", 0)
    )
    return out


def analyze_source_tree(
    source_root: Path,
    max_files: int = DEFAULT_SOFT_MAX_FILES,
    *,
    hard_max_files: int = DEFAULT_HARD_MAX_FILES,
    time_budget_ms: int = DEFAULT_TIME_BUDGET_MS,
) -> Dict[str, Any]:
    root = Path(source_root)
    if not root.exists():
        return {"ok": False, "message": f"Source folder not found: {root}"}

    requested_soft_limit = max(1, int(max_files or DEFAULT_SOFT_MAX_FILES))
    resolved_hard_limit = max(1, int(hard_max_files or DEFAULT_HARD_MAX_FILES))
    effective_soft_limit = min(requested_soft_limit, resolved_hard_limit)
    resolved_time_budget_ms = max(1, int(time_budget_ms or DEFAULT_TIME_BUDGET_MS))

    cpp_files = sorted(root.rglob("*.cpp"))
    h_files = sorted(root.rglob("*.h"))
    candidate_files = cpp_files + h_files
    files = candidate_files[:effective_soft_limit]
    soft_limit_truncated = len(candidate_files) > effective_soft_limit
    hard_limit_clamped = requested_soft_limit > resolved_hard_limit

    totals = {
        "raw_pointer": 0,
        "new_expression": 0,
        "delete_expression": 0,
        "std_container": 0,
        "dynamic_cast": 0,
        "new_delete_imbalance_hint": 0,
    }
    total_lines = 0
    scanned_files: List[Dict[str, Any]] = []
    timeout_hit = False
    deadline = time.perf_counter() + (resolved_time_budget_ms / 1000.0)

    for path in files:
        if time.perf_counter() > deadline:
            timeout_hit = True
            break
        text = _safe_read(path)
        if not text:
            continue
        line_count = len(text.splitlines())
        total_lines += line_count
        counters = _scan_patterns(text)
        for key, value in counters.items():
            totals[key] = totals.get(key, 0) + int(value)
        scanned_files.append(
            {
                "path": str(path),
                "line_count": line_count,
                "issues": counters,
            }
        )

    findings: List[Dict[str, Any]] = []
    if totals["new_delete_imbalance_hint"] > 0:
        findings.append(
            {
                "rule": "new_delete_imbalance_hint",
                "severity": "warning",
                "message": "Found more `new` expressions than `delete` expressions.",
                "count": totals["new_delete_imbalance_hint"],
            }
        )
    if totals["std_container"] > 0:
        findings.append(
            {
                "rule": "std_container",
                "severity": "info",
                "message": "Found std containers that may need UE container review.",
                "count": totals["std_container"],
            }
        )
    if totals["raw_pointer"] > 0:
        findings.append(
            {
                "rule": "raw_pointer",
                "severity": "info",
                "message": "Found raw pointer declarations; verify ownership/UPROPERTY usage.",
                "count": totals["raw_pointer"],
            }
        )

    truncated = bool(soft_limit_truncated or timeout_hit)
    truncation_reasons: List[str] = []
    if soft_limit_truncated:
        truncation_reasons.append("soft_limit")
    if timeout_hit:
        truncation_reasons.append("timeout")

    return {
        "ok": True,
        "source_root": str(root),
        "file_count": len(scanned_files),
        "candidate_file_count": len(candidate_files),
        "line_count": total_lines,
        "pattern_totals": totals,
        "findings": findings,
        "scanned_files": scanned_files[:25],
        "scan_guards": {
            "soft_limit": requested_soft_limit,
            "hard_limit": resolved_hard_limit,
            "effective_limit": effective_soft_limit,
            "hard_limit_clamped": bool(hard_limit_clamped),
            "time_budget_ms": resolved_time_budget_ms,
            "timeout_hit": bool(timeout_hit),
            "truncated": truncated,
            "truncation_reasons": truncation_reasons,
            "processed_file_count": len(scanned_files),
            "candidate_file_count": len(candidate_files),
        },
    }
