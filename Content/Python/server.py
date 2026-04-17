from __future__ import annotations

import asyncio
import ast
import hmac
import json
import logging
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

import websockets
from websockets.exceptions import ConnectionClosed

from agents.blueprint_agent import BlueprintAgent
from agents.content_agent import ContentAgent
from agents.code_agent import CodeAgent
from agents.orchestrator import RequestOrchestrator
from agents.scene_agent import SceneAgent
from blueprint_generator import generate_blueprint
from knowledge_graph import build_spatial_graph
from memory.memory_manager import MemoryManager
from run_utils import bridge_call, try_import_unreal
from runtime_config import DEFAULT_CONFIG, load_runtime_config, save_runtime_config
from scene_perception import (
    REMOTE_CONTROL_TIMEOUT_SEC,
    capture_viewport_base64,
    get_scene_context,
)
from unreal_runtime_bridge_client import (
    HEARTBEAT_MAX_AGE_SEC,
    REQUEST_TIMEOUT_SEC,
    call_runtime_bridge,
    is_runtime_bridge_connected,
    read_runtime_bridge_heartbeat,
)

LOGGER = logging.getLogger("aird.mcp")

JSONRPC_VERSION = "2.0"
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
TOGETHER_API_URL = "https://api.together.xyz/v1/chat/completions"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
PROVIDER_NAMES = {
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "anthropic": "Anthropic",
    "together": "Together AI",
    "ollama": "Ollama",
    "lmstudio": "LM Studio",
}

PROVIDER_ENV_KEYS = {
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "together": "TOGETHER_API_KEY",
}

PROVIDER_DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "openrouter": "openai/gpt-4o-mini",
    "anthropic": "claude-3-5-sonnet-latest",
    "together": "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
    "ollama": "llama3",
    "lmstudio": "local-model",
}

MODEL_ALIASES = {
    "openai": {
        "gpt-4o": "gpt-4o",
        "gpt-4o mini": "gpt-4o-mini",
        "gpt-4 turbo": "gpt-4-turbo",
        "gpt-4": "gpt-4o",
        "o3-mini": "o3-mini",
        "o1-mini": "o1-mini",
        "o1-preview": "o1-preview",
    },
    "openrouter": {
        "gpt-4o": "openai/gpt-4o",
        "gpt-4o mini": "openai/gpt-4o-mini",
        "gpt-4": "openai/gpt-4o",
        "gemini 2.0 flash": "google/gemini-2.0-flash-001",
        "deepseek r1": "deepseek/deepseek-r1",
        "o3-mini": "openai/o3-mini",
    },
    "anthropic": {
        "claude 3.7 sonnet": "claude-3-7-sonnet-latest",
        "claude 3.5 haiku": "claude-3-5-haiku-latest",
        "claude sonnet": "claude-3-7-sonnet-latest",
        "claude haiku": "claude-3-5-haiku-latest",
    },
    "together": {
        "llama 3.1 405b instruct": "meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
        "deepseek coder v2": "deepseek-ai/DeepSeek-V3",
    },
}

DEFAULT_CONTEXT_SERVER_URL = os.getenv(
    "AIRD_CONTEXT_SERVER_URL", "http://127.0.0.1:8787"
).strip()
SCENE_SYNC_INTERVAL_SEC = float(os.getenv("AIRD_SCENE_SYNC_INTERVAL", "2.0") or "2.0")
MISSING_SCENE_CONTEXT_MESSAGE = (
    "Missing Scene Context - Cannot analyze Unreal environment."
)
MAX_SCENE_SYNC_RETRIES = 3
HEARTBEAT_INTERVAL_SEC = float(os.getenv("AIRD_HEARTBEAT_INTERVAL", "0") or "0")
SCENE_SYNC_STARTUP_DELAY_SEC = float(
    os.getenv("AIRD_SCENE_SYNC_STARTUP_DELAY", "8.0") or "8.0"
)
SCENE_READ_GRACE_SEC = float(os.getenv("AIRD_SCENE_READ_GRACE_SEC", "20.0") or "20.0")
REMOTE_CONTROL_STARTUP_DELAY_SEC = float(
    os.getenv("AIRD_REMOTE_CONTROL_STARTUP_DELAY", "5.0") or "5.0"
)
LAST_VALID_SCENE: Optional[Dict[str, Any]] = None
SENSITIVE_KEYS = {"api_key", "authorization", "token", "access_token"}
SERVER_START_MONOTONIC = time.monotonic()
LAST_GRACE_LOG_MONOTONIC = 0.0
_ORCHESTRATOR: Optional[RequestOrchestrator] = None
_MEMORY_MANAGER: Optional[MemoryManager] = None
_SCENE_AGENT: Optional[SceneAgent] = None
_BLUEPRINT_AGENT: Optional[BlueprintAgent] = None
_CODE_AGENT: Optional[CodeAgent] = None
_CONTENT_AGENT: Optional[ContentAgent] = None
CONTEXT_LENGTH_ERROR_RE = re.compile(
    r"context length exceeded|maximum context length|too many tokens|prompt is too long|context window",
    re.IGNORECASE,
)
MUTATING_RPC_METHODS = frozenset(
    {
        "update_runtime_config",
        "clear_history",
        "apply_scene_perception_fix",
    }
)
RPC_AUTH_ENV_KEYS = ("AIRD_RPC_AUTH_TOKEN", "AIRD_MCP_AUTH_TOKEN")
RPC_AUTH_CONFIG_KEYS = ("rpc_auth_token", "mcp_rpc_auth_token")
ACTION_RESPONSE_SCHEMA_VERSION = "aird.action.v1"
ACTION_RESPONSE_CONTRACT_METHOD = "get_action_response_contract"
RELIABILITY_PROFILE_SCHEMA_VERSION = "aird.reliability.v1"
RELIABILITY_PROFILE_CONTRACT_METHOD = "get_reliability_profile"
PROJECT_CONTEXT_RPC_METHOD = "get_project_context"
PROJECT_CONTEXT_CACHE_TTL_SEC = float(
    os.getenv("AIRD_PROJECT_CONTEXT_CACHE_TTL", "10.0") or "10.0"
)
_PROJECT_CONTEXT_CACHE: Optional[Dict[str, Any]] = None
_PROJECT_CONTEXT_CACHE_MONOTONIC = 0.0
SCENE_CONTEXT_CACHE_TTL_SEC = float(
    os.getenv("AIRD_SCENE_CONTEXT_CACHE_TTL", "1.2") or "1.2"
)
SCENE_CONTEXT_STALE_CACHE_TTL_SEC = float(
    os.getenv("AIRD_SCENE_CONTEXT_STALE_CACHE_TTL", "0.35") or "0.35"
)
_SCENE_CONTEXT_CACHE: Optional[Dict[str, Any]] = None
_SCENE_CONTEXT_CACHE_STATE = "non_cacheable_response"
_SCENE_CONTEXT_CACHE_MONOTONIC = 0.0
_SCENE_CONTEXT_CACHE_LOCK = threading.Lock()
FALLBACK_TIMEOUT_MIN_SEC = float(os.getenv("AIRD_FALLBACK_TIMEOUT_MIN_SEC", "1.0") or "1.0")
FALLBACK_TIMEOUT_MAX_SEC = float(os.getenv("AIRD_FALLBACK_TIMEOUT_MAX_SEC", "90.0") or "90.0")
FALLBACK_RETRY_ATTEMPTS = int(os.getenv("AIRD_FALLBACK_RETRY_ATTEMPTS", "3") or "3")
FALLBACK_RETRY_BASE_DELAY_SEC = float(
    os.getenv("AIRD_FALLBACK_RETRY_BASE_DELAY_SEC", "0.25") or "0.25"
)
FALLBACK_RETRY_MAX_DELAY_SEC = float(
    os.getenv("AIRD_FALLBACK_RETRY_MAX_DELAY_SEC", "1.5") or "1.5"
)


def _action_response_contract_documentation() -> Dict[str, Any]:
    """
    Unified action-oriented response contract documentation.

    Backward compatibility policy:
    - Existing JSON-RPC envelope remains unchanged:
      {"jsonrpc": "2.0", "id": <id>, "result": {...}}.
    - Existing command packets remain valid for current UI consumers.
    - Action contract fields are additive, explicit, and safe to ignore.
    """

    return {
        "ok": True,
        "schema_version": ACTION_RESPONSE_SCHEMA_VERSION,
        "method": ACTION_RESPONSE_CONTRACT_METHOD,
        "owner": "Content/Python/server.py",
        "routing_decision_order": [
            "orchestrator_intent_selection",
            "capability_matrix_validation",
            "executor_dispatch",
            "result_normalization",
        ],
        "response_contract": {
            "required_fields": ["ok", "status", "message"],
            "recommended_fields": [
                "error_code",
                "routing",
                "runtime_status",
                "trace",
                "result",
                "next_step",
            ],
            "optional_fields": [
                "schema_version",
                "request_id",
                "diagnostics",
                "capabilities",
            ],
            "status_values": ["success", "partial", "warning", "error", "unavailable"],
        },
        "ui_response_mapping": {
            "goal": "Deterministic UI rendering for action cards using result + next_step + diagnostics.",
            "classification_order": [
                "unavailable_or_capability_limited",
                "error",
                "warning",
                "partial",
                "success",
            ],
            "classification_rules": {
                "unavailable_or_capability_limited": {
                    "when": "status == 'unavailable' OR error_code in ['unreal_runtime_unavailable','capability_limited','editor_only','unsupported']",
                    "ui_state": "unavailable",
                    "badge_tone": "neutral",
                    "result_priority": "show_message_first",
                    "next_step_required": True,
                    "diagnostics_required": True,
                },
                "error": {
                    "when": "ok == false OR status == 'error'",
                    "ui_state": "error",
                    "badge_tone": "danger",
                    "result_priority": "show_error_code_then_message",
                    "next_step_required": True,
                    "diagnostics_required": True,
                },
                "warning": {
                    "when": "status == 'warning' OR diagnostics contains warning-level entries",
                    "ui_state": "warning",
                    "badge_tone": "warning",
                    "result_priority": "show_result_with_warning_banner",
                    "next_step_required": False,
                    "diagnostics_required": True,
                },
                "partial": {
                    "when": "status == 'partial'",
                    "ui_state": "partial",
                    "badge_tone": "info",
                    "result_priority": "show_partial_result_and_limits",
                    "next_step_required": True,
                    "diagnostics_required": True,
                },
                "success": {
                    "when": "ok == true AND status == 'success'",
                    "ui_state": "success",
                    "badge_tone": "success",
                    "result_priority": "show_primary_result",
                    "next_step_required": False,
                    "diagnostics_required": False,
                },
            },
            "field_to_ui_slots": {
                "result": {
                    "slot": "action_result",
                    "fallback_order": ["result", "message"],
                    "required_for_states": ["success", "partial", "warning"],
                },
                "next_step": {
                    "slot": "assistant_next_step",
                    "fallback_order": ["next_step", "message"],
                    "required_for_states": ["partial", "error", "unavailable"],
                },
                "diagnostics": {
                    "slot": "diagnostics_panel",
                    "fallback_order": ["diagnostics", "trace", "runtime_status"],
                    "required_for_states": ["partial", "warning", "error", "unavailable"],
                },
            },
            "rendering_notes": [
                "Never show success badge when classification resolves to error/unavailable.",
                "When next_step is required and missing, fallback to a deterministic generic guidance string.",
                "Unknown additive fields must not break rendering; clients should ignore what they do not understand.",
            ],
        },
        "backward_compatibility": {
            "jsonrpc_envelope": True,
            "legacy_command_packets_supported": True,
            "unknown_fields_must_be_ignored_by_clients": True,
            "field_removals_allowed": False,
            "compatibility_note": "Legacy clients remain supported because the JSON-RPC envelope and existing result fields are unchanged; new fields are additive only.",
        },
        "example_alignment": {
            "policy": "planned-near-term-and-current",
            "note": "Examples mirror current outputs or near-term Phase 1 fields only; they are not speculative long-term schema.",
        },
        "examples": {
            "scene_success": {
                "ok": True,
                "status": "success",
                "error_code": None,
                "message": "Scene context collected.",
                "routing": {"intent": "scene", "agent": "sceneagent"},
                "runtime_status": {"unreal_runtime_connected": True},
                "trace": [
                    {
                        "order": 1,
                        "source": "runtime_bridge_editor_actor_subsystem",
                        "status": "success",
                        "reason": "editor_native_primary",
                    }
                ],
                "result": {"actor_count": 10, "scene_source": "runtime_bridge_editor_actor_subsystem"},
                "next_step": None,
            },
            "scene_failure": {
                "ok": False,
                "status": "error",
                "error_code": "unreal_runtime_unavailable",
                "message": "Cannot collect scene context: Unreal runtime is disconnected.",
                "routing": {"intent": "scene", "agent": "sceneagent"},
                "runtime_status": {"unreal_runtime_connected": False},
                "trace": [
                    {
                        "order": 1,
                        "source": "runtime_bridge_editor_actor_subsystem",
                        "status": "failed",
                        "reason": "runtime_bridge_disconnected",
                    }
                ],
                "result": None,
                "next_step": "Start AIRD Engine inside Unreal Editor and retry.",
            },
            "blueprint_success": {
                "ok": True,
                "status": "success",
                "error_code": None,
                "message": "Blueprint variable added successfully.",
                "routing": {"intent": "blueprint", "agent": "blueprintagent"},
                "runtime_status": {"unreal_runtime_connected": True},
                "trace": [{"order": 1, "source": "airdb_bridge", "status": "success"}],
                "result": {"operation": "add_variable", "blueprint_path": "/Game/TestBP"},
                "next_step": None,
            },
            "blueprint_failure": {
                "ok": False,
                "status": "error",
                "error_code": "duplicate_variable_name",
                "message": "Variable already exists in blueprint.",
                "routing": {"intent": "blueprint", "agent": "blueprintagent"},
                "runtime_status": {"unreal_runtime_connected": True},
                "trace": [{"order": 1, "source": "airdb_bridge", "status": "failed"}],
                "result": {"operation": "add_variable", "blueprint_path": "/Game/TestBP"},
                "next_step": "Use a different variable name.",
            },
            "code_success": {
                "ok": True,
                "status": "success",
                "error_code": None,
                "message": "Code analysis completed.",
                "routing": {"intent": "code", "agent": "codeagent"},
                "runtime_status": {"capability_ready": True},
                "trace": [{"order": 1, "source": "project_index", "status": "success"}],
                "result": {"files_scanned": 100, "issues_found": 4},
                "next_step": None,
            },
            "code_failure": {
                "ok": False,
                "status": "error",
                "error_code": "project_index_unavailable",
                "message": "Code workflow failed: project index is not ready.",
                "routing": {"intent": "code", "agent": "codeagent"},
                "runtime_status": {"capability_ready": False},
                "trace": [{"order": 1, "source": "project_index", "status": "failed"}],
                "result": None,
                "next_step": "Initialize project index, then retry.",
            },
            "partial_capability_limited": {
                "ok": True,
                "status": "partial",
                "error_code": "capability_limited",
                "message": "Request partially handled; mutation capability is unavailable.",
                "routing": {"intent": "blueprint", "agent": "blueprintagent"},
                "runtime_status": {"unreal_runtime_connected": True, "capability_ready": False},
                "trace": [{"order": 1, "source": "airdb_bridge", "status": "failed"}],
                "result": {"applied": False},
                "next_step": "Use supported operation or update runtime capability.",
            },
        },
    }


def _runtime_config() -> Dict[str, Any]:
    return load_runtime_config()


