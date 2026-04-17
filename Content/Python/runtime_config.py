from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

DEFAULT_CONFIG: Dict[str, Any] = {
    "mcp_websocket_port": 8765,
    "remote_control_http_port": 30010,
    "legacy_port": 30000,
    # Optional AIRD 2.0 UI controls (additive and disabled by default).
    "enable_agent_selector_ui": False,
    "enable_history_ui": False,
}

MIN_PORT = 1024
MAX_PORT = 65535


def _config_path() -> Path:
    # Content/Python/runtime_config.py -> plugin root/config.json
    return Path(__file__).resolve().parents[2] / "config.json"


def _to_port(value: Any, fallback: int) -> int:
    try:
        port = int(value)
    except Exception:
        return int(fallback)
    if MIN_PORT <= port <= MAX_PORT:
        return port
    return int(fallback)


def _to_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        norm = value.strip().lower()
        if norm in {"1", "true", "yes", "on"}:
            return True
        if norm in {"0", "false", "no", "off"}:
            return False
    return bool(fallback)


def validate_config(raw: Dict[str, Any] | None) -> Dict[str, Any]:
    raw = raw or {}
    return {
        "mcp_websocket_port": _to_port(
            raw.get("mcp_websocket_port"), DEFAULT_CONFIG["mcp_websocket_port"]
        ),
        "remote_control_http_port": _to_port(
            raw.get("remote_control_http_port"),
            DEFAULT_CONFIG["remote_control_http_port"],
        ),
        "legacy_port": _to_port(raw.get("legacy_port"), DEFAULT_CONFIG["legacy_port"]),
        "enable_agent_selector_ui": _to_bool(
            raw.get("enable_agent_selector_ui"),
            DEFAULT_CONFIG["enable_agent_selector_ui"],
        ),
        "enable_history_ui": _to_bool(
            raw.get("enable_history_ui"),
            DEFAULT_CONFIG["enable_history_ui"],
        ),
    }


def load_runtime_config() -> Dict[str, Any]:
    path = _config_path()
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            return dict(DEFAULT_CONFIG)
        return validate_config(parsed)
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_runtime_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    validated = validate_config(raw)
    path = _config_path()
    path.write_text(json.dumps(validated, indent=2), encoding="utf-8")
    return validated
