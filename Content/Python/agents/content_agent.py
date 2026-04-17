from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from unreal_runtime_bridge_client import call_runtime_bridge
from .base_agent import BaseAgent
from .content_parser import parse_content_command

LOGGER = logging.getLogger("aird.mcp")


class ContentAgent(BaseAgent):
    """Deterministic content-browser execution agent for /Game operations."""

    def __init__(self, fallback_executor: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
        super().__init__("contentagent")
        self._fallback_executor = fallback_executor

    def _base_response(
        self,
        request: Dict[str, Any],
        *,
        ok: bool,
        message: str,
        error: str = "",
        parser: Dict[str, Any] | None = None,
        payload: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        response: Dict[str, Any] = {
            "ok": bool(ok),
            "message": str(message or ""),
            "provider": "local-content-agent",
            "model": "none",
            "scene": request.get("scene") or {"actors": [], "source": "agent"},
            "scene_stale": False,
            "knowledge_graph": request.get("knowledge_graph") or {},
            "actions": [payload] if isinstance(payload, dict) and payload else [],
            "usage_tokens": 0,
            "demo": False,
        }
        if error:
            response["error"] = str(error)
        if isinstance(parser, dict):
            response["content_parser"] = parser
        return response

    def process(self, request: Dict[str, Any]) -> Dict[str, Any]:
        request_id = str((request or {}).get("request_id") or "").strip() or "unknown"
        LOGGER.info(
            "AGENT_EXECUTION_STARTED request_id=%s agent=%s",
            request_id,
            self.name,
        )
        text = str(request.get("text") or "").strip()
        parsed = parse_content_command(text)
        kind = str(parsed.get("kind") or "none")
        action = str(parsed.get("action") or "").strip().lower()
        payload = parsed.get("payload") if isinstance(parsed.get("payload"), dict) else {}

        if kind == "none":
            return self._base_response(
                request,
                ok=False,
                error="parse_failure",
                message="Content command could not be parsed into a deterministic /Game action.",
                parser=parsed,
            )

        if kind == "parse_failure":
            return self._base_response(
                request,
                ok=False,
                error="parse_failure",
                message=str(parsed.get("reason") or "Content command parse failure."),
                parser=parsed,
                payload=payload,
            )

        if action == "create_content_folder":
            folder_path = str(payload.get("target_folder_path") or "").strip()
            result = call_runtime_bridge(
                "create_content_folder",
                {"folder_path": folder_path},
                request_id=request_id,
            )
            if bool(result.get("ok")):
                return self._base_response(
                    request,
                    ok=True,
                    message=str(result.get("message") or f"Folder created: {folder_path}"),
                    parser=parsed,
                    payload=payload,
                )
            return self._base_response(
                request,
                ok=False,
                error=str(result.get("error") or "execution_failure"),
                message=str(result.get("message") or f"Failed to create folder: {folder_path}"),
                parser=parsed,
                payload=payload,
            )

        if action == "create_asset_placeholder":
            result = call_runtime_bridge(
                "create_content_asset_placeholder",
                {
                    "target_path": str(payload.get("target_path") or "/Game"),
                    "asset_name": str(payload.get("asset_name") or ""),
                    "inferred_type": str(payload.get("inferred_type") or "asset_placeholder"),
                },
                request_id=request_id,
            )
            return self._base_response(
                request,
                ok=bool(result.get("ok")),
                error=str(result.get("error") or ""),
                message=str(
                    result.get("message")
                    or "Asset placeholder operation is unavailable."
                ),
                parser=parsed,
                payload=payload,
            )

        return self._base_response(
            request,
            ok=False,
            error="unsupported_content_operation",
            message=f"Unsupported content action: {action or 'unknown'}",
            parser=parsed,
            payload=payload,
        )