def _code_workflow_guard_limits() -> Dict[str, Any]:
    defaults = {
        "hard_limit": 800,
        "modes": {
            "explicit": {"soft_limit": 180, "time_budget_ms": 1200},
            "inferred": {"soft_limit": 220, "time_budget_ms": 1800},
            "fallback": {"soft_limit": 400, "time_budget_ms": 3000},
        },
    }
    agent = _CODE_AGENT
    if agent is None:
        return defaults
    mode_limits = getattr(agent, "_mode_limits", None)
    hard_limit = getattr(agent, "_hard_scan_limit", None)
    if not isinstance(mode_limits, dict):
        return defaults
    out_modes: Dict[str, Dict[str, int]] = {}
    for mode in ("explicit", "inferred", "fallback"):
        raw = mode_limits.get(mode)
        if not isinstance(raw, dict):
            out_modes[mode] = dict(defaults["modes"][mode])
            continue
        out_modes[mode] = {
            "soft_limit": int(raw.get("soft_limit") or defaults["modes"][mode]["soft_limit"]),
            "time_budget_ms": int(raw.get("time_budget_ms") or defaults["modes"][mode]["time_budget_ms"]),
        }
    return {
        "hard_limit": int(hard_limit or defaults["hard_limit"]),
        "modes": out_modes,
    }


def _reliability_profile_documentation() -> Dict[str, Any]:
    code_limits = _code_workflow_guard_limits()
    code_modes = code_limits.get("modes") if isinstance(code_limits.get("modes"), dict) else {}
    explicit = code_modes.get("explicit") if isinstance(code_modes.get("explicit"), dict) else {}
    inferred = code_modes.get("inferred") if isinstance(code_modes.get("inferred"), dict) else {}
    fallback = code_modes.get("fallback") if isinstance(code_modes.get("fallback"), dict) else {}
    code_hard_limit = int(code_limits.get("hard_limit") or 800)

    scene_sync_attempt_timeout_ms = 6000
    scene_sync_hard_timeout_ms = int(MAX_SCENE_SYNC_RETRIES * scene_sync_attempt_timeout_ms)
    runtime_bridge_timeout_ms = int(max(0.25, float(REQUEST_TIMEOUT_SEC)) * 1000.0)
    heartbeat_stale_after_ms = int(max(0.5, float(HEARTBEAT_MAX_AGE_SEC)) * 1000.0)
    remote_control_timeout_ms = int(max(0.2, float(REMOTE_CONTROL_TIMEOUT_SEC)) * 1000.0)

    return {
        "ok": True,
        "schema_version": RELIABILITY_PROFILE_SCHEMA_VERSION,
        "method": RELIABILITY_PROFILE_CONTRACT_METHOD,
        "scope": "core_assistant_operations_only",
        "policy": {
            "target_latency_ms": "P50/P90 target under normal editor load.",
            "hard_timeout_ms": "Non-negotiable upper bound enforced by current guard/timeout values.",
            "degraded_mode": "Behavior when target/hard limits are exceeded or capability is partial.",
            "acceptable_partial_result": "Structured partial output allowed without false success.",
        },
        "operations": {
            "runtime_status_poll": {
                "target_latency_ms": 300,
                "hard_timeout_ms": 2000,
                "degraded_mode": "return status_semantics=unavailable/partially_ready with runtime_bridge reason",
                "acceptable_partial_result": "mcp_online=true with unreal_runtime_connected=false is valid partial status",
                "implementation_basis": "get_runtime_status + remote_control probe timeout(2.0s)",
            },
            "scene_context_acquisition": {
                "target_latency_ms": 700,
                "hard_timeout_ms": max(3000, remote_control_timeout_ms),
                "degraded_mode": "preserve last valid editor-native snapshot as stale when remote control returns 0 actors",
                "acceptable_partial_result": "scene_stale=true with explicit source_trace/fallback reason",
                "implementation_basis": "editor-native first, runtime bridge scene timeout(3.0s), remote control timeout",
            },
            "blueprint_runtime_mutation": {
                "target_latency_ms": 1200,
                "hard_timeout_ms": runtime_bridge_timeout_ms,
                "degraded_mode": "return unreal_runtime_unavailable or capability_limited (no synthetic success)",
                "acceptable_partial_result": "status=partial only when non-mutating portion succeeds and mutation is capability-limited",
                "implementation_basis": "unreal_runtime_bridge_client REQUEST_TIMEOUT_SEC + normalized blueprint failure mapping",
            },
            "code_workflow_explicit_target": {
                "target_latency_ms": int(explicit.get("time_budget_ms") or 1200),
                "hard_timeout_ms": int(explicit.get("time_budget_ms") or 1200),
                "degraded_mode": "truncate scan by timeout/soft_limit and surface scan_guards",
                "acceptable_partial_result": "code_workflow returned with truncated=true and reasons list",
                "scan_limits": {
                    "soft_limit": int(explicit.get("soft_limit") or 180),
                    "hard_limit": code_hard_limit,
                },
                "implementation_basis": "CodeAgent explicit mode limits + analyze_source_tree guards",
            },
            "code_workflow_inferred_or_fallback": {
                "target_latency_ms": int(inferred.get("time_budget_ms") or 1800),
                "hard_timeout_ms": int(fallback.get("time_budget_ms") or 3000),
                "degraded_mode": "fallback broad scan remains bounded by hard file limit and time budget",
                "acceptable_partial_result": "targeting includes truncated_scan/timeout_hit and workflow keeps actionable output",
                "scan_limits": {
                    "inferred_soft_limit": int(inferred.get("soft_limit") or 220),
                    "fallback_soft_limit": int(fallback.get("soft_limit") or 400),
                    "hard_limit": code_hard_limit,
                },
                "implementation_basis": "CodeAgent inferred/fallback mode limits + T050 scan guards",
            },
            "scene_sync_to_context_server": {
                "target_latency_ms": scene_sync_attempt_timeout_ms,
                "hard_timeout_ms": scene_sync_hard_timeout_ms,
                "degraded_mode": "retry with bounded attempts; keep main command response available even if sync fails",
                "acceptable_partial_result": "ok=false sync payload with explicit error while command result remains structured",
                "implementation_basis": "MAX_SCENE_SYNC_RETRIES x _sync_scene_snapshot timeout(6s)",
            },
        },
        "reliability_slos": {
            "runtime_status_freshness_slo": {
                "target": ">=99% of runtime status snapshots should be fresh within heartbeat max age.",
                "measurement_window": "rolling 15 minutes",
                "freshness_threshold_ms": heartbeat_stale_after_ms,
                "degraded_mode": "runtime_state becomes unavailable with reason=heartbeat_stale",
            },
            "core_command_response_slo": {
                "target": ">=99% of core command responses are structured (status/message/error_code or explicit diagnostics).",
                "measurement_window": "rolling 24 hours",
                "degraded_mode": "must still return deterministic failure_type; no false success",
            },
            "bounded_scan_slo": {
                "target": "100% of code scans must honor hard file/time guard limits.",
                "measurement_window": "per request",
                "degraded_mode": "return truncated scan with explicit truncation_reasons",
            },
        },
        "non_editor_fallback_retry_policy": {
            "scope": [
                "context_server_health",
                "context_server_llm_chat",
                "context_server_trim_memory",
                "provider_http_chat_calls",
            ],
            "timeout_normalization": {
                "min_sec": max(0.2, float(FALLBACK_TIMEOUT_MIN_SEC)),
                "max_sec": max(
                    max(0.2, float(FALLBACK_TIMEOUT_MIN_SEC)),
                    float(FALLBACK_TIMEOUT_MAX_SEC),
                ),
            },
            "backoff": {
                "max_attempts": max(1, int(FALLBACK_RETRY_ATTEMPTS)),
                "base_delay_sec": max(0.05, float(FALLBACK_RETRY_BASE_DELAY_SEC)),
                "max_delay_sec": max(
                    max(0.05, float(FALLBACK_RETRY_BASE_DELAY_SEC)),
                    float(FALLBACK_RETRY_MAX_DELAY_SEC),
                ),
                "strategy": "exponential_no_jitter",
            },
            "failure_classes": {
                "retryable_timeout": "Timeouts/408/504 -> retry with backoff.",
                "transient_failure": "429/5xx and transient socket/URL issues -> retry with backoff.",
                "hard_failure": "Non-transient transport/provider failure -> fail after current attempt.",
                "immediate_no_retry_condition": "401/403/404/422/invalid payload -> fail fast with no retry.",
            },
        },
        "backward_compatibility": {
            "existing_rpc_methods_unchanged": True,
            "contract_additive_only": True,
            "legacy_clients_may_ignore_profile": True,
        },
        "lightweight_cache_strategy": {
            "scene_context": {
                "cacheable_snapshot": "valid scene payload with non-pending source and required context",
                "stale_snapshot": "cached editor-native snapshot marked stale or source suffix '-cached'",
                "non_cacheable_response": "pending/unavailable/invalid scene payload",
                "cache_invalidation_triggers": [
                    "force_scene_refresh",
                    "ttl_expired",
                    "non_cacheable_response",
                ],
            },
            "project_context": {
                "cacheable_snapshot": "collector payload returned successfully",
                "stale_snapshot": "cached payload with diagnostics entries (advisory partial metadata)",
                "non_cacheable_response": "collector error or invalid payload",
                "cache_invalidation_triggers": [
                    "project_context_refresh",
                    "ttl_expired",
                    "collector_error",
                ],
            },
        },
    }


def _raw_plugin_config() -> Dict[str, Any]:
    config_path = _plugin_root() / "config.json"
    if not config_path.exists():
        return {}
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_project_context_mode(raw_mode: Any, default: str = "summary") -> str:
    mode = str(raw_mode or default).strip().lower()
    if mode in {"summary", "full", "none"}:
        return mode
    return str(default).strip().lower() or "summary"


def _project_context_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    modules = payload.get("modules")
    plugins = payload.get("plugins")
    source_roots = payload.get("source_roots")
    enabled_plugins = 0
    if isinstance(plugins, list):
        enabled_plugins = sum(1 for item in plugins if isinstance(item, dict) and bool(item.get("enabled")))
    return {
        "schema_version": payload.get("schema_version"),
        "project_name": payload.get("project_name"),
        "project_root": payload.get("project_root"),
        "collected_at": payload.get("collected_at"),
        "module_count": len(modules) if isinstance(modules, list) else 0,
        "plugin_count": len(plugins) if isinstance(plugins, list) else 0,
        "enabled_plugin_count": int(enabled_plugins),
        "source_root_count": len(source_roots) if isinstance(source_roots, list) else 0,
    }


def _project_context_cache_state(*, ok: bool, cached: bool, payload: Dict[str, Any]) -> str:
    if not ok:
        return "non_cacheable_response"
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, list) and diagnostics:
        return "stale_snapshot" if bool(cached) else "cacheable_snapshot"
    return "cacheable_snapshot"


def _collect_project_context_payload() -> Dict[str, Any]:
    from project_context_collector import collect_project_context

    payload = collect_project_context()
    if not isinstance(payload, dict):
        raise RuntimeError("project_context_collector returned non-object payload")
    return payload


def _get_project_context_cached(
    force_refresh: bool = False,
) -> tuple[bool, Dict[str, Any], bool, str]:
    global _PROJECT_CONTEXT_CACHE, _PROJECT_CONTEXT_CACHE_MONOTONIC

    now = time.monotonic()
    if (
        not force_refresh
        and isinstance(_PROJECT_CONTEXT_CACHE, dict)
        and (now - _PROJECT_CONTEXT_CACHE_MONOTONIC) <= max(0.0, PROJECT_CONTEXT_CACHE_TTL_SEC)
    ):
        return True, dict(_PROJECT_CONTEXT_CACHE), True, ""

    try:
        payload = _collect_project_context_payload()
    except Exception as exc:
        return False, {}, False, str(exc)

    _PROJECT_CONTEXT_CACHE = dict(payload)
    _PROJECT_CONTEXT_CACHE_MONOTONIC = now
    return True, dict(payload), False, ""


def _build_project_context_request_context(params: Dict[str, Any]) -> Dict[str, Any]:
    mode = _normalize_project_context_mode(params.get("project_context_mode"), default="summary")
    if mode == "none":
        return {
            "project_context_mode": "none",
            "project_context_available": False,
            "project_context_attached": False,
        }

    force_refresh = bool(params.get("project_context_refresh"))
    ok, payload, cached, error = _get_project_context_cached(force_refresh=force_refresh)
    if not ok:
        return {
            "project_context_mode": mode,
            "project_context_available": False,
            "project_context_attached": False,
            "project_context_error": error or "project_context_unavailable",
            "project_context_cache_state": "non_cacheable_response",
            "project_context_cache_invalidation_trigger": (
                "project_context_refresh" if force_refresh else "collector_error"
            ),
        }

    cache_state = _project_context_cache_state(ok=ok, cached=cached, payload=payload)
    context: Dict[str, Any] = {
        "project_context_mode": mode,
        "project_context_available": True,
        "project_context_attached": True,
        "project_context_cached": bool(cached),
        "project_context_cache_state": cache_state,
        "project_context_cache_invalidation_trigger": (
            "project_context_refresh"
            if force_refresh
            else (
                "ttl_expired_or_cache_miss"
                if not cached
                else "none"
            )
        ),
        "project_context_summary": _project_context_summary(payload),
    }
    if mode == "full":
        context["project_context"] = payload
    return context


def _project_context_rpc_result(params: Dict[str, Any]) -> Dict[str, Any]:
    mode = _normalize_project_context_mode(params.get("mode"), default="full")
    if mode == "none":
        mode = "summary"
    force_refresh = bool(params.get("refresh"))
    ok, payload, cached, error = _get_project_context_cached(force_refresh=force_refresh)
    if not ok:
        return {
            "ok": False,
            "mode": mode,
            "cached": False,
            "cache_state": "non_cacheable_response",
            "cache_invalidation_trigger": "refresh_requested" if force_refresh else "collector_error",
            "error": "project_context_unavailable",
            "message": str(error or "Failed to collect project context."),
            "project_context_summary": {},
        }

    cache_state = _project_context_cache_state(ok=ok, cached=cached, payload=payload)
    result: Dict[str, Any] = {
        "ok": True,
        "mode": mode,
        "cached": bool(cached),
        "cache_state": cache_state,
        "cache_invalidation_trigger": (
            "refresh_requested"
            if force_refresh
            else ("ttl_expired_or_cache_miss" if not cached else "none")
        ),
        "project_context_summary": _project_context_summary(payload),
    }
    if mode == "full":
        result["project_context"] = payload
    return result


def _mutation_auth_token() -> str:
    for env_key in RPC_AUTH_ENV_KEYS:
        token = str(os.getenv(env_key, "") or "").strip()
        if token:
            return token

    cfg = _raw_plugin_config()
    for cfg_key in RPC_AUTH_CONFIG_KEYS:
        token = str(cfg.get(cfg_key, "") or "").strip()
        if token:
            return token
    return ""


def _request_auth_token(request: Dict[str, Any], params: Dict[str, Any]) -> str:
    for key in ("auth_token", "token", "rpc_token"):
        value = params.get(key)
        if value is None:
            value = request.get(key)
        token = str(value or "").strip()
        if token:
            return token
    return ""


def _authorize_rpc_mutation(
    request: Dict[str, Any], params: Dict[str, Any]
) -> tuple[bool, str]:
    configured_token = _mutation_auth_token()
    if not configured_token:
        return False, "RPC mutation auth is not configured on server."

    incoming_token = _request_auth_token(request, params)
    if not incoming_token:
        return False, "Missing RPC mutation auth token."

    if not hmac.compare_digest(incoming_token, configured_token):
        return False, "Invalid RPC mutation auth token."

    return True, ""


def _runtime_port(name: str, fallback: int) -> int:
    cfg = _runtime_config()
    try:
        return int(cfg.get(name, fallback))
    except Exception:
        return int(fallback)


def _force_scene_refresh_from_params(params: Dict[str, Any]) -> bool:
    return bool(params.get("force_scene_refresh") or params.get("scene_refresh"))


