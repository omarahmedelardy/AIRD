from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

LOGGER = logging.getLogger("aird.mcp")

HEARTBEAT_MAX_AGE_SEC = float(
    os.getenv("AIRD_RUNTIME_BRIDGE_HEARTBEAT_MAX_AGE", "8.0") or "8.0"
)
REQUEST_TIMEOUT_SEC = float(
    os.getenv("AIRD_RUNTIME_BRIDGE_REQUEST_TIMEOUT", "8.0") or "8.0"
)
REQUEST_POLL_INTERVAL_SEC = float(
    os.getenv("AIRD_RUNTIME_BRIDGE_POLL_INTERVAL", "0.05") or "0.05"
)


def _plugin_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _bridge_root() -> Path:
    return _plugin_root() / "memory" / "runtime_bridge"


def _requests_dir() -> Path:
    return _bridge_root() / "requests"


def _responses_dir() -> Path:
    return _bridge_root() / "responses"


def _heartbeat_path() -> Path:
    return _bridge_root() / "heartbeat.json"


def _ensure_bridge_dirs() -> None:
    _requests_dir().mkdir(parents=True, exist_ok=True)
    _responses_dir().mkdir(parents=True, exist_ok=True)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def read_runtime_bridge_heartbeat() -> Dict[str, Any]:
    heartbeat_path = _heartbeat_path()
    heartbeat = _read_json(heartbeat_path)
    if not heartbeat:
        return {"connected": False, "reason": "heartbeat_missing"}

    timestamp = heartbeat.get("timestamp")
    try:
        ts = float(timestamp)
    except Exception:
        ts = 0.0

    age = max(0.0, time.time() - ts)
    max_age = max(0.5, HEARTBEAT_MAX_AGE_SEC)
    mtime_fresh = False
    mtime_age = None
    try:
        mtime_age = max(0.0, time.time() - float(heartbeat_path.stat().st_mtime))
        mtime_fresh = mtime_age <= max_age
    except Exception:
        mtime_fresh = False

    timestamp_fresh = bool(ts > 0.0 and age <= max_age)
    connected = bool(heartbeat.get("ok")) and bool(timestamp_fresh or mtime_fresh)
    payload: Dict[str, Any] = {
        "connected": connected,
        "age_sec": round(age, 3),
        "max_age_sec": max_age,
        "runtime": str(heartbeat.get("runtime") or "unknown"),
        "timestamp": ts,
        "pid": heartbeat.get("pid"),
        "timestamp_fresh": timestamp_fresh,
        "mtime_fresh": mtime_fresh,
    }
    if mtime_age is not None:
        payload["mtime_age_sec"] = round(mtime_age, 3)
    if not connected and bool(heartbeat.get("ok")):
        payload["reason"] = "heartbeat_stale"
    elif not connected:
        payload["reason"] = str(heartbeat.get("reason") or "runtime_unavailable")
    return payload


def is_runtime_bridge_connected() -> bool:
    return bool(read_runtime_bridge_heartbeat().get("connected"))


def call_runtime_bridge(
    method: str,
    params: Dict[str, Any] | None = None,
    timeout_sec: float | None = None,
    request_id: str | None = None,
) -> Dict[str, Any]:
    method_name = str(method or "").strip()
    if not method_name:
        return {"ok": False, "error": "invalid_request", "message": "method is required"}

    _ensure_bridge_dirs()
    bridge_request_id = uuid4().hex
    trace_request_id = str(request_id or "").strip() or bridge_request_id
    request_file = _requests_dir() / f"{bridge_request_id}.json"
    request_tmp = _requests_dir() / f".{bridge_request_id}.tmp"
    response_file = _responses_dir() / f"{bridge_request_id}.json"

    payload = {
        "id": bridge_request_id,
        "trace_request_id": trace_request_id,
        "method": method_name,
        "params": params if isinstance(params, dict) else {},
        "timestamp": time.time(),
    }

    request_tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    request_tmp.replace(request_file)

    LOGGER.info(
        "RUNTIME_BRIDGE_CALL request_id=%s method=%s bridge_request_id=%s",
        trace_request_id,
        method_name,
        bridge_request_id,
    )

    deadline = time.time() + max(0.25, float(timeout_sec or REQUEST_TIMEOUT_SEC))
    while time.time() < deadline:
        if response_file.exists():
            response = _read_json(response_file)
            try:
                response_file.unlink(missing_ok=True)
            except Exception:
                pass
            try:
                request_file.unlink(missing_ok=True)
            except Exception:
                pass
            if not response:
                return {
                    "ok": False,
                    "error": "invalid_response",
                    "message": "Runtime bridge returned empty or invalid payload.",
                }
            LOGGER.info(
                "Runtime bridge response received: request_id=%s method=%s bridge_request_id=%s ok=%s error=%s",
                trace_request_id,
                method_name,
                bridge_request_id,
                bool(response.get("ok")),
                response.get("error"),
            )
            return response
        time.sleep(max(0.01, REQUEST_POLL_INTERVAL_SEC))

    LOGGER.warning(
        "Runtime bridge timed out: request_id=%s method=%s bridge_request_id=%s timeout_sec=%.2f",
        trace_request_id,
        method_name,
        bridge_request_id,
        max(0.25, float(timeout_sec or REQUEST_TIMEOUT_SEC)),
    )
    return {
        "ok": False,
        "error": "unreal_runtime_unavailable",
        "message": "Unreal runtime bridge did not respond. Ensure Unreal Editor is running with AIRD loaded.",
    }
