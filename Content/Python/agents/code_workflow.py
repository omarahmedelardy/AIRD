from __future__ import annotations

from typing import Any, Dict, List


CODE_WORKFLOW_CONTRACT_VERSION = "aird.code.v1"


def _rule_category(rule: str, severity: str) -> str:
    normalized_rule = str(rule or "").strip().lower()
    normalized_severity = str(severity or "").strip().lower()

    if normalized_rule in {"new_delete_imbalance_hint"}:
        return "actionable_issues"
    if normalized_severity == "warning":
        return "warnings"
    return "informational"


def _unreal_next_action(
    *,
    rule: str,
    count: int,
) -> Dict[str, Any]:
    normalized_rule = str(rule or "").strip().lower()
    qty = int(count or 0)

    if normalized_rule == "new_delete_imbalance_hint":
        return {
            "priority": "high",
            "action": "Review UObject ownership and replace raw allocations with RAII/UE-managed lifetimes where applicable.",
            "reason": f"Detected {qty} potential new/delete imbalance hints in C++ sources.",
            "related_rules": ["new_delete_imbalance_hint", "raw_pointer"],
        }
    if normalized_rule == "raw_pointer":
        return {
            "priority": "medium",
            "action": "Audit raw pointers for Unreal GC safety (UPROPERTY/TObjectPtr) and clear ownership semantics.",
            "reason": f"Detected {qty} raw pointer declarations that may need ownership clarification.",
            "related_rules": ["raw_pointer"],
        }
    if normalized_rule == "std_container":
        return {
            "priority": "low",
            "action": "Review std container usage versus UE containers in reflected/game-thread-sensitive paths.",
            "reason": f"Detected {qty} std container usages in scanned Unreal modules.",
            "related_rules": ["std_container"],
        }
    return {
        "priority": "low",
        "action": "Review reported code findings and align fixes with Unreal coding standards.",
        "reason": f"Detected {qty} findings for rule '{normalized_rule or 'unknown'}'.",
        "related_rules": [normalized_rule or "unknown"],
    }


def _dedupe_next_actions(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for action in actions:
        key = str(action.get("action") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(action)
    return deduped


def build_code_workflow_output(scan: Dict[str, Any]) -> Dict[str, Any]:
    findings = scan.get("findings")
    if not isinstance(findings, list):
        findings = []

    grouped: Dict[str, List[Dict[str, Any]]] = {
        "informational": [],
        "warnings": [],
        "actionable_issues": [],
    }
    next_actions: List[Dict[str, Any]] = []

    for item in findings:
        if not isinstance(item, dict):
            continue
        rule = str(item.get("rule") or "unknown")
        severity = str(item.get("severity") or "")
        count = int(item.get("count") or 0)
        category = _rule_category(rule, severity)
        enriched = dict(item)
        enriched["category"] = category
        grouped.setdefault(category, []).append(enriched)
        next_actions.append(_unreal_next_action(rule=rule, count=count))

    deduped_actions = _dedupe_next_actions(next_actions)
    counts = {
        "informational": len(grouped.get("informational", [])),
        "warnings": len(grouped.get("warnings", [])),
        "actionable_issues": len(grouped.get("actionable_issues", [])),
    }
    total_findings = sum(counts.values())
    summary_text = (
        f"Scanned {int(scan.get('file_count') or 0)} files and found {total_findings} findings "
        f"({counts['actionable_issues']} actionable, {counts['warnings']} warnings, {counts['informational']} informational)."
    )

    payload: Dict[str, Any] = {
        "contract_version": CODE_WORKFLOW_CONTRACT_VERSION,
        "summary": {
            "text": summary_text,
            "source_root": str(scan.get("source_root") or ""),
            "file_count": int(scan.get("file_count") or 0),
            "line_count": int(scan.get("line_count") or 0),
            "finding_counts": {
                **counts,
                "total": int(total_findings),
            },
        },
        "findings": grouped,
        "next_actions": deduped_actions,
    }
    scan_guards = scan.get("scan_guards")
    if isinstance(scan_guards, dict):
        payload["scan_guards"] = dict(scan_guards)
    targeting = scan.get("targeting")
    if isinstance(targeting, dict):
        payload["targeting"] = dict(targeting)
    return payload


def build_code_workflow_error(message: str, targeting: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "contract_version": CODE_WORKFLOW_CONTRACT_VERSION,
        "summary": {
            "text": str(message or "Code analysis failed."),
            "source_root": "",
            "file_count": 0,
            "line_count": 0,
            "finding_counts": {
                "informational": 0,
                "warnings": 0,
                "actionable_issues": 0,
                "total": 0,
            },
        },
        "findings": {
            "informational": [],
            "warnings": [],
            "actionable_issues": [],
        },
        "next_actions": [
            {
                "priority": "high",
                "action": "Resolve code scan availability first (project root/source path) then rerun analysis.",
                "reason": "Code workflow could not collect source metrics.",
                "related_rules": ["scan_unavailable"],
            }
        ],
    }
    if isinstance(targeting, dict):
        payload["targeting"] = dict(targeting)
    return payload
