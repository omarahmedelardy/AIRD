from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, Tuple

LOGGER = logging.getLogger("aird.mcp")


class RequestOrchestrator:
    """
    Lightweight request classifier for AIRD 2.0 Phase 2.

    This provides deterministic keyword routing with a fallback agent.
    """

    def __init__(self, fallback_agent: str = "sceneagent") -> None:
        self.fallback_agent = str(fallback_agent or "sceneagent").strip().lower()
        self._registry: dict[str, Any] = {}
        self._handlers: dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}

    def register_agent(self, name: str, agent: Any) -> None:
        key = str(name or "").strip().lower()
        if not key:
            return
        self._registry[key] = agent

    def set_handler(
        self, name: str, handler: Callable[[Dict[str, Any]], Dict[str, Any]]
    ) -> None:
        key = str(name or "").strip().lower()
        if not key:
            return
        self._handlers[key] = handler

    def registered_agents(self) -> list[str]:
        return sorted(self._registry.keys())

    def route(
        self, text: str, preferred_agent: str | None = None
    ) -> Tuple[str, float, str]:
        preferred = str(preferred_agent or "").strip().lower()
        if preferred:
            return preferred, 1.0, "user-selected"

        raw_text = str(text or "").strip().lower()
        if not raw_text:
            return self.fallback_agent, 0.0, "empty-input-fallback"

        score_scene = 0
        score_blueprint = 0
        score_code = 0
        score_content = 0

        scene_terms = (
            "scene",
            "actor",
            "spawn",
            "create cube",
            "create sphere",
            "delete actor",
            "move actor",
            "lights",
            "viewport",
            "مشهد",
            "ممثل",
            "اضاءة",
            "إضاءة",
        )
        blueprint_terms = (
            "blueprint",
            "bp ",
            "bp_",
            "asset",
            "add variable",
            "add function",
            "بلوبرنت",
            "بلو برنت",
            "مخطط",
            "انشاء مخطط",
            "إنشاء مخطط",
            "انشئ بلوبرنت",
            "أنشئ بلوبرنت",
            "قراءة المخطط",
            "اقرأ المخطط",
            "تحكم في unreal",
            "التحكم في unreal",
        )
        code_terms = (
            ".cpp",
            ".h",
            "c++",
            "analyze code",
            "source",
            "memory leak",
            "raw pointer",
            "refactor",
            "كود",
            "تحليل الكود",
        )
        content_terms = (
            "/game",
            "content browser",
            "create folder",
            "new folder",
            "create asset",
            "create file",
            "folder",
            "file",
            "asset",
            "مجلد",
            "فولدر",
            "ملف",
            "اصل",
            "أصل",
            "محتوى",
        )
        create_tokens = (
            "create",
            "new",
            "make",
            "انشئ",
            "أنشئ",
            "انشاء",
            "إنشاء",
            "اعمل",
            "قم بإنشاء",
        )

        for term in scene_terms:
            if term in raw_text:
                score_scene += 1
        for term in blueprint_terms:
            if term in raw_text:
                score_blueprint += 1
        for term in code_terms:
            if term in raw_text:
                score_code += 1
        has_blueprint_signal = any(term in raw_text for term in ("blueprint", "bp ", "bp_", "بلوبرنت", "بلو برنت", "مخطط"))
        has_content_term = any(term in raw_text for term in content_terms)
        has_create_token = any(term in raw_text for term in create_tokens)
        if has_content_term and has_create_token and not has_blueprint_signal:
            score_content += 3
        if "/game" in raw_text and not has_blueprint_signal:
            score_content += 2

        if re.search(r"\b(class|struct|template|header|compile)\b", raw_text):
            score_code += 1
        if re.search(r"\b(level|world|mesh|camera|light)\b", raw_text):
            score_scene += 1

        best = max(
            [
                ("sceneagent", score_scene),
                ("blueprintagent", score_blueprint),
                ("codeagent", score_code),
                ("contentagent", score_content),
            ],
            key=lambda item: item[1],
        )
        if best[1] <= 0:
            return self.fallback_agent, 0.0, "no-signal-fallback"

        confidence = min(1.0, 0.4 + (best[1] * 0.2))
        return best[0], confidence, "keyword-classifier"

    def build_decision(
        self, text: str, preferred_agent: str | None = None
    ) -> Dict[str, Any]:
        agent_name, confidence, reason = self.route(text, preferred_agent)
        return {
            "agent": agent_name,
            "confidence": float(confidence),
            "reason": reason,
            "preferred_agent": str(preferred_agent or "").strip().lower() or None,
            "registry": self.registered_agents(),
        }

    def process(
        self,
        *,
        text: str,
        request: Dict[str, Any],
        preferred_agent: str | None = None,
    ) -> Dict[str, Any]:
        request_id = str((request or {}).get("request_id") or "").strip() or "unknown"
        decision = self.build_decision(text, preferred_agent)
        LOGGER.info(
            "ORCHESTRATOR_ROUTE_SELECTED request_id=%s route=%s confidence=%s reason=%s",
            request_id,
            decision.get("agent"),
            decision.get("confidence"),
            decision.get("reason"),
        )
        agent_name = str(decision.get("agent") or self.fallback_agent).lower()
        handler = self._handlers.get(agent_name)
        if handler is None:
            handler = self._handlers.get(self.fallback_agent)
        if handler is None:
            return {"ok": False, "message": f"No handler for agent: {agent_name}"}

        result = handler(request)
        if isinstance(result, dict):
            result.setdefault("routing", decision)
            return result
        return {"ok": False, "message": "Agent handler must return dict", "routing": decision}