def configure_logging() -> None:
    if LOGGER.handlers:
        return

    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    log_path = Path(__file__).with_name("AIRD_MCP.log")
    formatter = logging.Formatter("[AIRD MCP] %(asctime)s %(levelname)s: %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    LOGGER.addHandler(stream_handler)


def _json_dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _sanitize_for_log(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in SENSITIVE_KEYS:
                cleaned[key] = "***redacted***"
            else:
                cleaned[key] = _sanitize_for_log(item)
        return cleaned
    if isinstance(value, list):
        return [_sanitize_for_log(item) for item in value]
    return value


def _safe_log_payload(value: Any) -> str:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return json.dumps(_sanitize_for_log(parsed), ensure_ascii=False)
        except Exception:
            return value
    if isinstance(value, (dict, list)):
        return json.dumps(_sanitize_for_log(value), ensure_ascii=False)
    return str(value)


def _structured_log(event: str, **fields: Any) -> None:
    payload = {"event": str(event or "").strip().lower(), "fields": fields}
    LOGGER.info("STRUCT %s", _safe_log_payload(payload))


def _resolve_request_id(candidate: Any = None) -> str:
    text = str(candidate or "").strip()
    return text or uuid4().hex


def _trace_flow(event: str, request_id: str, **fields: Any) -> None:
    marker = str(event or "").strip().upper() or "TRACE_EVENT"
    rid = _resolve_request_id(request_id)
    LOGGER.info("%s request_id=%s %s", marker, rid, _safe_log_payload(fields))


def _memory_db_path() -> Path:
    return _plugin_root() / "memory" / "aird_memory.db"


def _bootstrap_phase2_components() -> None:
    global _ORCHESTRATOR, _MEMORY_MANAGER
    global _SCENE_AGENT, _BLUEPRINT_AGENT, _CODE_AGENT, _CONTENT_AGENT

    if _ORCHESTRATOR is None:
        _ORCHESTRATOR = RequestOrchestrator(fallback_agent="sceneagent")

    if _MEMORY_MANAGER is None:
        db_path = _memory_db_path()
        _MEMORY_MANAGER = MemoryManager(db_path)
        _structured_log("memory_initialized", db_path=str(db_path))

    if _SCENE_AGENT is None:
        _SCENE_AGENT = SceneAgent(
            lambda _request: {"defer_scene_pipeline": True, "ok": True}
        )
    if _BLUEPRINT_AGENT is None:
        _BLUEPRINT_AGENT = BlueprintAgent(
            lambda _request: {"defer_scene_pipeline": True, "ok": True}
        )
    if _CODE_AGENT is None:
        _CODE_AGENT = CodeAgent(
            project_root_resolver=_project_root,
            fallback_executor=lambda _request: {"defer_scene_pipeline": True, "ok": True},
        )
    if _CONTENT_AGENT is None:
        _CONTENT_AGENT = ContentAgent(
            fallback_executor=lambda _request: {"defer_scene_pipeline": True, "ok": True},
        )

    _ORCHESTRATOR.register_agent(_SCENE_AGENT.name, _SCENE_AGENT)
    _ORCHESTRATOR.register_agent(_BLUEPRINT_AGENT.name, _BLUEPRINT_AGENT)
    _ORCHESTRATOR.register_agent(_CODE_AGENT.name, _CODE_AGENT)
    _ORCHESTRATOR.register_agent(_CONTENT_AGENT.name, _CONTENT_AGENT)
    _ORCHESTRATOR.set_handler(_SCENE_AGENT.name, _SCENE_AGENT.process)
    _ORCHESTRATOR.set_handler(_BLUEPRINT_AGENT.name, _BLUEPRINT_AGENT.process)
    _ORCHESTRATOR.set_handler(_CODE_AGENT.name, _CODE_AGENT.process)
    _ORCHESTRATOR.set_handler(_CONTENT_AGENT.name, _CONTENT_AGENT.process)

    _structured_log(
        "orchestrator_initialized",
        fallback_agent=_ORCHESTRATOR.fallback_agent,
        registered_agents=_ORCHESTRATOR.registered_agents(),
    )


async def _save_conversation_record(
    *,
    text: str,
    message: str,
    routing: Dict[str, Any],
    provider_id: str,
    model: str,
    scene_stale: bool,
    usage_tokens: int,
    params: Dict[str, Any],
) -> int:
    if _MEMORY_MANAGER is None:
        return 0
    try:
        memory_meta = {
            "routing": routing,
            "scene_stale": bool(scene_stale),
            "usage_tokens": int(usage_tokens or 0),
        }
        record_id = await asyncio.to_thread(
            _MEMORY_MANAGER.save_conversation,
            text,
            message,
            session_id=str(params.get("session_id") or "").strip(),
            agent_used=str(routing.get("agent") or "").strip(),
            provider_id=provider_id,
            model=model,
            metadata_json=json.dumps(memory_meta, ensure_ascii=False),
        )
        _structured_log(
            "memory_write_ok",
            record_id=record_id,
            agent=routing.get("agent"),
            provider=provider_id,
            model=model,
        )
        return int(record_id or 0)
    except Exception as memory_exc:
        _structured_log(
            "memory_write_failed",
            error=str(memory_exc),
            provider=provider_id,
            model=model,
        )
        return 0


def _scene_cache_state(scene: Dict[str, Any]) -> str:
    if not isinstance(scene, dict):
        return "non_cacheable_response"
    source = str(scene.get("source") or "").strip().lower()
    if source in ("", "unavailable", "empty_json", "pending", "pending_game_thread"):
        return "non_cacheable_response"
    if bool(scene.get("stale")) or source.endswith("-cached"):
        return "stale_snapshot"
    if _has_required_scene_context(scene):
        return "cacheable_snapshot"
    return "non_cacheable_response"


def _scene_cache_ttl_for_state(state: str) -> float:
    if state == "cacheable_snapshot":
        return max(0.0, SCENE_CONTEXT_CACHE_TTL_SEC)
    if state == "stale_snapshot":
        return max(0.0, SCENE_CONTEXT_STALE_CACHE_TTL_SEC)
    return 0.0


def _invalidate_scene_cache(trigger: str = "manual") -> None:
    global _SCENE_CONTEXT_CACHE, _SCENE_CONTEXT_CACHE_STATE, _SCENE_CONTEXT_CACHE_MONOTONIC
    had_cache = False
    with _SCENE_CONTEXT_CACHE_LOCK:
        had_cache = isinstance(_SCENE_CONTEXT_CACHE, dict)
        _SCENE_CONTEXT_CACHE = None
        _SCENE_CONTEXT_CACHE_STATE = "non_cacheable_response"
        _SCENE_CONTEXT_CACHE_MONOTONIC = 0.0
    if had_cache or str(trigger or "").startswith("force_refresh"):
        _structured_log("scene_cache_invalidate", trigger=str(trigger or "manual"))


def _scene_cache_read(force_refresh: bool = False) -> tuple[bool, Dict[str, Any], str]:
    if force_refresh:
        return False, {}, "force_refresh"
    now = time.monotonic()
    with _SCENE_CONTEXT_CACHE_LOCK:
        cached = _SCENE_CONTEXT_CACHE
        state = str(_SCENE_CONTEXT_CACHE_STATE or "non_cacheable_response")
        cached_at = float(_SCENE_CONTEXT_CACHE_MONOTONIC or 0.0)
    if not isinstance(cached, dict):
        return False, {}, "cache_empty"
    ttl = _scene_cache_ttl_for_state(state)
    if ttl <= 0.0:
        return False, {}, "ttl_disabled"
    if (now - cached_at) > ttl:
        return False, {}, "cache_expired"
    payload = dict(cached)
    payload["cache_state"] = state
    payload["cache_hit"] = True
    return True, payload, state


def _scene_cache_write(scene: Dict[str, Any], trigger: str = "scene_read") -> str:
    global _SCENE_CONTEXT_CACHE, _SCENE_CONTEXT_CACHE_STATE, _SCENE_CONTEXT_CACHE_MONOTONIC
    state = _scene_cache_state(scene)
    if state == "non_cacheable_response":
        _invalidate_scene_cache(f"{trigger}:non_cacheable")
        return state
    cached = dict(scene)
    cached["cache_state"] = state
    cached["cache_hit"] = False
    cached["cached_at_monotonic"] = time.monotonic()
    with _SCENE_CONTEXT_CACHE_LOCK:
        _SCENE_CONTEXT_CACHE = cached
        _SCENE_CONTEXT_CACHE_STATE = state
        _SCENE_CONTEXT_CACHE_MONOTONIC = time.monotonic()
    return state


def _safe_scene_context(force_refresh: bool = False) -> Dict[str, Any]:
    global LAST_GRACE_LOG_MONOTONIC
    cache_hit, cached_scene, cache_reason = _scene_cache_read(force_refresh=force_refresh)
    if cache_hit:
        return cached_scene

    elapsed = time.monotonic() - SERVER_START_MONOTONIC
    runtime_bridge_connected = False
    try:
        runtime_bridge_connected = is_runtime_bridge_connected()
    except Exception:
        runtime_bridge_connected = False
    unreal_python_available = try_import_unreal() is not None
    editor_native_available = bool(runtime_bridge_connected or unreal_python_available)

    # Guard rail for Unreal initialization (CVars/Shaders): do not send first RC HTTP calls too early.
    if (
        REMOTE_CONTROL_STARTUP_DELAY_SEC > 0.0
        and elapsed < REMOTE_CONTROL_STARTUP_DELAY_SEC
        and not editor_native_available
    ):
        remaining = REMOTE_CONTROL_STARTUP_DELAY_SEC - elapsed
        if (time.monotonic() - LAST_GRACE_LOG_MONOTONIC) > 2.0:
            LAST_GRACE_LOG_MONOTONIC = time.monotonic()
            LOGGER.info(
                "[AIRD MCP] remote control startup delay active %.2fs remaining",
                max(0.0, remaining),
            )
        pending = {"actors": [], "source": "pending", "count": 0}
        pending["cache_hit"] = False
        pending["cache_state"] = "non_cacheable_response"
        pending["cache_reason"] = cache_reason
        return pending

    if (
        SCENE_READ_GRACE_SEC > 0.0
        and elapsed < SCENE_READ_GRACE_SEC
        and not editor_native_available
    ):
        remaining = SCENE_READ_GRACE_SEC - elapsed
        # Keep this log throttled to avoid flooding while UI polls.
        if (time.monotonic() - LAST_GRACE_LOG_MONOTONIC) > 2.0:
            LAST_GRACE_LOG_MONOTONIC = time.monotonic()
            LOGGER.info(
                "[AIRD MCP] scene read grace active %.2fs remaining",
                max(0.0, remaining),
            )
        pending = {"actors": [], "source": "pending", "count": 0}
        pending["cache_hit"] = False
        pending["cache_state"] = "non_cacheable_response"
        pending["cache_reason"] = cache_reason
        return pending

    try:
        LOGGER.info(
            "[AIRD MCP] scene: calling get_scene_context() (editor-first strategy, runtime_bridge_connected=%s, unreal_python_available=%s)",
            runtime_bridge_connected,
            unreal_python_available,
        )
        scene = get_scene_context()
        if isinstance(scene, dict):
            scene.setdefault("actors", [])
            scene.setdefault("source", "unknown")
            scene = _stabilize_scene_snapshot(scene)
            ac = len(scene["actors"]) if isinstance(scene.get("actors"), list) else 0
            LOGGER.info(
                "[AIRD MCP] scene: ok source=%s actor_count=%s",
                scene.get("source"),
                ac,
            )
            trace = scene.get("source_trace")
            if isinstance(trace, list):
                LOGGER.info("[AIRD MCP] scene source trace: %s", _safe_log_payload(trace))
            cache_state = _scene_cache_write(scene, trigger="scene_read_ok")
            scene["cache_state"] = cache_state
            scene["cache_hit"] = False
            scene["cache_reason"] = cache_reason
            return scene
    except Exception as exc:
        LOGGER.exception("Failed to read scene context: %s", exc)
    unavailable = {"actors": [], "source": "unavailable"}
    unavailable["cache_hit"] = False
    unavailable["cache_state"] = "non_cacheable_response"
    unavailable["cache_reason"] = cache_reason
    return unavailable


def _has_required_scene_context(scene: Dict[str, Any]) -> bool:
    """True when Unreal scene was read successfully; actor list may be empty."""
    if not isinstance(scene, dict):
        return False
    source = str(scene.get("source") or "").strip().lower()
    actors = scene.get("actors")
    if source in ("", "unavailable", "empty_json", "pending", "pending_game_thread"):
        return False
    if not isinstance(actors, list):
        return False
    if (
        source.startswith("unreal_")
        or source.startswith("editor_")
        or source.startswith("runtime_bridge_")
    ):
        return True
    if source in ("unreal_cpp_bridge", "aird", "airdb", "remote_control_api"):
        return True
    return len(actors) > 0


def _is_editor_native_source(scene_source: str) -> bool:
    source = str(scene_source or "").strip().lower()
    if source.startswith("unreal_") or source.startswith("runtime_bridge_") or source.startswith("editor_"):
        return True
    return source in ("unreal_cpp_bridge", "aird", "airdb")


def _is_remote_control_zero_scene(scene: Dict[str, Any]) -> bool:
    if not isinstance(scene, dict):
        return False
    source = str(scene.get("source") or "").strip().lower()
    if not source.startswith("remote_control_api"):
        return False
    actors = scene.get("actors")
    return isinstance(actors, list) and len(actors) == 0


def _stabilize_scene_snapshot(scene: Dict[str, Any]) -> Dict[str, Any]:
    """
    Policy for valid-empty ambiguity:
    - `remote_control_api` with 0 actors is transport-valid but low-authority.
    - If a valid editor-native snapshot exists, preserve it (as stale) instead of replacing it.
    """
    global LAST_VALID_SCENE
    if not _is_remote_control_zero_scene(scene):
        return scene
    if not isinstance(LAST_VALID_SCENE, dict):
        return scene
    cached_source = str(LAST_VALID_SCENE.get("source") or "").strip()
    if not _is_editor_native_source(cached_source):
        return scene
    if not _has_required_scene_context(LAST_VALID_SCENE):
        return scene

    stabilized = dict(LAST_VALID_SCENE)
    stabilized["stale"] = True
    normalized_source = str(stabilized.get("source") or "").strip() or "unknown"
    if not normalized_source.endswith("-cached"):
        stabilized["source"] = f"{normalized_source}-cached"

    trace = stabilized.get("source_trace")
    trace_list = list(trace) if isinstance(trace, list) else []
    trace_list.append(
        {
            "order": len(trace_list) + 1,
            "source": "remote_control_api",
            "status": "fallback",
            "reason": "Remote Control returned 0 actors; preserved last valid editor-native scene.",
            "actor_count": 0,
        }
    )
    stabilized["source_trace"] = trace_list
    stabilized["scene_fallback_policy"] = "preserve_editor_native_on_remote_control_zero"
    return stabilized


def _scene_provider_layer(scene_source: str) -> str:
    source = str(scene_source or "").strip().lower()
    if source.startswith("remote_control_api"):
        return "remote_control_api"
    if source.startswith("unreal_") or source.startswith("runtime_bridge_") or source in (
        "editor_level_library",
        "airdb",
        "aird",
        "unreal_cpp_bridge",
    ):
        return "unreal_runtime"
    if source.startswith("pending"):
        return "scene_provider_pending"
    if source in ("", "unavailable", "empty_json"):
        return "scene_provider_unavailable"
    return "unknown"


def _scene_source_order_trace(
    scene_source: str, raw_trace: Any
) -> Dict[str, Any]:
    entries: list[Dict[str, Any]] = []
    if isinstance(raw_trace, list):
        for index, item in enumerate(raw_trace, start=1):
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "").strip() or "unknown"
            status = str(item.get("status") or "").strip() or "unknown"
            reason = str(item.get("reason") or "").strip() or "unspecified"
            try:
                order = int(item.get("order") or index)
            except Exception:
                order = index
            entries.append(
                {
                    "order": order,
                    "source": source,
                    "status": status,
                    "reason": reason,
                }
            )
    entries.sort(key=lambda entry: int(entry.get("order") or 0))

    source_fallback = str(scene_source or "").strip() or "unknown"
    primary_entry = entries[0] if entries else None
    primary = {
        "source": str((primary_entry or {}).get("source") or source_fallback),
        "status": str((primary_entry or {}).get("status") or "unknown"),
        "reason": str(
            (primary_entry or {}).get("reason")
            or "No source trace was provided by scene acquisition."
        ),
    }

    fallback: list[Dict[str, str]] = []
    for item in entries[1:]:
        fallback.append(
            {
                "source": str(item.get("source") or "unknown"),
                "status": str(item.get("status") or "unknown"),
                "reason": str(item.get("reason") or "unspecified"),
            }
        )

    chosen_entry = None
    for item in entries:
        if str(item.get("status") or "").strip().lower() == "success":
            chosen_entry = item
            break
    if chosen_entry is None:
        chosen_entry = entries[-1] if entries else None

    chosen_source = str((chosen_entry or {}).get("source") or source_fallback)
    chosen_reason = str(
        (chosen_entry or {}).get("reason")
        or (
            "Scene source selected from current snapshot."
            if source_fallback and source_fallback != "unknown"
            else "No source selection reason provided."
        )
    )
    return {
        "primary": primary,
        "fallback": fallback,
        "reason": chosen_reason,
        "chosen_source": chosen_source,
    }


def _runtime_status_snapshot(
    scene: Optional[Dict[str, Any]] = None, include_probes: bool = False
) -> Dict[str, Any]:
    snapshot = scene if isinstance(scene, dict) else _safe_scene_context()
    source = str(snapshot.get("source") or "").strip() or "unknown"
    actors = snapshot.get("actors")
    actor_count = len(actors) if isinstance(actors, list) else 0
    scene_valid = _has_required_scene_context(snapshot)

    unreal = try_import_unreal()
    unreal_python_available = unreal is not None
    aird_bridge_available = bool(unreal_python_available and hasattr(unreal, "AIRDBridge"))
    runtime_bridge_connected = False
    try:
        runtime_bridge_connected = is_runtime_bridge_connected()
    except Exception:
        runtime_bridge_connected = False

    unreal_runtime_connected = bool(aird_bridge_available or runtime_bridge_connected)
    runtime_connection_mode = "none"
    if aird_bridge_available:
        runtime_connection_mode = "local_airdb"
    elif runtime_bridge_connected:
        runtime_connection_mode = "runtime_bridge_queue"

    mcp_state = "connected"
    runtime_state = "connected" if unreal_runtime_connected else "unavailable"
    if unreal_runtime_connected and scene_valid:
        capability_readiness = "ready"
    elif unreal_runtime_connected or scene_valid:
        capability_readiness = "partially_ready"
    else:
        capability_readiness = "unavailable"

    if capability_readiness == "ready":
        status_text = (
            "MCP connected; Unreal runtime connected; capabilities ready."
        )
    elif capability_readiness == "partially_ready":
        status_text = (
            "MCP connected; capabilities partially ready."
            if unreal_runtime_connected
            else "MCP connected; scene data available but Unreal runtime is unavailable."
        )
    else:
        status_text = "MCP connected; Unreal runtime unavailable; capabilities unavailable."

    raw_trace = (
        snapshot.get("source_trace")
        if isinstance(snapshot.get("source_trace"), list)
        else []
    )
    payload: Dict[str, Any] = {
        "mcp_online": True,
        "runtime_ready": bool(unreal_runtime_connected and scene_valid),
        "unreal_runtime_connected": unreal_runtime_connected,
        "capability_ready": bool(unreal_runtime_connected and scene_valid),
        "runtime_connection_mode": runtime_connection_mode,
        "unreal_python_available": unreal_python_available,
        "airdb_bridge_available": aird_bridge_available,
        "runtime_bridge_connected": runtime_bridge_connected,
        "scene_context_valid": scene_valid,
        "scene_source": source,
        "scene_source_trace": raw_trace,
        "scene_source_order_trace": _scene_source_order_trace(source, raw_trace),
        "scene_provider_layer": _scene_provider_layer(source),
        "actor_count": actor_count,
        "status_semantics": {
            "mcp_state": mcp_state,
            "runtime_state": runtime_state,
            "capability_readiness": capability_readiness,
        },
        "status_state": capability_readiness,
        "status": status_text,
        "runtime_bridge": read_runtime_bridge_heartbeat(),
    }

    if include_probes:
        payload["editor_actor_subsystem"] = _probe_editor_actor_subsystem_status()
        payload["remote_control"] = _probe_remote_control_status()

    return payload


def _is_blueprint_runtime_command(text: str) -> bool:
    low = str(text or "").strip().lower()
    blueprint_aliases = ("blueprint", "بلوبرنت", "بلو برنت")
    if not any(alias in low for alias in blueprint_aliases):
        return False
    return bool(
        re.search(
            r"\b(add\s+variable|add\s+function|create\s+blueprint|generate\s+blueprint|make\s+blueprint)\b|"
            r"(انشاء|إنشاء|انشئ|أنشئ).{0,24}(بلوبرنت|بلو برنت)|"
            r"(بلوبرنت|بلو برنت).{0,24}(انشاء|إنشاء|انشئ|أنشئ|اضافة|إضافة|قراءة|اقرأ)",
            low,
        )
    )


def _is_scene_runtime_command(text: str) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return False
    scene_markers = (
        "analyze the scene",
        "analyze scene",
        "scan scene",
        "scene summary",
        "scene stats",
        "scene context",
        "تحليل المشهد",
        "تحليل المشهد الحالي",
        "تحليل scene",
        "ملخص المشهد",
    )
    return any(marker in low for marker in scene_markers)


def _get_effective_scene_context() -> tuple[Dict[str, Any], bool]:
    global LAST_VALID_SCENE
    scene = _safe_scene_context()
    scene = _stabilize_scene_snapshot(scene)
    if _has_required_scene_context(scene):
        is_stale = bool(scene.get("stale"))
        scene["stale"] = is_stale
        if not is_stale:
            LAST_VALID_SCENE = dict(scene)
        return scene, is_stale

    if isinstance(LAST_VALID_SCENE, dict) and _has_required_scene_context(
        LAST_VALID_SCENE
    ):
        cached = dict(LAST_VALID_SCENE)
        cached["stale"] = True
        cached["source"] = f"{cached.get('source', 'unknown')}-cached"
        return cached, True

    return scene, False


def _resolve_context_server_url(params: Dict[str, Any]) -> str:
    raw = str(params.get("context_server_url") or DEFAULT_CONTEXT_SERVER_URL).strip()
    if not raw:
        return ""
    if not raw.startswith("http://") and not raw.startswith("https://"):
        return ""
    return raw.rstrip("/")


def _normalize_timeout_sec(timeout_sec: float, default_sec: float) -> float:
    try:
        candidate = float(timeout_sec)
    except Exception:
        candidate = float(default_sec)
    lower = max(0.2, float(FALLBACK_TIMEOUT_MIN_SEC))
    upper = max(lower, float(FALLBACK_TIMEOUT_MAX_SEC))
    return min(upper, max(lower, candidate))


def _is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    text = str(exc or "").strip().lower()
    return "timed out" in text or "timeout" in text


def _classify_fallback_failure(exc: BaseException) -> Dict[str, Any]:
    """
    Non-editor fallback classification:
    - retryable_timeout
    - transient_failure
    - hard_failure
    - immediate_no_retry_condition
    """
    if _is_timeout_error(exc):
        return {
            "class": "retryable_timeout",
            "retryable": True,
            "reason": "Request timed out.",
        }

    if isinstance(exc, urllib.error.HTTPError):
        code = int(exc.code or 0)
        if code in {408, 425, 429, 500, 502, 503, 504}:
            klass = "retryable_timeout" if code in {408, 504} else "transient_failure"
            return {
                "class": klass,
                "retryable": True,
                "reason": f"HTTP {code} transient upstream error.",
            }
        if code in {400, 401, 403, 404, 405, 409, 410, 422}:
            return {
                "class": "immediate_no_retry_condition",
                "retryable": False,
                "reason": f"HTTP {code} non-retryable request/provider condition.",
            }
        return {
            "class": "hard_failure",
            "retryable": False,
            "reason": f"HTTP {code} hard failure.",
        }

    if isinstance(exc, urllib.error.URLError):
        reason = str(getattr(exc, "reason", "") or exc).lower()
        if any(token in reason for token in ("refused", "reset", "temporar", "unreachable", "again")):
            return {
                "class": "transient_failure",
                "retryable": True,
                "reason": "Transient network/socket failure.",
            }
        return {
            "class": "hard_failure",
            "retryable": False,
            "reason": "Network failure is not classified as retryable.",
        }

    if isinstance(exc, (ValueError, json.JSONDecodeError)):
        return {
            "class": "immediate_no_retry_condition",
            "retryable": False,
            "reason": "Invalid response payload format.",
        }

    return {
        "class": "hard_failure",
        "retryable": False,
        "reason": str(exc) or "Unknown hard failure.",
    }


def _normalized_retry_attempts(requested: int, default_attempts: int = 1) -> int:
    try:
        candidate = int(requested)
    except Exception:
        candidate = int(default_attempts)
    allowed_max = max(1, int(FALLBACK_RETRY_ATTEMPTS))
    return max(1, min(allowed_max, candidate))


def _request_json_with_retry(
    *,
    url: str,
    method: str,
    timeout_sec: float,
    headers: Dict[str, str],
    body: Optional[Dict[str, Any]] = None,
    operation: str,
    retry_attempts: int = 1,
    retry_base_delay_sec: float = 0.25,
    retry_max_delay_sec: float = 1.5,
) -> Dict[str, Any]:
    normalized_timeout = _normalize_timeout_sec(timeout_sec, timeout_sec)
    attempts = _normalized_retry_attempts(retry_attempts, default_attempts=1)
    base_delay = max(0.05, float(retry_base_delay_sec))
    max_delay = max(base_delay, float(retry_max_delay_sec))
    payload_data = json.dumps(body).encode("utf-8") if isinstance(body, dict) else None

    last_error: Optional[BaseException] = None
    last_classification: Dict[str, Any] = {
        "class": "hard_failure",
        "retryable": False,
        "reason": "request_not_executed",
    }
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            data=payload_data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=normalized_timeout) as response:
                raw = response.read().decode("utf-8")
                parsed = json.loads(raw) if raw else {}
                if isinstance(parsed, dict):
                    return parsed
                raise ValueError("Response payload is not a JSON object.")
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="ignore")
            wrapped = RuntimeError(f"HTTP {exc.code}: {details[:600]}")
            wrapped.__cause__ = exc
            last_error = wrapped
            last_classification = _classify_fallback_failure(exc)
        except Exception as exc:
            last_error = exc
            last_classification = _classify_fallback_failure(exc)

        if attempt >= attempts or not bool(last_classification.get("retryable")):
            break

        delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
        LOGGER.info(
            "Fallback retry %s/%s for %s (%s): %s",
            attempt,
            attempts,
            operation,
            last_classification.get("class"),
            last_classification.get("reason"),
        )
        time.sleep(delay)

    raise RuntimeError(
        f"{operation} failed [{last_classification.get('class')}]"
        f": {last_classification.get('reason')}"
    ) from last_error


