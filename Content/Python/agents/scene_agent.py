from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from .base_agent import BaseAgent

LOGGER = logging.getLogger("aird.mcp")


class SceneAgent(BaseAgent):
    """Scene-domain wrapper that delegates to the existing scene pipeline."""

    def __init__(self, executor: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
        super().__init__("sceneagent")
        self._executor = executor

    def process(self, request: Dict[str, Any]) -> Dict[str, Any]:
        request_id = str((request or {}).get("request_id") or "").strip() or "unknown"
        LOGGER.info(
            "AGENT_EXECUTION_STARTED request_id=%s agent=%s",
            request_id,
            self.name,
        )
        return self._executor(request)
