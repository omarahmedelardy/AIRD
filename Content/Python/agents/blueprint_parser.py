from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

_VARIABLE_PATTERN = re.compile(
    r"add\s+variable\s+([a-zA-Z_]\w*)(?:\s+(?:of\s+)?type\s+([a-zA-Z_]\w*))?\s+to\s+(?:blueprint\s+)?([^\n\r]+)",
    flags=re.IGNORECASE,
)
_FUNCTION_PATTERN = re.compile(
    r"add\s+function\s+([a-zA-Z_]\w*)\s+to\s+(?:blueprint\s+)?([^\n\r]+)",
    flags=re.IGNORECASE,
)

_EDIT_ACTIONS = {"add_blueprint_variable", "add_blueprint_function"}
_INTENT_PRIORITY = {
    "add_blueprint_variable": 0,
    "add_blueprint_function": 1,
    "generate_blueprint": 2,
}

PARSER_POLICY = (
    "deterministic_intent_order: explicit edit intents (add variable/function) "
    "always outrank create/generate; within edits, the earliest intent token wins; "
    "ties are resolved by fixed priority variable > function > create."
)

_BLUEPRINT_ALIASES = (
    "blueprint",
    "بلوبرنت",
    "بلو برنت",
)
_CREATE_TOKENS = (
    "create",
    "generate",
    "make",
    "انشاء",
    "إنشاء",
    "انشئ",
    "أنشئ",
)


def _normalize_blueprint_path(path: str) -> str:
    cleaned = str(path or "").strip().strip("\"'")
    cleaned = re.sub(r"^blueprint\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+blueprint$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _collect_intents(text: str, lowered: str) -> List[Tuple[int, str]]:
    intents: List[Tuple[int, str]] = []
    variable_match = re.search(r"\badd\s+variable\b", lowered)
    if variable_match is not None:
        intents.append((int(variable_match.start()), "add_blueprint_variable"))

    function_match = re.search(r"\badd\s+function\b", lowered)
    if function_match is not None:
        intents.append((int(function_match.start()), "add_blueprint_function"))

    has_blueprint_alias = any(alias in lowered for alias in _BLUEPRINT_ALIASES)
    if has_blueprint_alias:
        create_positions = [lowered.find(token.lower()) for token in _CREATE_TOKENS]
        create_positions = [pos for pos in create_positions if pos >= 0]
        if create_positions:
            intents.append((min(create_positions), "generate_blueprint"))

    return intents


def parse_blueprint_command(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    lowered = raw.lower()
    intents = _collect_intents(raw, lowered)
    if not intents:
        return {
            "kind": "none",
            "parser_policy": PARSER_POLICY,
            "intent_candidates": [],
        }

    edit_intents = [item for item in intents if item[1] in _EDIT_ACTIONS]
    ranked = edit_intents if edit_intents else intents
    ranked = sorted(ranked, key=lambda item: (item[0], _INTENT_PRIORITY[item[1]]))
    _, chosen_action = ranked[0]

    if chosen_action == "add_blueprint_variable":
        match = _VARIABLE_PATTERN.search(raw)
        if not match:
            return {
                "kind": "parse_failure",
                "action": "add_blueprint_variable",
                "reason": "Variable command does not match expected parse pattern.",
                "parser_policy": PARSER_POLICY,
                "intent_candidates": [name for _, name in sorted(intents)],
            }
        payload = {
            "variable_name": str(match.group(1) or "").strip(),
            "variable_type": str(match.group(2) or "float").strip().lower() or "float",
            "blueprint_path": _normalize_blueprint_path(match.group(3) or ""),
        }
        return {
            "kind": "action",
            "action": "add_blueprint_variable",
            "payload": payload,
            "parser_policy": PARSER_POLICY,
            "intent_candidates": [name for _, name in sorted(intents)],
        }

    if chosen_action == "add_blueprint_function":
        match = _FUNCTION_PATTERN.search(raw)
        if not match:
            return {
                "kind": "parse_failure",
                "action": "add_blueprint_function",
                "reason": "Function command does not match expected parse pattern.",
                "parser_policy": PARSER_POLICY,
                "intent_candidates": [name for _, name in sorted(intents)],
            }
        payload = {
            "function_name": str(match.group(1) or "").strip(),
            "blueprint_path": _normalize_blueprint_path(match.group(2) or ""),
        }
        return {
            "kind": "action",
            "action": "add_blueprint_function",
            "payload": payload,
            "parser_policy": PARSER_POLICY,
            "intent_candidates": [name for _, name in sorted(intents)],
        }

    return {
        "kind": "action",
        "action": "generate_blueprint",
        "payload": {"prompt": raw},
        "parser_policy": PARSER_POLICY,
        "intent_candidates": [name for _, name in sorted(intents)],
    }