def _post_json(
    url: str,
    payload: Dict[str, Any],
    timeout: int = 12,
    *,
    retry_attempts: int = 1,
    operation: str = "context_server_post",
) -> Dict[str, Any]:
    return _request_json_with_retry(
        url=url,
        method="POST",
        timeout_sec=float(timeout),
        headers={"Content-Type": "application/json"},
        body=payload,
        operation=operation,
        retry_attempts=retry_attempts,
        retry_base_delay_sec=FALLBACK_RETRY_BASE_DELAY_SEC,
        retry_max_delay_sec=FALLBACK_RETRY_MAX_DELAY_SEC,
    )


def _sync_scene_snapshot(
    context_server_url: str, scene: Dict[str, Any]
) -> Dict[str, Any]:
    if not context_server_url:
        return {"ok": False, "error": "context server url is empty"}
    payload = {
        "type": "scene_sync",
        "scene": scene,
        "source": "mcp-python-bridge",
        "actor_count": len(scene.get("actors", []))
        if isinstance(scene.get("actors"), list)
        else 0,
        "timestamp": scene.get("updated_at", ""),
    }
    # Outer retry policy is implemented by _sync_scene_snapshot_with_retry.
    return _post_json(
        f"{context_server_url}/scene-sync",
        payload,
        timeout=6,
        retry_attempts=1,
        operation="context_server_scene_sync",
    )


def _sync_scene_snapshot_with_retry(
    context_server_url: str, scene: Dict[str, Any]
) -> Dict[str, Any]:
    delay = 0.25
    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_SCENE_SYNC_RETRIES + 1):
        try:
            return _sync_scene_snapshot(context_server_url, scene)
        except Exception as exc:
            last_error = exc
            if attempt >= MAX_SCENE_SYNC_RETRIES:
                break
            LOGGER.warning(
                "Scene sync retry %s/%s failed: %s",
                attempt,
                MAX_SCENE_SYNC_RETRIES,
                exc,
            )
            import time

            time.sleep(delay)
            delay *= 2.0
    raise RuntimeError(str(last_error) if last_error else "Scene sync failed")


def _context_server_health(context_server_url: str) -> Dict[str, Any]:
    return _request_json_with_retry(
        url=f"{context_server_url}/health",
        method="GET",
        timeout_sec=5.0,
        headers={},
        body=None,
        operation="context_server_health",
        retry_attempts=2,
        retry_base_delay_sec=FALLBACK_RETRY_BASE_DELAY_SEC,
        retry_max_delay_sec=FALLBACK_RETRY_MAX_DELAY_SEC,
    )


def _context_server_trim_memory(
    context_server_url: str, max_entries: int = 8, reset_scene: bool = False
) -> Dict[str, Any]:
    return _post_json(
        f"{context_server_url}/maintenance/trim-memory",
        {"maxEntries": max_entries, "resetScene": reset_scene},
        timeout=8,
        retry_attempts=2,
        operation="context_server_trim_memory",
    )


def _trim_runtime_log_buffer() -> None:
    log_path = Path(__file__).with_name("AIRD_MCP.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")


def _prepare_runtime_buffers() -> None:
    try:
        _trim_runtime_log_buffer()
    except Exception as exc:
        LOGGER.warning("Failed to trim AIRD log buffer: %s", exc)

    context_server_url = DEFAULT_CONTEXT_SERVER_URL.rstrip("/")
    if context_server_url:
        try:
            _context_server_trim_memory(context_server_url, max_entries=8)
        except Exception as exc:
            LOGGER.info("Context server memory trim skipped: %s", exc)


def _is_context_length_error(error: Any) -> bool:
    return bool(CONTEXT_LENGTH_ERROR_RE.search(str(error or "")))


def _plugin_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _scene_perception_path() -> Path:
    return Path(__file__).with_name("scene_perception.py")


def _project_root() -> Optional[Path]:
    plugin_root = _plugin_root()
    for parent in plugin_root.parents:
        if (parent / "Saved" / "Logs").exists():
            return parent
    return None


