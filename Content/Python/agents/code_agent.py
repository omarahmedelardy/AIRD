from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict

from tools.code_analyzer import analyze_source_tree
from .code_workflow import build_code_workflow_error, build_code_workflow_output

from .base_agent import BaseAgent

LOGGER = logging.getLogger("aird.mcp")


class CodeAgent(BaseAgent):
    """
    Code-domain wrapper for Phase 3.

    It provides a local lightweight scan for explicit code-analysis intents,
    and falls back to the existing scene/LLM pipeline otherwise.
    """

    def __init__(
        self,
        project_root_resolver: Callable[[], Path | None],
        fallback_executor: Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> None:
        super().__init__("codeagent")
        self._project_root_resolver = project_root_resolver
        self._fallback_executor = fallback_executor
        self._hard_scan_limit = 800
        self._mode_limits = {
            "explicit": {"soft_limit": 180, "time_budget_ms": 1200},
            "inferred": {"soft_limit": 220, "time_budget_ms": 1800},
            "fallback": {"soft_limit": 400, "time_budget_ms": 3000},
        }

    @staticmethod
    def _extract_project_context(request: Dict[str, Any]) -> Dict[str, Any]:
        context = request.get("request_context")
        if not isinstance(context, dict):
            return {}
        full = context.get("project_context")
        return full if isinstance(full, dict) else {}

    @staticmethod
    def _normalize_relative_target(candidate: str) -> str:
        cleaned = str(candidate or "").strip().strip("\"'").strip(".,;:()[]{}")
        cleaned = cleaned.replace("\\", "/")
        while cleaned.startswith("/"):
            cleaned = cleaned[1:]
        return cleaned

    def _resolve_explicit_target(self, root: Path, text: str) -> Dict[str, Any] | None:
        patterns = [
            r"(?:in|under|inside|target(?:ing)?|scope)\s+((?:Source|Plugins)[A-Za-z0-9_./\\-]+)",
            r"((?:Source|Plugins)[A-Za-z0-9_./\\-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match is None:
                continue
            rel = self._normalize_relative_target(match.group(1))
            if not rel:
                continue
            candidate = (root / rel).resolve()
            if candidate.is_file():
                limits = self._mode_limits["explicit"]
                return {
                    "mode": "explicit",
                    "strategy": "user_text_path_file_parent",
                    "requested_target": rel,
                    "resolved_source_root": str(candidate.parent),
                    "project_context_used": False,
                    "soft_limit": int(limits["soft_limit"]),
                    "hard_limit": int(self._hard_scan_limit),
                    "time_budget_ms": int(limits["time_budget_ms"]),
                }
            if candidate.is_dir():
                limits = self._mode_limits["explicit"]
                return {
                    "mode": "explicit",
                    "strategy": "user_text_path_directory",
                    "requested_target": rel,
                    "resolved_source_root": str(candidate),
                    "project_context_used": False,
                    "soft_limit": int(limits["soft_limit"]),
                    "hard_limit": int(self._hard_scan_limit),
                    "time_budget_ms": int(limits["time_budget_ms"]),
                }
        return None

    def _resolve_inferred_target(
        self, root: Path, text: str, project_context: Dict[str, Any]
    ) -> Dict[str, Any] | None:
        modules = project_context.get("modules")
        if not isinstance(modules, list):
            return None

        lowered = text.lower()
        matches: list[tuple[int, str, str]] = []
        for module in modules:
            if not isinstance(module, dict):
                continue
            module_name = str(module.get("name") or "").strip()
            module_path = str(module.get("path") or "").strip()
            if not module_name or not module_path:
                continue
            token_match = re.search(rf"\b{re.escape(module_name.lower())}\b", lowered)
            if token_match is None:
                continue
            source_root = (root / module_path).resolve()
            if source_root.is_dir():
                matches.append((int(token_match.start()), module_name, str(source_root)))

        if not matches:
            return None

        matches.sort(key=lambda item: (item[0], len(item[1])))
        _, selected_module, selected_root = matches[0]
        limits = self._mode_limits["inferred"]
        return {
            "mode": "inferred",
            "strategy": "project_context_module_match",
            "requested_target": selected_module,
            "resolved_source_root": selected_root,
            "project_context_used": True,
            "soft_limit": int(limits["soft_limit"]),
            "hard_limit": int(self._hard_scan_limit),
            "time_budget_ms": int(limits["time_budget_ms"]),
        }

    def _resolve_scan_target(self, request: Dict[str, Any], text: str) -> Dict[str, Any]:
        root = self._project_root_resolver()
        if root is None:
            return {
                "ok": False,
                "message": "Project root is unavailable for code scan.",
                "targeting": {
                    "mode": "unavailable",
                    "strategy": "project_root_missing",
                    "requested_target": "",
                    "resolved_source_root": "",
                    "project_context_used": False,
                    "soft_limit": 0,
                    "hard_limit": int(self._hard_scan_limit),
                    "time_budget_ms": 0,
                },
            }

        explicit = self._resolve_explicit_target(root, text)
        if explicit is not None:
            source_root = Path(str(explicit["resolved_source_root"]))
            return {"ok": True, "source_root": source_root, "targeting": explicit}

        project_context = self._extract_project_context(request)
        inferred = self._resolve_inferred_target(root, text, project_context)
        if inferred is not None:
            source_root = Path(str(inferred["resolved_source_root"]))
            return {"ok": True, "source_root": source_root, "targeting": inferred}

        fallback_root = (root / "Source").resolve()
        if not fallback_root.exists():
            fallback_root = root.resolve()
        limits = self._mode_limits["fallback"]
        return {
            "ok": True,
            "source_root": fallback_root,
            "targeting": {
                "mode": "fallback",
                "strategy": "broad_source_scan",
                "requested_target": "",
                "resolved_source_root": str(fallback_root),
                "project_context_used": bool(project_context),
                "soft_limit": int(limits["soft_limit"]),
                "hard_limit": int(self._hard_scan_limit),
                "time_budget_ms": int(limits["time_budget_ms"]),
            },
        }

    def _local_scan(self, request: Dict[str, Any], text: str) -> Dict[str, Any]:
        target = self._resolve_scan_target(request, text)
        if not bool(target.get("ok")):
            return {
                "ok": False,
                "message": str(target.get("message") or "Code targeting failed."),
                "targeting": target.get("targeting") or {},
            }

        source_root = target.get("source_root")
        targeting = target.get("targeting") if isinstance(target.get("targeting"), dict) else {}
        if not isinstance(source_root, Path):
            return {"ok": False, "message": "Resolved source root is invalid.", "targeting": targeting}

        soft_limit = int(targeting.get("soft_limit") or self._mode_limits["fallback"]["soft_limit"])
        hard_limit = int(targeting.get("hard_limit") or self._hard_scan_limit)
        time_budget_ms = int(
            targeting.get("time_budget_ms") or self._mode_limits["fallback"]["time_budget_ms"]
        )
        scan = analyze_source_tree(
            source_root,
            max_files=soft_limit,
            hard_max_files=hard_limit,
            time_budget_ms=time_budget_ms,
        )
        if isinstance(scan, dict):
            scan_guards = scan.get("scan_guards")
            if isinstance(scan_guards, dict):
                targeting["truncated_scan"] = bool(scan_guards.get("truncated"))
                targeting["timeout_hit"] = bool(scan_guards.get("timeout_hit"))
                targeting["truncation_reasons"] = list(scan_guards.get("truncation_reasons") or [])
            scan["targeting"] = targeting
        return scan

    def _legacy_local_scan(self) -> Dict[str, Any]:
        root = self._project_root_resolver()
        if root is None:
            return {"ok": False, "message": "Project root is unavailable for code scan."}

        source_root = root / "Source"
        return analyze_source_tree(source_root, max_files=400)

    def process(self, request: Dict[str, Any]) -> Dict[str, Any]:
        request_id = str((request or {}).get("request_id") or "").strip() or "unknown"
        LOGGER.info(
            "AGENT_EXECUTION_STARTED request_id=%s agent=%s",
            request_id,
            self.name,
        )
        text = str(request.get("text") or "").strip().lower()
        if any(token in text for token in ("analyze code", "scan code", "c++", ".cpp", ".h")):
            scan = self._local_scan(request, text)
            targeting = scan.get("targeting") if isinstance(scan, dict) else {}
            if scan.get("ok"):
                code_workflow = build_code_workflow_output(scan)
                summary = code_workflow.get("summary") if isinstance(code_workflow, dict) else {}
                return {
                    "ok": True,
                    "message": str(
                        (summary or {}).get("text")
                        or (
                            f"Code analysis complete. Files: {scan['file_count']}, "
                            f"Lines: {scan['line_count']}, "
                            f"Findings: {len(scan.get('findings', []))}."
                        )
                    ),
                    "provider": "local-code-agent",
                    "model": "none",
                    "scene": request.get("scene") or {"actors": [], "source": "agent"},
                    "scene_stale": False,
                    "knowledge_graph": request.get("knowledge_graph") or {},
                    "actions": [],
                    "usage_tokens": 0,
                    "demo": False,
                    "code_metrics": scan,
                    "code_workflow": code_workflow,
                    "code_targeting": targeting,
                }
            error_message = str(scan.get("message") or "Code analysis failed.")
            return {
                "ok": False,
                "message": error_message,
                "actions": [],
                "usage_tokens": 0,
                "code_workflow": build_code_workflow_error(error_message, targeting),
                "code_targeting": targeting,
            }

        return self._fallback_executor(request)
