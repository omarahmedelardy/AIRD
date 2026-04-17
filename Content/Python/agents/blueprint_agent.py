from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from blueprint_generator import (
    add_function_to_blueprint,
    add_variable_to_blueprint,
    generate_blueprint,
)
from .blueprint_parser import parse_blueprint_command
from .blueprint_workflow import (
    failed_execution_workflow,
    parse_failure_workflow,
    success_workflow,
    validation_failure_workflow,
)

from .base_agent import BaseAgent

LOGGER = logging.getLogger("aird.mcp")


class BlueprintAgent(BaseAgent):
    """
    Blueprint-domain wrapper.

    For Phase 3, it handles explicit blueprint creation intents and falls back
    to the existing scene/LLM pipeline for other requests.
    """

    def __init__(self, fallback_executor: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
        super().__init__("blueprintagent")
        self._fallback_executor = fallback_executor

    def _base_response(
        self,
        request: Dict[str, Any],
        *,
        ok: bool,
        message: str,
        actions: list[Dict[str, Any]],
        workflow: Dict[str, Any],
        parser: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        response = {
            "ok": bool(ok),
            "message": str(message or ""),
            "provider": "local-blueprint-agent",
            "model": "none",
            "scene": request.get("scene") or {"actors": [], "source": "agent"},
            "scene_stale": False,
            "knowledge_graph": request.get("knowledge_graph") or {},
            "actions": actions,
            "usage_tokens": 0,
            "demo": False,
            "blueprint_workflow": workflow,
        }
        if not bool(ok) and isinstance(workflow, dict):
            response["blueprint_error"] = {
                "failure_type": workflow.get("failure_type"),
                "normalized_error_code": workflow.get("normalized_error_code")
                or workflow.get("execution_error_code"),
                "raw_error_code": workflow.get("raw_error_code"),
            }
        if isinstance(parser, dict):
            response["blueprint_parser"] = parser
        return response

    @staticmethod
    def _is_blueprint_path_valid(path: str) -> bool:
        candidate = str(path or "").strip()
        return candidate.startswith("/Game/")

    def process(self, request: Dict[str, Any]) -> Dict[str, Any]:
        request_id = str((request or {}).get("request_id") or "").strip() or "unknown"
        LOGGER.info(
            "AGENT_EXECUTION_STARTED request_id=%s agent=%s",
            request_id,
            self.name,
        )
        text = str(request.get("text") or "").strip()
        parsed = parse_blueprint_command(text)
        parsed_kind = str(parsed.get("kind") or "none")
        parsed_action = str(parsed.get("action") or "").strip().lower()
        parsed_payload = (
            parsed.get("payload") if isinstance(parsed.get("payload"), dict) else {}
        )

        if parsed_kind == "action" and parsed_action == "generate_blueprint":
            prompt = str(parsed_payload.get("prompt") or text or "AIRD Generated Actor")
            result = generate_blueprint(prompt)
            ok = str(result.get("status") or "").lower() == "ok"
            workflow = (
                success_workflow(
                    "generate_blueprint",
                    {"prompt": prompt},
                )
                if ok
                else failed_execution_workflow(
                    "generate_blueprint",
                    error_code=str(result.get("error") or "execution_failure"),
                    message=str(result.get("message") or ""),
                    details={"prompt": prompt},
                )
            )
            return self._base_response(
                request,
                ok=ok,
                message=str(result.get("message") or ""),
                actions=[{"type": "generate_blueprint", "prompt": prompt}],
                workflow=workflow,
                parser=parsed,
            )

        if parsed_kind == "parse_failure":
            parse_action = parsed_action or "add_blueprint_variable"
            parse_reason = str(
                parsed.get("reason")
                or "Blueprint command does not match expected parse pattern."
            )
            action_hint = (
                "variable"
                if parse_action == "add_blueprint_variable"
                else "function"
            )
            return self._base_response(
                request,
                ok=False,
                message=(
                    f"Unable to parse {action_hint} command. "
                    f"Follow the expected pattern and retry."
                ),
                actions=[],
                workflow=parse_failure_workflow(
                    parse_action,
                    parse_reason,
                ),
                parser=parsed,
            )

        if parsed_kind == "action" and parsed_action == "add_blueprint_variable":
            var_name = str(parsed_payload.get("variable_name") or "").strip()
            var_type = str(parsed_payload.get("variable_type") or "float").strip()
            blueprint_path = str(parsed_payload.get("blueprint_path") or "").strip()
            if not self._is_blueprint_path_valid(blueprint_path):
                return self._base_response(
                    request,
                    ok=False,
                    message="Blueprint path must start with /Game/ for validation to pass.",
                    actions=[],
                    workflow=validation_failure_workflow(
                        "add_blueprint_variable",
                        "Blueprint path failed validation.",
                        code="invalid_blueprint_path",
                        details={
                            "blueprint_path": blueprint_path,
                            "variable_name": var_name,
                            "variable_type": var_type,
                        },
                    ),
                    parser=parsed,
                )
            result = add_variable_to_blueprint(blueprint_path, var_name, var_type)
            ok = str(result.get("status") or "").lower() == "ok"
            details = {
                "blueprint_path": blueprint_path,
                "variable_name": var_name,
                "variable_type": var_type,
            }
            workflow = (
                success_workflow("add_blueprint_variable", details)
                if ok
                else failed_execution_workflow(
                    "add_blueprint_variable",
                    error_code=str(result.get("error") or "execution_failure"),
                    message=str(result.get("message") or ""),
                    details=details,
                )
            )
            return self._base_response(
                request,
                ok=ok,
                message=str(result.get("message") or ""),
                actions=[
                    {
                        "type": "edit_blueprint_variable",
                        "blueprint_path": blueprint_path,
                        "variable_name": var_name,
                        "variable_type": var_type,
                    }
                ],
                workflow=workflow,
                parser=parsed,
            )

        if parsed_kind == "action" and parsed_action == "add_blueprint_function":
            fn_name = str(parsed_payload.get("function_name") or "").strip()
            blueprint_path = str(parsed_payload.get("blueprint_path") or "").strip()
            if not self._is_blueprint_path_valid(blueprint_path):
                return self._base_response(
                    request,
                    ok=False,
                    message="Blueprint path must start with /Game/ for validation to pass.",
                    actions=[],
                    workflow=validation_failure_workflow(
                        "add_blueprint_function",
                        "Blueprint path failed validation.",
                        code="invalid_blueprint_path",
                        details={
                            "blueprint_path": blueprint_path,
                            "function_name": fn_name,
                        },
                    ),
                    parser=parsed,
                )
            result = add_function_to_blueprint(blueprint_path, fn_name)
            ok = str(result.get("status") or "").lower() == "ok"
            details = {
                "blueprint_path": blueprint_path,
                "function_name": fn_name,
            }
            workflow = (
                success_workflow("add_blueprint_function", details)
                if ok
                else failed_execution_workflow(
                    "add_blueprint_function",
                    error_code=str(result.get("error") or "execution_failure"),
                    message=str(result.get("message") or ""),
                    details=details,
                )
            )
            return self._base_response(
                request,
                ok=ok,
                message=str(result.get("message") or ""),
                actions=[
                    {
                        "type": "edit_blueprint_function",
                        "blueprint_path": blueprint_path,
                        "function_name": fn_name,
                    }
                ],
                workflow=workflow,
                parser=parsed,
            )

        return self._fallback_executor(request)