def _latest_unreal_output_log_path() -> Optional[Path]:
    project_root = _project_root()
    if project_root is None:
        return None
    log_dir = project_root / "Saved" / "Logs"
    candidates = sorted(
        log_dir.glob("*.log"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _tail_lines(path: Optional[Path], max_lines: int = 20) -> list[str]:
    if path is None or not path.exists():
        return []
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    return raw_lines[-max(1, int(max_lines)) :]


def _probe_remote_control_status() -> Dict[str, Any]:
    cfg = _runtime_config()
    candidates = [
        int(
            cfg.get(
                "remote_control_http_port", DEFAULT_CONFIG["remote_control_http_port"]
            )
        ),
        int(cfg.get("legacy_port", DEFAULT_CONFIG["legacy_port"])),
    ]
    seen: set[int] = set()
    ordered_ports: list[int] = []
    for port in candidates:
        if port not in seen:
            seen.add(port)
            ordered_ports.append(port)

    last_error = "No candidates."
    for port in ordered_ports:
        base_url = f"http://127.0.0.1:{port}"
        request = urllib.request.Request(base_url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=2.0) as response:
                return {
                    "configured": True,
                    "reachable": True,
                    "base_url": base_url,
                    "port": port,
                    "detail": f"Remote Control reachable (HTTP {response.status}).",
                }
        except urllib.error.HTTPError as exc:
            return {
                "configured": True,
                "reachable": True,
                "base_url": base_url,
                "port": port,
                "detail": f"Remote Control reachable (HTTP {exc.code}).",
            }
        except Exception as exc:
            last_error = str(exc)

    return {
        "configured": bool(ordered_ports),
        "reachable": False,
        "base_url": f"http://127.0.0.1:{ordered_ports[0]}" if ordered_ports else "",
        "port": ordered_ports[0] if ordered_ports else 0,
        "detail": last_error,
    }


def _probe_editor_actor_subsystem_status() -> Dict[str, Any]:
    unreal = try_import_unreal()
    if unreal is None:
        return {
            "available": False,
            "detail": "unreal module is unavailable in this Python runtime.",
        }

    subsystem_getter = getattr(unreal, "get_editor_subsystem", None)
    subsystem_class = getattr(unreal, "EditorActorSubsystem", None)
    if not callable(subsystem_getter) or subsystem_class is None:
        return {
            "available": False,
            "detail": "EditorActorSubsystem is not exposed by the current Unreal Python API.",
        }

    try:
        subsystem = subsystem_getter(subsystem_class)
        if subsystem is None:
            return {
                "available": False,
                "detail": "EditorActorSubsystem exists but returned None.",
            }
        get_all_level_actors = getattr(subsystem, "get_all_level_actors", None)
        if not callable(get_all_level_actors):
            return {
                "available": False,
                "detail": "EditorActorSubsystem.get_all_level_actors is unavailable.",
            }
        return {"available": True, "detail": "EditorActorSubsystem is available."}
    except Exception as exc:
        return {"available": False, "detail": str(exc)}


def _build_missing_scene_context_diagnostics(
    scene: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    runtime_status = _runtime_status_snapshot(scene, include_probes=True)
    subsystem = runtime_status.get("editor_actor_subsystem", {})
    remote_control = runtime_status.get("remote_control", {})
    source_trace = (
        runtime_status.get("scene_source_trace")
        if isinstance(runtime_status.get("scene_source_trace"), list)
        else []
    )
    source_order_trace = (
        runtime_status.get("scene_source_order_trace")
        if isinstance(runtime_status.get("scene_source_order_trace"), dict)
        else _scene_source_order_trace(
            str(runtime_status.get("scene_source") or "unknown"),
            source_trace,
        )
    )
    source = str(runtime_status.get("scene_source") or "unknown")
    actor_count = int(runtime_status.get("actor_count") or 0)

    cfg = _runtime_config()
    primary_port = int(
        cfg.get("remote_control_http_port", DEFAULT_CONFIG["remote_control_http_port"])
    )

    if str(source).lower().startswith("pending"):
        broken_layer = "Scene Provider Warmup"
        step = "Wait a few seconds for Unreal startup warmup, then retry."
    elif not bool(runtime_status.get("unreal_runtime_connected")):
        broken_layer = "Unreal Runtime Bridge"
        step = (
            "Start AIRD Engine inside Unreal so the MCP server runs in Unreal Python runtime."
        )
    elif not bool(remote_control.get("reachable")) and not bool(
        subsystem.get("available")
    ):
        broken_layer = "Scene Provider (Remote Control + Editor Subsystem)"
        step = (
            "Enable Remote Control API + Python Editor Script Plugin, then press Recompile in Unreal."
        )
    elif not bool(remote_control.get("reachable")):
        broken_layer = "Remote Control API"
        step = (
            f"Run this in CMD: curl http://127.0.0.1:{primary_port} and confirm Remote Control responds."
        )
    else:
        broken_layer = "Unreal/MCP Scene Extraction"
        step = "Press Recompile in Unreal, then retry AIRD."

    summary = [
        MISSING_SCENE_CONTEXT_MESSAGE,
        f"Broken Layer: {broken_layer}",
        f"Scene source is '{source}' and actor_count={actor_count}",
        f"Scene source trace entries: {len(source_trace)}",
        f"Primary source: {source_order_trace.get('primary', {}).get('source', 'unknown')}",
        f"Chosen source reason: {source_order_trace.get('reason', 'unspecified')}",
        f"EditorActorSubsystem: {'OK' if subsystem.get('available') else 'Unavailable'}",
        f"Remote Control: {'Reachable' if remote_control.get('reachable') else 'Unavailable'}",
        f"Next step: {step}",
    ]
    return {
        "summary": "\n".join(summary),
        "broken_layer": broken_layer,
        "runtime_status": runtime_status,
        "scene_source_trace": source_trace,
        "scene_source_order_trace": source_order_trace,
        "editor_actor_subsystem": subsystem,
        "remote_control": remote_control,
        "next_step": step,
    }


def _collect_system_diagnostics(max_log_lines: int = 20) -> Dict[str, Any]:
    scene = _safe_scene_context()
    missing_context = _build_missing_scene_context_diagnostics(scene)
    unreal_log_path = _latest_unreal_output_log_path()
    mcp_log_path = Path(__file__).with_name("AIRD_MCP.log")
    cfg = _runtime_config()
    return {
        "runtime_config": cfg,
        "runtime_status": _runtime_status_snapshot(scene, include_probes=True),
        "missing_scene_context": missing_context,
        "editor_actor_subsystem": missing_context.get("editor_actor_subsystem", {}),
        "remote_control": missing_context.get("remote_control", {}),
        "recommended_step": missing_context.get("next_step", ""),
        "unreal_output_log_path": str(unreal_log_path) if unreal_log_path else "",
        "unreal_output_log_tail": _tail_lines(unreal_log_path, max_log_lines),
        "mcp_log_path": str(mcp_log_path),
        "mcp_log_tail": _tail_lines(mcp_log_path, max_log_lines),
    }


def _replace_function_source(source: str, func_name: str, replacement: str) -> str:
    pattern = re.compile(
        rf"def {re.escape(func_name)}\([^)]*\) -> [^:]+:\n(?:    .*\n|\n)*?(?=^def |\Z)",
        re.MULTILINE,
    )
    updated, count = pattern.subn(replacement.rstrip() + "\n\n", source, count=1)
    if count != 1:
        raise RuntimeError(f"Could not replace function {func_name}")
    return updated


def _build_scene_perception_fix_proposal() -> Dict[str, Any]:
    file_path = _scene_perception_path()
    original = file_path.read_text(encoding="utf-8")
    subsystem = _probe_editor_actor_subsystem_status()
    remote_control = _probe_remote_control_status()

    replacement = """def _get_scene_context_via_unreal() -> Optional[Dict[str, Any]]:
    unreal = try_import_unreal()
    if unreal is None:
        return None

    def _collect_from_editor_actor_subsystem() -> Dict[str, Any]:
        subsystem_getter = getattr(unreal, "get_editor_subsystem", None)
        subsystem_class = getattr(unreal, "EditorActorSubsystem", None)
        if not callable(subsystem_getter) or subsystem_class is None:
            raise RuntimeError("EditorActorSubsystem is not exposed")

        actor_subsystem = subsystem_getter(subsystem_class)
        if actor_subsystem is None:
            raise RuntimeError("unreal.EditorActorSubsystem is unavailable")

        get_all_level_actors = getattr(actor_subsystem, "get_all_level_actors", None)
        if not callable(get_all_level_actors):
            raise RuntimeError(
                "EditorActorSubsystem.get_all_level_actors is unavailable"
            )
        return _build_scene_from_unreal_actors(get_all_level_actors() or [], "unreal_editor_actor_subsystem")

    def _collect_from_editor_level_library() -> Dict[str, Any]:
        editor_level_library = getattr(unreal, "EditorLevelLibrary", None)
        if editor_level_library is None:
            raise RuntimeError("EditorLevelLibrary is unavailable")

        get_all_level_actors = getattr(editor_level_library, "get_all_level_actors", None)
        if not callable(get_all_level_actors):
            raise RuntimeError("EditorLevelLibrary.get_all_level_actors is unavailable")
        return _build_scene_from_unreal_actors(get_all_level_actors() or [], "editor_level_library")

    collectors = (_collect_from_editor_actor_subsystem, _collect_from_editor_level_library)
    last_error: Optional[Exception] = None

    def _collect_scene() -> Dict[str, Any]:
        nonlocal last_error
        for collector in collectors:
            try:
                return collector()
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("No Unreal scene collector is available")

    status, scene = run_on_game_thread_sync(_collect_scene, max_wait=0.25)
    if status == "pending":
        LOGGER.info("[AIRD] get_scene_context: Unreal scene collector pending")
        return {"actors": [], "source": "pending_game_thread", "count": 0}
    return scene if isinstance(scene, dict) else None
"""

    helper = """def _build_scene_from_unreal_actors(raw_actors: List[Any], source: str) -> Dict[str, Any]:
    actors: List[Dict[str, Any]] = []
    for actor in raw_actors[:REMOTE_CONTROL_MAX_ACTORS]:
        snapshot = _snapshot_unreal_actor(actor)
        if snapshot.get("path") or snapshot.get("name"):
            actors.append(snapshot)

    scene = {
        "actors": actors,
        "source": source,
        "count": len(actors),
    }
    _enrich_scene_data(scene)
    return scene


"""

    updated = original
    if "def _build_scene_from_unreal_actors(" not in updated:
        anchor = "def _snapshot_unreal_actor(actor: Any) -> Dict[str, Any]:\n"
        idx = updated.find(anchor)
        if idx == -1:
            raise RuntimeError(
                "Could not locate insertion point in scene_perception.py"
            )
        anchor_end = updated.find("\n\ndef _get_scene_context_via_unreal()", idx)
        if anchor_end == -1:
            raise RuntimeError("Could not find _get_scene_context_via_unreal block")
        updated = updated[: anchor_end + 2] + helper + updated[anchor_end + 2 :]

    updated = _replace_function_source(
        updated, "_get_scene_context_via_unreal", replacement
    )
    proposal_id = str(uuid4())
    summary = (
        "scene_perception.py still depends on EditorActorSubsystem as the only Unreal collector. "
        "The proposed fix adds EditorLevelLibrary fallback and keeps Remote Control as the final fallback."
    )
    return {
        "proposal_id": proposal_id,
        "file_path": str(file_path),
        "summary": summary,
        "reason": {
            "editor_actor_subsystem": subsystem,
            "remote_control": remote_control,
        },
        "proposed_content": updated,
    }


def _analyze_scene_perception_file() -> Dict[str, Any]:
    proposal = _build_scene_perception_fix_proposal()
    original = _scene_perception_path().read_text(encoding="utf-8")
    diagnostics = _collect_system_diagnostics(20)
    return {
        "ok": True,
        "summary": proposal["summary"],
        "file_path": proposal["file_path"],
        "proposal_id": proposal["proposal_id"],
        "requires_apply_confirmation": True,
        "current_excerpt": original[:1800],
        "proposed_excerpt": str(proposal["proposed_content"])[:2600],
        "proposed_content": proposal["proposed_content"],
        "diagnostics": diagnostics,
    }


def _apply_scene_perception_fix(proposed_content: str) -> Dict[str, Any]:
    file_path = _scene_perception_path()
    content = str(proposed_content or "")
    if not content.strip():
        return {"ok": False, "message": "proposed_content is required"}

    try:
        ast.parse(content, filename=str(file_path))
        compile(content, str(file_path), "exec")
    except SyntaxError as exc:
        return {"ok": False, "message": f"Python syntax validation failed: {exc}"}
    except Exception as exc:
        return {"ok": False, "message": f"Python compile validation failed: {exc}"}

    try:
        original = file_path.read_text(encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "message": f"Failed to read target file: {exc}"}

    backup_path = file_path.with_suffix(".py.bak")
    temp_path = file_path.with_suffix(".py.tmp")

    try:
        temp_path.write_text(content, encoding="utf-8")
        staged = temp_path.read_text(encoding="utf-8")
        ast.parse(staged, filename=str(file_path))
        compile(staged, str(file_path), "exec")
        backup_path.write_text(original, encoding="utf-8")
        file_path.write_text(staged, encoding="utf-8")
    except Exception as exc:
        try:
            if backup_path.exists():
                file_path.write_text(
                    backup_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
        except Exception:
            pass
        return {"ok": False, "message": f"Failed to safely apply fix: {exc}"}
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass

    return {
        "ok": True,
        "file_path": str(file_path),
        "backup_path": str(backup_path),
        "message": "scene_perception.py updated successfully.",
    }


def _update_runtime_config_from_params(params: Dict[str, Any]) -> Dict[str, Any]:
    incoming = {
        "mcp_websocket_port": params.get("mcp_websocket_port"),
        "remote_control_http_port": params.get("remote_control_http_port"),
        "legacy_port": params.get("legacy_port"),
    }
    before = _runtime_config()
    after = save_runtime_config(incoming)
    return {
        "ok": True,
        "message": "Runtime ports saved to config.json",
        "before": before,
        "config": after,
        "config_path": str(_plugin_root() / "config.json"),
    }


def _call_context_aware_llm(
    context_server_url: str,
    provider_id: str,
    model: str,
    api_key: str,
    user_text: str,
    temperature: float,
    max_tokens: int,
    scene: Dict[str, Any],
    image_base64: Optional[str],
) -> Dict[str, Any]:
    if not context_server_url:
        raise RuntimeError("Context server URL is not configured.")
    payload = {
        "providerId": provider_id,
        "model": model,
        "apiKey": api_key,
        "userMessage": user_text,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "scene": scene,
        "vision_context": image_base64 or "",
    }
    response = _post_json(
        f"{context_server_url}/llm/chat",
        payload,
        timeout=60,
        retry_attempts=3,
        operation="context_server_llm_chat",
    )
    if not isinstance(response, dict):
        raise RuntimeError("Invalid response from context server.")
    if str(response.get("status") or "").lower() != "success":
        message = str(response.get("message") or "Context server request failed.")
        raise RuntimeError(message)
    return {
        "reply": str(response.get("reply") or ""),
        "actions": response.get("actions")
        if isinstance(response.get("actions"), list)
        else [],
        "usage_tokens": int(response.get("usage_tokens") or 0),
        "provider": str(response.get("provider") or provider_id),
        "model": str(response.get("model") or model),
    }


def _safe_knowledge_graph(scene: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return build_spatial_graph(scene)
    except Exception as exc:
        LOGGER.exception("Failed to build knowledge graph: %s", exc)
        return {"nodes": [], "edges": []}


def _normalize_provider_id(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "openai"
    normalized = re.sub(r"[^a-z0-9_-]+", "", raw.replace(" ", ""))
    return normalized or "openai"


def _provider_name(provider_id: str) -> str:
    return PROVIDER_NAMES.get(provider_id, provider_id or "Unknown")


def _provider_api_key(provider_id: str, explicit_key: str) -> str:
    if explicit_key.strip():
        return explicit_key.strip()
    env_name = PROVIDER_ENV_KEYS.get(provider_id, "")
    return os.getenv(env_name, "").strip() if env_name else ""


def _resolve_model(provider_id: str, model_ui: str) -> str:
    ui = str(model_ui or "").strip().lower()
    alias_map = MODEL_ALIASES.get(provider_id, {})
    for key, value in alias_map.items():
        if key in ui:
            return value
    return PROVIDER_DEFAULT_MODELS.get(provider_id, "gpt-4o-mini")


def _openai_compatible_body(
    model: str,
    temperature: float,
    max_tokens: int,
    user_text: str,
    scene: Dict[str, Any],
    image_base64: Optional[str],
) -> Dict[str, Any]:
    system_prompt = (
        "You are an Unreal Engine assistant. "
        "Return JSON only with schema "
        '{"reply":"string","actions":[{"type":"spawn_actor|move_actor|generate_blueprint|analyze_scene","description":"string","actor_name":"string","location":{"x":0,"y":0,"z":0},"prompt":"string"}]}. '
        "Keep reply concise and use actions only when needed."
    )

    scene_payload = {
        "actors": scene.get("actors", [])[:300],
        "source": scene.get("source", "unknown"),
    }
    user_content: list[Dict[str, Any]] = [
        {
            "type": "text",
            "text": f"User command:\n{user_text}\n\nScene JSON:\n{json.dumps(scene_payload, ensure_ascii=False)}",
        }
    ]
    if image_base64:
        user_content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_base64}"},
            }
        )

    return {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }


def _anthropic_body(
    model: str,
    max_tokens: int,
    user_text: str,
    scene: Dict[str, Any],
    image_base64: Optional[str],
) -> Dict[str, Any]:
    scene_payload = {
        "actors": scene.get("actors", [])[:300],
        "source": scene.get("source", "unknown"),
    }
    content: list[Dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Return JSON only with schema "
                '{"reply":"string","actions":[{"type":"spawn_actor|move_actor|generate_blueprint|analyze_scene","description":"string","actor_name":"string","location":{"x":0,"y":0,"z":0},"prompt":"string"}]}\n\n'
                f"User command:\n{user_text}\n\nScene JSON:\n{json.dumps(scene_payload, ensure_ascii=False)}"
            ),
        }
    ]
    if image_base64:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image_base64,
                },
            }
        )

    return {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
    }


def _http_json(
    url: str,
    body: Dict[str, Any],
    headers: Dict[str, str],
    timeout: int = 45,
    *,
    operation: str = "provider_http",
    retry_attempts: int = 2,
) -> Dict[str, Any]:
    return _request_json_with_retry(
        url=url,
        method="POST",
        timeout_sec=float(timeout),
        headers=headers,
        body=body,
        operation=operation,
        retry_attempts=retry_attempts,
        retry_base_delay_sec=FALLBACK_RETRY_BASE_DELAY_SEC,
        retry_max_delay_sec=FALLBACK_RETRY_MAX_DELAY_SEC,
    )


def _parse_ai_payload(content: Any, usage_tokens: int) -> Dict[str, Any]:
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text", "")))
        content = "\n".join(text_parts)

    raw = str(content or "{}").strip() or "{}"
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = {"reply": raw, "actions": []}

    if not isinstance(parsed, dict):
        parsed = {"reply": raw, "actions": []}

    parsed.setdefault("reply", "")
    parsed.setdefault("actions", [])
    parsed["usage_tokens"] = int(usage_tokens or 0)
    return parsed


def _run_remote_scene_agent(
    provider_id: str,
    api_key: str,
    model: str,
    temperature: float,
    max_tokens: int,
    user_text: str,
    scene: Dict[str, Any],
    image_base64: Optional[str],
) -> Dict[str, Any]:
    if provider_id == "anthropic":
        payload = _http_json(
            ANTHROPIC_API_URL,
            _anthropic_body(model, max_tokens, user_text, scene, image_base64),
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            operation="provider_anthropic_chat",
            retry_attempts=2,
        )
        message = ((payload.get("content") or [{}])[0].get("text")) or "{}"
        usage = int(((payload.get("usage") or {}).get("input_tokens")) or 0) + int(
            ((payload.get("usage") or {}).get("output_tokens")) or 0
        )
        return _parse_ai_payload(message, usage)

    if provider_id == "openrouter":
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://aird.local",
            "X-Title": "AIRD Unreal Bridge",
        }
        payload = _http_json(
            OPENROUTER_API_URL,
            _openai_compatible_body(
                model, temperature, max_tokens, user_text, scene, image_base64
            ),
            headers,
            operation="provider_openrouter_chat",
            retry_attempts=2,
        )
        message = (
            ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
        ) or "{}"
        usage = int(((payload.get("usage") or {}).get("total_tokens")) or 0)
        return _parse_ai_payload(message, usage)

    if provider_id == "together":
        payload = _http_json(
            TOGETHER_API_URL,
            _openai_compatible_body(
                model, temperature, max_tokens, user_text, scene, image_base64
            ),
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            operation="provider_together_chat",
            retry_attempts=2,
        )
        message = (
            ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
        ) or "{}"
        usage = int(((payload.get("usage") or {}).get("total_tokens")) or 0)
        return _parse_ai_payload(message, usage)

    payload = _http_json(
        OPENAI_API_URL,
        _openai_compatible_body(
            model, temperature, max_tokens, user_text, scene, image_base64
        ),
        {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        operation="provider_openai_chat",
        retry_attempts=2,
    )
    message = (
        ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
    ) or "{}"
    usage = int(((payload.get("usage") or {}).get("total_tokens")) or 0)
    return _parse_ai_payload(message, usage)


def _run_local_scene_agent(
    provider_id: str, model: str, user_text: str, scene: Dict[str, Any]
) -> Dict[str, Any]:
    actor_count = len(scene.get("actors", []))
    provider_name = _provider_name(provider_id)
    reply = (
        f"{provider_name} received the command '{user_text}'. "
        f"Local provider mode is active. Scene actors available: {actor_count}."
    )
    return {
        "reply": reply,
        "actions": [],
        "usage_tokens": 0,
        "mock": True,
        "model": model,
    }


def _to_vector(coords: tuple[float, float, float]):
    unreal = try_import_unreal()
    if unreal is None:
        return coords
    return unreal.Vector(coords[0], coords[1], coords[2])


def _move_actor_by_name(actor_name: str, location_obj: Dict[str, Any]) -> bool:
    if not actor_name:
        return False
    unreal = try_import_unreal()
    if unreal is None:
        return False

    x = float(location_obj.get("x", 0.0))
    y = float(location_obj.get("y", 0.0))
    z = float(location_obj.get("z", 0.0))
    target = None
    for actor in unreal.EditorLevelLibrary.get_all_level_actors():
        if actor.get_name().lower() == actor_name.lower():
            target = actor
            break

    if target is None:
        return False

    target.modify()
    target.set_actor_location(unreal.Vector(x, y, z), False, True)
    return True


def _execute_actions(actions: Any) -> list[str]:
    results: list[str] = []
    if not isinstance(actions, list):
        return results

    for action in actions[:8]:
        if not isinstance(action, dict):
            continue

        action_type = str(action.get("type", "")).strip().lower()
        try:
            if action_type == "spawn_actor":
                loc = action.get("location") or {}
                coords = (
                    float(loc.get("x", 0.0)),
                    float(loc.get("y", 0.0)),
                    float(loc.get("z", 100.0)),
                )
                description = str(action.get("description") or "actor")
                ok = bridge_call(
                    ["spawn_actor_from_description", "SpawnActorFromDescription"],
                    description,
                    _to_vector(coords),
                )
                results.append(
                    f"spawn_actor: {'ok' if ok else 'failed'} ({description})"
                )
            elif action_type == "generate_blueprint":
                prompt = str(action.get("prompt") or "Generated Blueprint")
                outcome = generate_blueprint(prompt)
                results.append(
                    f"generate_blueprint: {outcome.get('status', 'unknown')} ({prompt})"
                )
            elif action_type == "move_actor":
                ok = _move_actor_by_name(
                    str(action.get("actor_name") or ""), action.get("location") or {}
                )
                results.append(f"move_actor: {'ok' if ok else 'failed'}")
            elif action_type == "analyze_scene":
                results.append("analyze_scene: scene refreshed")
        except Exception as exc:
            results.append(f"{action_type or 'action'}: failed ({exc})")
    return results


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _extract_xyz(text: str, default_z: float = 100.0) -> Dict[str, float]:
    matches = re.findall(r"(-?\d+(?:\.\d+)?)", text)
    if len(matches) >= 3:
        return {
            "x": _coerce_float(matches[0], 0.0),
            "y": _coerce_float(matches[1], 0.0),
            "z": _coerce_float(matches[2], default_z),
        }
    if len(matches) == 2:
        return {
            "x": _coerce_float(matches[0], 0.0),
            "y": _coerce_float(matches[1], 0.0),
            "z": default_z,
        }
    return {"x": 0.0, "y": 0.0, "z": default_z}


def _find_actor_by_name(actor_name: str):
    unreal = try_import_unreal()
    if unreal is None:
        return None
    target = str(actor_name or "").strip().lower()
    if not target:
        return None
    for actor in unreal.EditorLevelLibrary.get_all_level_actors():
        actor_label = str(actor.get_actor_label() or "").strip().lower()
        actor_name_raw = str(actor.get_name() or "").strip().lower()
        if target in (actor_label, actor_name_raw):
            return actor
    for actor in unreal.EditorLevelLibrary.get_all_level_actors():
        actor_label = str(actor.get_actor_label() or "").strip().lower()
        actor_name_raw = str(actor.get_name() or "").strip().lower()
        if target in actor_label or target in actor_name_raw:
            return actor
    return None


def _spawn_primitive_actor(
    actor_type: str, location_obj: Dict[str, Any], color: str = ""
) -> Dict[str, Any]:
    normalized_type = str(actor_type or "cube").strip().lower()
    if normalized_type in ("box", "staticmesh", "mesh"):
        normalized_type = "cube"
    if normalized_type not in ("cube", "sphere"):
        return {"status": "error", "message": f"Unsupported actor type: {actor_type}"}

    location = {
        "x": _coerce_float(location_obj.get("x"), 0.0),
        "y": _coerce_float(location_obj.get("y"), 0.0),
        "z": _coerce_float(location_obj.get("z"), 100.0),
    }

    unreal = try_import_unreal()
    spawned_name = f"{normalized_type}_{uuid4().hex[:8]}"
    color_hint = str(color or "").strip().lower()

    if unreal is not None:
        try:
            actor = unreal.EditorLevelLibrary.spawn_actor_from_class(
                unreal.StaticMeshActor,
                unreal.Vector(location["x"], location["y"], location["z"]),
                unreal.Rotator(0.0, 0.0, 0.0),
            )
            if actor is None:
                return {
                    "status": "error",
                    "message": f"Failed to spawn {normalized_type}",
                }

            actor.set_actor_label(spawned_name)
            mesh_path = (
                "/Engine/BasicShapes/Cube.Cube"
                if normalized_type == "cube"
                else "/Engine/BasicShapes/Sphere.Sphere"
            )
            mesh_asset = unreal.EditorAssetLibrary.load_asset(mesh_path)
            sm_comp = actor.get_component_by_class(unreal.StaticMeshComponent)
            if sm_comp is not None and mesh_asset is not None:
                sm_comp.set_static_mesh(mesh_asset)

            message = f"{normalized_type.capitalize()} created at ({location['x']:.2f}, {location['y']:.2f}, {location['z']:.2f})"
            if color_hint:
                message += f" with color hint '{color_hint}'"
            return {
                "status": "success",
                "message": message,
                "actor_name": spawned_name,
                "location": location,
            }
        except Exception as exc:
            LOGGER.exception("Spawn failed for %s", normalized_type)
            return {"status": "error", "message": f"Spawn failed: {exc}"}

    # Fallback to C++ bridge if Unreal Python API is unavailable
    try:
        ok = bridge_call(
            ["spawn_actor_from_description", "SpawnActorFromDescription"],
            normalized_type,
            _to_vector((location["x"], location["y"], location["z"])),
        )
        if ok:
            return {
                "status": "success",
                "message": f"{normalized_type.capitalize()} created via bridge",
                "location": location,
            }
        return {
            "status": "error",
            "message": f"Bridge failed to spawn {normalized_type}",
        }
    except Exception as exc:
        return {"status": "error", "message": f"Spawn bridge failed: {exc}"}


def _move_actor_command(
    actor_name: str, location_obj: Dict[str, Any]
) -> Dict[str, Any]:
    actor_name = str(actor_name or "").strip()
    if not actor_name:
        return {"status": "error", "message": "Actor name is required for move_actor"}

    location = {
        "x": _coerce_float(location_obj.get("x"), 0.0),
        "y": _coerce_float(location_obj.get("y"), 0.0),
        "z": _coerce_float(location_obj.get("z"), 0.0),
    }

    unreal = try_import_unreal()
    if unreal is not None:
        actor = _find_actor_by_name(actor_name)
        if actor is None:
            return {"status": "error", "message": f"Actor not found: {actor_name}"}
        try:
            actor.set_actor_location(
                unreal.Vector(location["x"], location["y"], location["z"]), False, True
            )
            return {
                "status": "success",
                "message": f"Actor '{actor_name}' moved",
                "location": location,
            }
        except Exception as exc:
            return {"status": "error", "message": f"Move failed: {exc}"}

    ok = _move_actor_by_name(actor_name, location)
    if ok:
        return {
            "status": "success",
            "message": f"Actor '{actor_name}' moved",
            "location": location,
        }
    return {"status": "error", "message": f"Actor not found: {actor_name}"}


def _delete_actor_command(actor_name: str) -> Dict[str, Any]:
    actor_name = str(actor_name or "").strip()
    if not actor_name:
        return {"status": "error", "message": "Actor name is required for delete_actor"}

    unreal = try_import_unreal()
    if unreal is None:
        return {
            "status": "error",
            "message": "Unreal Python API unavailable for delete_actor",
        }

    actor = _find_actor_by_name(actor_name)
    if actor is None:
        return {"status": "error", "message": f"Actor not found: {actor_name}"}
    try:
        unreal.EditorLevelLibrary.destroy_actor(actor)
        return {"status": "success", "message": f"Actor '{actor_name}' deleted"}
    except Exception as exc:
        return {"status": "error", "message": f"Delete failed: {exc}"}


def parse_command_payload(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict):
        action = str(payload.get("action") or "").strip().lower()
        if action:
            normalized = {
                "action": action,
                "actor": str(payload.get("actor") or payload.get("shape") or "")
                .strip()
                .lower(),
                "actor_name": str(
                    payload.get("actor_name") or payload.get("name") or ""
                ).strip(),
                "location": payload.get("location")
                if isinstance(payload.get("location"), dict)
                else {},
                "color": str(payload.get("color") or "").strip().lower(),
            }
            if not normalized["location"]:
                normalized["location"] = {
                    "x": _coerce_float(payload.get("x"), 0.0),
                    "y": _coerce_float(payload.get("y"), 0.0),
                    "z": _coerce_float(payload.get("z"), 100.0),
                }
            return normalized
        payload = str(payload.get("payload") or payload.get("text") or "").strip()

    text = str(payload or "").strip()
    if not text:
        raise ValueError("Command payload is empty")

    low = text.lower()
    location = _extract_xyz(low, default_z=100.0)
    color_match = re.search(
        r"\b(red|green|blue|yellow|white|black|orange|purple)\b", low
    )
    color = color_match.group(1) if color_match else ""

    if "create" in low or "spawn" in low:
        actor = "sphere" if "sphere" in low or "ball" in low else "cube"
        return {
            "action": "spawn_actor",
            "actor": actor,
            "location": location,
            "color": color,
        }

    if "move" in low:
        name_match = re.search(r"move\s+actor\s+([a-zA-Z0-9_\-]+)", low)
        actor_name = name_match.group(1) if name_match else ""
        return {"action": "move_actor", "actor_name": actor_name, "location": location}

    if "delete" in low or "destroy" in low or "remove" in low:
        name_match = re.search(
            r"(?:delete|destroy|remove)\s+actor\s+([a-zA-Z0-9_\-]+)", low
        )
        actor_name = name_match.group(1) if name_match else ""
        return {"action": "delete_actor", "actor_name": actor_name}

    if "scan" in low and "scene" in low:
        return {"action": "scan_scene"}

    if "show" in low and "light" in low:
        return {"action": "get_scene_lights"}

    if "scene" in low and ("summary" in low or "stats" in low):
        return {"action": "get_scene_summary"}

    raise ValueError(f"Unsupported command: {text}")


def execute_normalized_command(cmd: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(cmd, dict):
        return {"status": "error", "message": "Command object expected"}

    action = str(cmd.get("action") or "").strip().lower()
    if action == "spawn_actor":
        actor = str(cmd.get("actor") or "cube").strip().lower() or "cube"
        location = cmd.get("location") if isinstance(cmd.get("location"), dict) else {}
        color = str(cmd.get("color") or "").strip().lower()
        return _spawn_primitive_actor(actor, location, color)
    if action == "move_actor":
        return _move_actor_command(
            str(cmd.get("actor_name") or ""), cmd.get("location") or {}
        )
    if action == "delete_actor":
        return _delete_actor_command(str(cmd.get("actor_name") or ""))
    if action == "scan_scene":
        from scene_analysis import SceneProcessor

        processor = SceneProcessor()
        result = processor.process_scene()
        return {"status": "success", "result": result.to_dict()}
    if action == "get_scene_lights":
        from scene_analysis import SceneQueryAPI

        api = SceneQueryAPI()
        lights = api.get_all_lights()
        return {"status": "success", "lights": lights}
    if action == "get_scene_summary":
        from scene_analysis import SceneQueryAPI

        api = SceneQueryAPI()
        summary = api.get_quick_summary()
        return {"status": "success", "summary": summary}
    return {"status": "error", "message": f"Unknown action: {action}"}


def execute_command_dispatcher(cmd: Dict[str, Any]) -> Dict[str, Any]:
    return execute_normalized_command(cmd)


async def execute_command(params: Dict[str, Any]) -> Dict[str, Any]:
    _bootstrap_phase2_components()
    request_id = _resolve_request_id(params.get("request_id"))
    text = str(params.get("text") or "").strip()
    hidden_context = str(params.get("hidden_context") or "").strip()
    _trace_flow(
        "SERVER_RECEIVED",
        request_id,
        method="execute_command",
        text_preview=text[:140],
    )
    if not text:
        _trace_flow("PARSE_FAILURE", request_id, reason="empty_command_text")
        return {
            "ok": False,
            "message": "Empty command.",
            "scene": _safe_scene_context(),
            "actions": [],
            "request_id": request_id,
        }

    provider_id = _normalize_provider_id(params.get("provider_id") or "openai")
    if _is_blueprint_runtime_command(text):
        _structured_log("blueprint_command_received", text_preview=text[:140])
        runtime_status = await asyncio.to_thread(
            _runtime_status_snapshot, None, True
        )
        if not bool(runtime_status.get("unreal_runtime_connected")):
            _trace_flow(
                "RUNTIME_UNAVAILABLE",
                request_id,
                reason="blueprint_runtime_not_connected",
            )
            scene_snapshot = await asyncio.to_thread(_safe_scene_context)
            message = (
                "unreal_runtime_unavailable: Blueprint operations must run inside Unreal runtime. "
                "Start AIRD Engine in Unreal, then retry."
            )
            return {
                "ok": False,
                "error": "unreal_runtime_unavailable",
                "message": message,
                "provider": provider_id,
                "scene": scene_snapshot,
                "knowledge_graph": _safe_knowledge_graph(scene_snapshot),
                "diagnostics": {
                    "runtime_status": runtime_status,
                    "next_step": "Start AIRD Engine inside Unreal and reconnect the UI to that MCP session.",
                },
                "runtime_status": runtime_status,
                "actions": [],
                "usage_tokens": 0,
                "request_id": request_id,
            }

    provider_name = _provider_name(provider_id)
    api_key = _provider_api_key(provider_id, str(params.get("api_key") or ""))
    model = _resolve_model(provider_id, str(params.get("model_ui") or ""))
    temperature = float(params.get("temperature", 0.3) or 0.3)
    max_tokens = int(params.get("max_tokens", 800) or 800)
    image_base64 = str(params.get("image_base64") or "").strip() or None
    context_server_url = _resolve_context_server_url(params)
    preferred_agent = str(
        params.get("agent") or params.get("agent_override") or ""
    ).strip()
    request_context = await asyncio.to_thread(
        _build_project_context_request_context, params
    )
    orchestrated = (
        _ORCHESTRATOR.process(
            text=text,
            request={
                "text": text,
                "request_id": request_id,
                "request_context": request_context,
            },
            preferred_agent=preferred_agent,
        )
        if _ORCHESTRATOR is not None
        else {
            "defer_scene_pipeline": True,
            "routing": {
                "agent": "sceneagent",
                "confidence": 0.0,
                "reason": "bootstrap-unavailable-fallback",
                "preferred_agent": preferred_agent or None,
                "registry": [],
            },
        }
    )
    routing = (
        orchestrated.get("routing", {})
        if isinstance(orchestrated, dict)
        else {"agent": "sceneagent"}
    )
    _structured_log(
        "routing_decision",
        request_id=request_id,
        text_preview=text[:120],
        agent=routing.get("agent"),
        confidence=routing.get("confidence"),
        reason=routing.get("reason"),
        preferred_agent=routing.get("preferred_agent"),
    )
    _trace_flow(
        "ORCHESTRATOR_ROUTE_SELECTED",
        request_id,
        route=routing.get("agent"),
        confidence=routing.get("confidence"),
        reason=routing.get("reason"),
    )

    if isinstance(orchestrated, dict) and not bool(
        orchestrated.get("defer_scene_pipeline", False)
    ):
        _trace_flow(
            "EXECUTED",
            request_id,
            route=routing.get("agent"),
            path="orchestrator_agent",
        )
        usage_tokens = int(orchestrated.get("usage_tokens") or 0)
        message = str(orchestrated.get("message") or "").strip() or "Done."
        scene = orchestrated.get("scene")
        if not isinstance(scene, dict):
            scene = await asyncio.to_thread(_safe_scene_context)
        knowledge_graph = orchestrated.get("knowledge_graph")
        if not isinstance(knowledge_graph, dict):
            knowledge_graph = await asyncio.to_thread(_safe_knowledge_graph, scene)
        memory_record_id = await _save_conversation_record(
            text=text,
            message=message,
            routing=routing if isinstance(routing, dict) else {"agent": "sceneagent"},
            provider_id=str(orchestrated.get("provider") or "local-agent"),
            model=str(orchestrated.get("model") or "none"),
            scene_stale=bool(orchestrated.get("scene_stale", False)),
            usage_tokens=usage_tokens,
            params=params,
        )
        return {
            "ok": bool(orchestrated.get("ok", False)),
            "error": orchestrated.get("error"),
            "message": message,
            "provider": str(orchestrated.get("provider") or "local-agent"),
            "model": str(orchestrated.get("model") or "none"),
            "routing": routing,
            "scene": scene,
            "scene_stale": bool(orchestrated.get("scene_stale", False)),
            "knowledge_graph": knowledge_graph,
            "actions": orchestrated.get("actions", []),
            "usage_tokens": usage_tokens,
            "memory_record_id": int(memory_record_id or 0),
            "demo": bool(orchestrated.get("demo", False)),
            "code_metrics": orchestrated.get("code_metrics"),
            "content_parser": orchestrated.get("content_parser"),
            "runtime_status": _runtime_status_snapshot(scene, include_probes=False),
            "request_id": request_id,
        }

    _trace_flow(
        "AGENT_BYPASSED",
        request_id,
        reason="defer_scene_pipeline",
        route=routing.get("agent"),
    )
    routed_agent = str((routing or {}).get("agent") or "").strip().lower()

    if routed_agent == "sceneagent" and _is_scene_runtime_command(text):
        bridge_scene_result = await asyncio.to_thread(
            call_runtime_bridge,
            "get_scene_context",
            {},
            3.0,
            request_id,
        )
        if bool(bridge_scene_result.get("ok")) and isinstance(
            bridge_scene_result.get("scene"), dict
        ):
            scene = bridge_scene_result.get("scene") or {}
            knowledge_graph = await asyncio.to_thread(_safe_knowledge_graph, scene)
            _trace_flow(
                "EXECUTED",
                request_id,
                route="sceneagent",
                path="runtime_bridge_scene",
            )
            return {
                "ok": True,
                "message": "Scene analysis executed via Unreal runtime bridge.",
                "provider": "runtime-bridge",
                "model": "none",
                "routing": routing,
                "scene": scene,
                "scene_stale": False,
                "knowledge_graph": knowledge_graph,
                "actions": [],
                "usage_tokens": 0,
                "runtime_status": _runtime_status_snapshot(scene, include_probes=False),
                "request_id": request_id,
            }

        runtime_status = await asyncio.to_thread(_runtime_status_snapshot, None, True)
        _trace_flow(
            "RUNTIME_UNAVAILABLE",
            request_id,
            reason=str(
                bridge_scene_result.get("error")
                or bridge_scene_result.get("message")
                or "runtime_bridge_scene_unavailable"
            ),
        )
        return {
            "ok": False,
            "error": "unreal_runtime_unavailable",
            "message": str(
                bridge_scene_result.get("message")
                or "Unreal runtime bridge is unavailable for scene analysis."
            ),
            "provider": "runtime-bridge",
            "routing": routing,
            "scene": {"actors": [], "source": "runtime_bridge_unavailable", "count": 0},
            "knowledge_graph": _safe_knowledge_graph(
                {"actors": [], "source": "runtime_bridge_unavailable", "count": 0}
            ),
            "diagnostics": {"runtime_status": runtime_status},
            "runtime_status": runtime_status,
            "actions": [],
            "usage_tokens": 0,
            "request_id": request_id,
        }

    if routed_agent == "blueprintagent":
        _trace_flow(
            "PARSE_FAILURE",
            request_id,
            reason="blueprint_route_deferred_without_deterministic_payload",
        )
        return {
            "ok": False,
            "error": "parse_failure",
            "message": "Blueprint command could not be parsed into a deterministic action payload.",
            "provider": "local-blueprint-agent",
            "routing": routing,
            "actions": [],
            "usage_tokens": 0,
            "request_id": request_id,
        }

    effective_text = (
        f"{text}\n\n[Hidden diagnostic context]\n{hidden_context}"
        if hidden_context
        else text
    )

    scene, stale_scene = await asyncio.to_thread(_get_effective_scene_context)
    if not _has_required_scene_context(scene):
        diagnostics = await asyncio.to_thread(
            _build_missing_scene_context_diagnostics, scene
        )
        return {
            "ok": False,
            "error": "scene_context_unavailable",
            "message": diagnostics.get("summary") or MISSING_SCENE_CONTEXT_MESSAGE,
            "provider": provider_id,
            "scene": scene,
            "knowledge_graph": _safe_knowledge_graph(scene),
            "diagnostics": diagnostics,
            "runtime_status": diagnostics.get("runtime_status", {}),
            "actions": [],
            "usage_tokens": 0,
            "request_id": request_id,
        }

    if provider_id not in ("ollama", "lmstudio") and not api_key:
        _trace_flow(
            "FALLBACK_TO_CHAT",
            request_id,
            reason="missing_provider_api_key",
            provider=provider_id,
        )
        return {
            "ok": False,
            "message": f"Missing API key for {provider_name}.",
            "provider": provider_id,
            "scene": scene,
            "knowledge_graph": _safe_knowledge_graph(scene),
            "actions": [],
            "usage_tokens": 0,
            "request_id": request_id,
        }

    try:
        try:
            health = await asyncio.to_thread(_context_server_health, context_server_url)
            if str(health.get("status") or "").lower() == "ok" and not bool(
                health.get("stable", True)
            ):
                await asyncio.to_thread(
                    _context_server_trim_memory, context_server_url, 8, False
                )
        except Exception as health_error:
            LOGGER.info("Context server preflight skipped: %s", health_error)

        ai_plan = await asyncio.to_thread(
            _call_context_aware_llm,
            context_server_url,
            provider_id,
            model,
            api_key,
            effective_text,
            temperature,
            max_tokens,
            scene,
            image_base64,
        )
    except Exception as context_error:
        if _is_context_length_error(context_error):
            try:
                await asyncio.to_thread(
                    _context_server_trim_memory, context_server_url, 4, False
                )
            except Exception as trim_error:
                LOGGER.info("Context length recovery trim failed: %s", trim_error)
        LOGGER.warning(
            "Context server unavailable, using direct provider fallback: %s",
            context_error,
        )
        if provider_id in ("ollama", "lmstudio"):
            ai_plan = await asyncio.to_thread(
                _run_local_scene_agent, provider_id, model, text, scene
            )
        else:
            ai_plan = await asyncio.to_thread(
                _run_remote_scene_agent,
                provider_id,
                api_key,
                model,
                temperature,
                max_tokens,
                effective_text,
                scene,
                image_base64,
            )

    action_results = await asyncio.to_thread(
        _execute_actions, ai_plan.get("actions", [])
    )
    latest_scene, latest_stale = await asyncio.to_thread(_get_effective_scene_context)
    knowledge_graph = await asyncio.to_thread(_safe_knowledge_graph, latest_scene)

    message = str(ai_plan.get("reply") or "").strip() or "Done."
    if action_results:
        message += "\n\nAction Results:\n- " + "\n- ".join(action_results)

    memory_record_id = await _save_conversation_record(
        text=text,
        message=message,
        routing=routing if isinstance(routing, dict) else {"agent": "sceneagent"},
        provider_id=provider_id,
        model=model,
        scene_stale=bool(stale_scene or latest_stale),
        usage_tokens=int(ai_plan.get("usage_tokens") or 0),
        params=params,
    )
    _trace_flow(
        "EXECUTED",
        request_id,
        route=routing.get("agent") if isinstance(routing, dict) else "sceneagent",
        path="scene_pipeline",
    )

    return {
        "ok": True,
        "message": message,
        "provider": provider_id,
        "model": model,
        "routing": routing,
        "scene": latest_scene,
        "scene_stale": bool(stale_scene or latest_stale),
        "knowledge_graph": knowledge_graph,
        "actions": ai_plan.get("actions", []),
        "usage_tokens": int(ai_plan.get("usage_tokens") or 0),
        "memory_record_id": int(memory_record_id or 0),
        "demo": bool(ai_plan.get("mock", False)),
        "runtime_status": _runtime_status_snapshot(
            latest_scene, include_probes=False
        ),
        "request_id": request_id,
    }


class MCPBridgeServer:
    def __init__(self) -> None:
        self.connections: set[Any] = set()
        self.context_server_url = DEFAULT_CONTEXT_SERVER_URL.rstrip("/")
        self.scene_sync_interval = max(0.5, SCENE_SYNC_INTERVAL_SEC)
        self.scene_sync_task: Optional[asyncio.Task[Any]] = None
        self.heartbeat_task: Optional[asyncio.Task[Any]] = None
        self.context_server_ready = False
        self.last_heartbeat_error = ""
        self._scene_sync_failures = 0

    @staticmethod
    def _peer_name(ws: Any) -> str:
        peer = ws.remote_address
        if isinstance(peer, tuple) and len(peer) >= 2:
            return f"{peer[0]}:{peer[1]}"
        return "unknown"

    async def _send_json(self, ws: Any, payload: Dict[str, Any]) -> None:
        message = _json_dumps(payload)
        LOGGER.info("TX %s %s", self._peer_name(ws), _safe_log_payload(payload))
        await ws.send(message)

    def start_background_tasks(self) -> None:
        if self.scene_sync_task and not self.scene_sync_task.done():
            return
        self.scene_sync_task = asyncio.create_task(
            self._scene_sync_loop(), name="aird_scene_sync_loop"
        )
        if HEARTBEAT_INTERVAL_SEC > 0:
            self.heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(), name="aird_heartbeat_loop"
            )
        LOGGER.info(
            "Scene sync task started -> %s (interval=%ss)",
            self.context_server_url,
            self.scene_sync_interval,
        )

    async def stop_background_tasks(self) -> None:
        task = self.scene_sync_task
        self.scene_sync_task = None
        hb = self.heartbeat_task
        self.heartbeat_task = None
        if task is None:
            pass
        else:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if hb is not None:
            hb.cancel()
            try:
                await hb
            except asyncio.CancelledError:
                pass

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                health = await asyncio.to_thread(
                    _context_server_health, self.context_server_url
                )
                self.context_server_ready = (
                    str(health.get("status") or "").lower() == "ok"
                )
                self.last_heartbeat_error = ""
            except Exception as exc:
                self.context_server_ready = False
                self.last_heartbeat_error = str(exc)
                LOGGER.warning("Context server heartbeat failed: %s", exc)
            await asyncio.sleep(max(2.0, HEARTBEAT_INTERVAL_SEC))

    async def _scene_sync_loop(self) -> None:
        startup_delay = max(0.0, SCENE_SYNC_STARTUP_DELAY_SEC)
        if startup_delay > 0.0:
            LOGGER.info("Scene sync warmup delay %.2fs", startup_delay)
            await asyncio.sleep(startup_delay)

        while True:
            try:
                scene = await asyncio.to_thread(_safe_scene_context)
                if _has_required_scene_context(scene):
                    await asyncio.to_thread(
                        _sync_scene_snapshot_with_retry, self.context_server_url, scene
                    )
                    if self._scene_sync_failures:
                        LOGGER.info(
                            "Scene sync recovered after %s failures",
                            self._scene_sync_failures,
                        )
                        self._scene_sync_failures = 0
                else:
                    LOGGER.debug("Skipping scene sync due to missing context")
            except Exception as exc:
                self._scene_sync_failures += 1
                if (
                    self._scene_sync_failures <= 3
                    or self._scene_sync_failures % 10 == 0
                ):
                    LOGGER.warning(
                        "Scene sync failed #%s: %s", self._scene_sync_failures, exc
                    )
            await asyncio.sleep(self.scene_sync_interval)

    async def handle_client(self, websocket: Any) -> None:
        self.start_background_tasks()
        self.connections.add(websocket)
        peer = self._peer_name(websocket)
        LOGGER.info("Client connected: %s active=%s", peer, len(self.connections))
        await self._send_json(
            websocket, {"status": "ok", "message": "connected", "type": "hello"}
        )
        try:
            async for raw_message in websocket:
                LOGGER.info("RX %s %s", peer, _safe_log_payload(raw_message))
                response = await self.dispatch_message(raw_message)
                if response is not None:
                    await self._send_json(websocket, response)
        except ConnectionClosed:
            LOGGER.info("Client disconnected: %s", peer)
        except Exception as exc:
            LOGGER.exception("Connection error for %s: %s", peer, exc)
            try:
                await self._send_json(
                    websocket, {"status": "error", "message": str(exc)}
                )
            except Exception:
                pass
        finally:
            self.connections.discard(websocket)
            LOGGER.info("Connection closed: %s active=%s", peer, len(self.connections))

    async def dispatch_message(self, raw_message: str) -> Optional[Dict[str, Any]]:
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError as exc:
            return {"status": "error", "message": f"Invalid JSON: {exc.msg}"}

        if not isinstance(payload, dict):
            return {"status": "error", "message": "JSON object required"}

        if payload.get("type") == "ping":
            return {"status": "ok", "message": "pong", "type": "pong"}

        if payload.get("type") == "command":
            return await self.handle_command_message(payload)

        if payload.get("type") == "batch":
            return await self.handle_batch_command(payload)

        if "method" in payload:
            return await self.handle_rpc(payload)

        return {"status": "error", "message": "Unsupported message format"}

    async def handle_command_message(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        _bootstrap_phase2_components()
        request_id = _resolve_request_id(payload.get("requestId") or payload.get("id"))
        _trace_flow("SERVER_RECEIVED", request_id, method="command", transport="ws")
        raw_command = payload.get("payload")
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        try:
            normalized = parse_command_payload(raw_command)
            LOGGER.info("Parsed command: %s", normalized)
            _structured_log(
                "routing_decision",
                request_id=request_id,
                text_preview=str(raw_command or "")[:120],
                agent="command-dispatcher",
                confidence=1.0,
                reason="deterministic-parser",
                preferred_agent=None,
            )
            _trace_flow(
                "ORCHESTRATOR_ROUTE_SELECTED",
                request_id,
                route="command-dispatcher",
                reason="deterministic-parser",
            )
            executed = await asyncio.to_thread(execute_command_dispatcher, normalized)
            scene = await asyncio.to_thread(_safe_scene_context)
            status = "success" if executed.get("status") == "success" else "error"
            if status == "success":
                _trace_flow("EXECUTED", request_id, route="command-dispatcher")
            response = {
                "type": "command_result",
                "requestId": request_id,
                "status": status,
                "message": str(executed.get("message") or ""),
                "normalized": normalized,
                "result": executed,
                "scene": scene,
            }
            return response
        except ValueError as exc:
            LOGGER.info("Command parser fallback to AI executor: %s", exc)
            _trace_flow(
                "PARSE_FAILURE",
                request_id,
                reason=str(exc),
                fallback="execute_command",
            )
            ai_params = dict(meta)
            ai_params["request_id"] = request_id
            ai_params["text"] = str(raw_command or ai_params.get("text") or "").strip()
            ai_result = await execute_command(ai_params)
            status = "success" if bool(ai_result.get("ok")) else "error"
            return {
                "type": "command_result",
                "requestId": request_id,
                "status": status,
                "message": str(ai_result.get("message") or ""),
                "result": ai_result,
                "scene": ai_result.get("scene")
                or await asyncio.to_thread(_safe_scene_context),
            }
        except Exception as exc:
            LOGGER.exception("Command execution failed")
            return {
                "type": "command_result",
                "requestId": request_id,
                "status": "error",
                "message": str(exc),
            }

    async def handle_batch_command(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        request_id = payload.get("requestId") or payload.get("id")
        commands = payload.get("commands")
        if not isinstance(commands, list):
            return {
                "type": "batch_result",
                "requestId": request_id,
                "status": "error",
                "message": "commands must be a list",
                "results": [],
            }

        batch_results: list[Dict[str, Any]] = []
        for index, command_payload in enumerate(commands[:20]):
            try:
                normalized = parse_command_payload(command_payload)
                LOGGER.info("Parsed batch command[%s]: %s", index, normalized)
                executed = await asyncio.to_thread(
                    execute_command_dispatcher, normalized
                )
                batch_results.append(
                    {
                        "index": index,
                        "status": executed.get("status", "error"),
                        "message": executed.get("message", ""),
                        "normalized": normalized,
                        "result": executed,
                    }
                )
            except Exception as exc:
                batch_results.append(
                    {
                        "index": index,
                        "status": "error",
                        "message": str(exc),
                    }
                )

        has_error = any(item.get("status") != "success" for item in batch_results)
        return {
            "type": "batch_result",
            "requestId": request_id,
            "status": "error" if has_error else "success",
            "message": "Batch executed"
            if not has_error
            else "Batch completed with errors",
            "results": batch_results,
            "scene": await asyncio.to_thread(_safe_scene_context),
        }

    async def handle_rpc(self, request: Dict[str, Any]) -> Dict[str, Any]:
        _bootstrap_phase2_components()
        rpc_id = request.get("id")
        method = str(request.get("method") or "").strip()
        params = request.get("params", {}) or {}
        if not isinstance(params, dict):
            params = {}
        request_id = _resolve_request_id(params.get("request_id") or rpc_id)
        _trace_flow("SERVER_RECEIVED", request_id, method=method or "unknown", transport="jsonrpc")

        try:
            if method in MUTATING_RPC_METHODS:
                authorized, auth_error = _authorize_rpc_mutation(request, params)
                if not authorized:
                    raise PermissionError(auth_error)

            if method == "ping":
                result = {"status": "ok", "message": "pong"}
            elif method == "get_scene_context":
                result = await asyncio.to_thread(
                    _safe_scene_context,
                    _force_scene_refresh_from_params(params),
                )
            elif method == "analyze_scene":
                scene = await asyncio.to_thread(
                    _safe_scene_context,
                    _force_scene_refresh_from_params(params),
                )
                result = {
                    "scene": scene,
                    "knowledge_graph": await asyncio.to_thread(
                        _safe_knowledge_graph, scene
                    ),
                }
            elif method == "capture_viewport":
                result = {
                    "image_base64": await asyncio.to_thread(capture_viewport_base64)
                }
            elif method == "sync_scene_context":
                scene = await asyncio.to_thread(
                    _safe_scene_context,
                    _force_scene_refresh_from_params(params),
                )
                if not _has_required_scene_context(scene):
                    result = {
                        "ok": False,
                        "message": MISSING_SCENE_CONTEXT_MESSAGE,
                        "scene": scene,
                    }
                else:
                    sync_result = await asyncio.to_thread(
                        _sync_scene_snapshot_with_retry, self.context_server_url, scene
                    )
                    result = {"ok": True, "scene": scene, "sync": sync_result}
            elif method == "get_runtime_status":
                scene = await asyncio.to_thread(
                    _safe_scene_context,
                    _force_scene_refresh_from_params(params),
                )
                result = await asyncio.to_thread(
                    _runtime_status_snapshot, scene, False
                )
            elif method == "system_health":
                scene = await asyncio.to_thread(
                    _safe_scene_context,
                    _force_scene_refresh_from_params(params),
                )
                runtime_status = await asyncio.to_thread(
                    _runtime_status_snapshot, scene, True
                )
                result = {
                    "mcp": "ok",
                    "context_server_url": self.context_server_url,
                    "context_server_ready": bool(self.context_server_ready),
                    "heartbeat_error": self.last_heartbeat_error,
                    "scene_context_valid": _has_required_scene_context(scene),
                    "actor_count": len(scene.get("actors", []))
                    if isinstance(scene.get("actors"), list)
                    else 0,
                    "runtime_status": runtime_status,
                    "unreal_runtime_connected": bool(
                        runtime_status.get("unreal_runtime_connected")
                    ),
                }
            elif method == "system_diagnostics":
                result = await asyncio.to_thread(
                    _collect_system_diagnostics,
                    int(params.get("max_log_lines", 20) or 20),
                )
            elif method == ACTION_RESPONSE_CONTRACT_METHOD:
                result = await asyncio.to_thread(_action_response_contract_documentation)
            elif method == RELIABILITY_PROFILE_CONTRACT_METHOD:
                result = await asyncio.to_thread(_reliability_profile_documentation)
            elif method == PROJECT_CONTEXT_RPC_METHOD:
                result = await asyncio.to_thread(_project_context_rpc_result, params)
            elif method == "get_runtime_config":
                result = {
                    "ok": True,
                    "config": await asyncio.to_thread(_runtime_config),
                    "config_path": str(_plugin_root() / "config.json"),
                }
            elif method == "update_runtime_config":
                result = await asyncio.to_thread(
                    _update_runtime_config_from_params, params
                )
            elif method == "get_history":
                if _MEMORY_MANAGER is None:
                    result = {"ok": False, "message": "Memory manager unavailable", "history": []}
                else:
                    limit = int(params.get("limit", 20) or 20)
                    history = await asyncio.to_thread(_MEMORY_MANAGER.get_history, limit)
                    result = {"ok": True, "history": history, "count": len(history)}
            elif method == "search_history":
                if _MEMORY_MANAGER is None:
                    result = {"ok": False, "message": "Memory manager unavailable", "history": []}
                else:
                    query = str(params.get("query") or "").strip()
                    limit = int(params.get("limit", 20) or 20)
                    history = await asyncio.to_thread(
                        _MEMORY_MANAGER.search_history, query, limit
                    )
                    result = {
                        "ok": True,
                        "query": query,
                        "history": history,
                        "count": len(history),
                    }
            elif method == "clear_history":
                if _MEMORY_MANAGER is None:
                    result = {"ok": False, "message": "Memory manager unavailable", "deleted": 0}
                else:
                    deleted = await asyncio.to_thread(_MEMORY_MANAGER.clear_history)
                    result = {"ok": True, "deleted": int(deleted or 0)}
            elif method == "analyze_scene_perception":
                result = await asyncio.to_thread(_analyze_scene_perception_file)
            elif method == "apply_scene_perception_fix":
                proposed_content = str(params.get("proposed_content") or "")
                if not proposed_content.strip():
                    raise RuntimeError("proposed_content is required")
                result = await asyncio.to_thread(
                    _apply_scene_perception_fix, proposed_content
                )
            elif method == "generate_blueprint":
                result = await asyncio.to_thread(
                    generate_blueprint,
                    str(params.get("prompt") or "AIRD Generated Actor"),
                )
            elif method == "scan_scene":
                from scene_analysis import SceneQueryAPI

                api = SceneQueryAPI()
                result = api.get_scene_summary()
            elif method == "get_scene_lights":
                from scene_analysis import SceneQueryAPI

                api = SceneQueryAPI()
                result = api.get_all_lights()
            elif method == "get_scene_actors":
                from scene_analysis import SceneQueryAPI

                api = SceneQueryAPI()
                category = params.get("category", "Other")
                result = api.get_by_category(category)
            elif method == "get_scene_bounds":
                from scene_analysis import SceneQueryAPI

                api = SceneQueryAPI()
                result = api.get_scene_bounds()
            elif method == "get_scene_quick_summary":
                from scene_analysis import SceneQueryAPI

                api = SceneQueryAPI()
                result = api.get_quick_summary()
            elif method == "get_scene_pie_chart":
                from scene_analysis import SceneVisualizationData

                viz = SceneVisualizationData()
                result = viz.get_pie_chart_data()
            elif method == "get_visualization_html":
                from scene_analysis import get_scene_visualization_html

                result = {"html": get_scene_visualization_html()}
            elif method == "get_actor_list":
                from scene_analysis import SceneVisualizationData

                viz = SceneVisualizationData()
                category = params.get("category", "Other")
                limit = int(params.get("limit", 100) or 100)
                result = viz.get_actor_list_by_category(category, limit)
            elif method == "execute_command":
                params = dict(params)
                params.setdefault("request_id", request_id)
                result = await execute_command(params)
            else:
                raise RuntimeError(f"Unknown method: {method}")

            return {"jsonrpc": JSONRPC_VERSION, "id": rpc_id, "result": result}
        except PermissionError as exc:
            LOGGER.warning("RPC authorization failed method=%s: %s", method, exc)
            return {
                "jsonrpc": JSONRPC_VERSION,
                "id": rpc_id,
                "error": {"code": -32001, "message": str(exc)},
            }
        except Exception as exc:
            LOGGER.exception("RPC failed method=%s", method)
            return {
                "jsonrpc": JSONRPC_VERSION,
                "id": rpc_id,
                "error": {"code": -32000, "message": str(exc)},
            }


async def run_server(host: str = "127.0.0.1", port: Optional[int] = None) -> None:
    configure_logging()
    _prepare_runtime_buffers()
    _bootstrap_phase2_components()
    if port is None:
        port = _runtime_port("mcp_websocket_port", DEFAULT_CONFIG["mcp_websocket_port"])
    bridge = MCPBridgeServer()
    LOGGER.info("Starting MCP WebSocket server on ws://%s:%s", host, port)
    try:
        async with websockets.serve(
            bridge.handle_client,
            host,
            port,
            max_size=2**22,
            ping_interval=20,
            ping_timeout=20,
        ):
            await asyncio.Future()
    finally:
        await bridge.stop_background_tasks()


if __name__ == "__main__":
    asyncio.run(run_server())
