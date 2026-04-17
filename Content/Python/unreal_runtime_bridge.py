from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List

from run_utils import bridge_call, try_import_unreal

LOGGER = logging.getLogger("aird.runtime_bridge.worker")

HEARTBEAT_INTERVAL_SEC = float(
    os.getenv("AIRD_RUNTIME_BRIDGE_HEARTBEAT_INTERVAL", "1.0") or "1.0"
)
MAX_REQUESTS_PER_TICK = max(
    1, int(os.getenv("AIRD_RUNTIME_BRIDGE_MAX_REQUESTS_PER_TICK", "4") or "4")
)

_STARTED = False
_TICK_HANDLE: Any = None
_LAST_HEARTBEAT_AT = 0.0
_DIRS_LOGGED = False
_WORKER_LOOP_LOGGED = False
_HEARTBEAT_LOGGED = False


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


def get_runtime_bridge_root_path() -> str:
    return str(_bridge_root().resolve())


def _ensure_bridge_dirs() -> None:
    global _DIRS_LOGGED
    _requests_dir().mkdir(parents=True, exist_ok=True)
    _responses_dir().mkdir(parents=True, exist_ok=True)
    if not _DIRS_LOGGED:
        _DIRS_LOGGED = True
        _log_runtime(f"runtime bridge root path = {str(_bridge_root().resolve())}")
        _log_runtime(f"requests path = {str(_requests_dir().resolve())}")
        _log_runtime(f"responses path = {str(_responses_dir().resolve())}")


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _log_runtime(message: str) -> None:
    unreal = try_import_unreal()
    if unreal is None:
        LOGGER.info(message)
        return
    try:
        unreal.log(f"[AIRD Runtime Bridge] {message}")
    except Exception:
        LOGGER.info(message)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _last_bridge_error() -> str:
    try:
        code = bridge_call(["get_last_blueprint_edit_error", "GetLastBlueprintEditError"])
    except Exception:
        return "operation_failed"
    text = str(code or "").strip().lower()
    if not text or text == "none":
        return "operation_failed"
    return text


def _execute_add_blueprint_variable(params: Dict[str, Any]) -> Dict[str, Any]:
    blueprint_path = str(params.get("blueprint_path") or "").strip()
    variable_name = str(params.get("variable_name") or "").strip()
    variable_type = str(params.get("variable_type") or "float").strip().lower() or "float"
    if not blueprint_path or not variable_name:
        return {
            "ok": False,
            "error": "invalid_request",
            "message": "blueprint_path and variable_name are required.",
        }

    ok = bool(
        bridge_call(
            ["add_blueprint_variable", "AddBlueprintVariable"],
            blueprint_path,
            variable_name,
            variable_type,
        )
    )
    if ok:
        return {
            "ok": True,
            "message": f"Variable '{variable_name}' ({variable_type}) added to {blueprint_path}.",
        }
    return {
        "ok": False,
        "error": _last_bridge_error(),
        "message": f"AddBlueprintVariable failed for {blueprint_path}.",
    }


def _execute_add_blueprint_function(params: Dict[str, Any]) -> Dict[str, Any]:
    blueprint_path = str(params.get("blueprint_path") or "").strip()
    function_name = str(params.get("function_name") or "").strip()
    if not blueprint_path or not function_name:
        return {
            "ok": False,
            "error": "invalid_request",
            "message": "blueprint_path and function_name are required.",
        }

    ok = bool(
        bridge_call(
            ["add_blueprint_function", "AddBlueprintFunction"],
            blueprint_path,
            function_name,
        )
    )
    if ok:
        return {
            "ok": True,
            "message": f"Function '{function_name}' added to {blueprint_path}.",
        }
    return {
        "ok": False,
        "error": _last_bridge_error(),
        "message": f"AddBlueprintFunction failed for {blueprint_path}.",
    }


def _execute_generate_blueprint(params: Dict[str, Any]) -> Dict[str, Any]:
    prompt = str(params.get("prompt") or "AIRD Generated Actor").strip()
    ok = bool(
        bridge_call(
            ["generate_blueprint_from_prompt", "GenerateBlueprintFromPrompt"],
            prompt,
        )
    )
    if ok:
        return {"ok": True, "message": "Blueprint generated in Unreal runtime."}
    return {
        "ok": False,
        "error": _last_bridge_error(),
        "message": "GenerateBlueprintFromPrompt failed in Unreal runtime.",
    }


def _is_valid_game_path(path: str) -> bool:
    candidate = str(path or "").strip().replace("\\", "/")
    if not candidate.startswith("/Game"):
        return False
    if ".." in candidate:
        return False
    return True


def _execute_create_content_folder(params: Dict[str, Any]) -> Dict[str, Any]:
    unreal = try_import_unreal()
    if unreal is None:
        return {
            "ok": False,
            "error": "unreal_runtime_unavailable",
            "message": "Unreal Python runtime is unavailable.",
        }
    folder_path = str(params.get("folder_path") or "").strip()
    if not folder_path:
        return {
            "ok": False,
            "error": "validation_failure",
            "message": "folder_path is required.",
        }
    if not _is_valid_game_path(folder_path):
        return {
            "ok": False,
            "error": "validation_failure",
            "message": f"Invalid content path: {folder_path}",
        }

    editor_asset_library = getattr(unreal, "EditorAssetLibrary", None)
    if editor_asset_library is None:
        return {
            "ok": False,
            "error": "asset_tools_unavailable",
            "message": "EditorAssetLibrary is unavailable in this Unreal runtime.",
        }

    dir_exists_fn = getattr(editor_asset_library, "does_directory_exist", None)
    if callable(dir_exists_fn):
        try:
            if bool(dir_exists_fn(folder_path)):
                return {"ok": True, "message": f"Folder already exists: {folder_path}"}
        except Exception:
            pass

    make_dir_fn = getattr(editor_asset_library, "make_directory", None)
    if not callable(make_dir_fn):
        return {
            "ok": False,
            "error": "asset_tools_unavailable",
            "message": "EditorAssetLibrary.make_directory is unavailable.",
        }

    try:
        ok = bool(make_dir_fn(folder_path))
    except Exception as exc:
        return {
            "ok": False,
            "error": "execution_failure",
            "message": f"Failed to create folder: {exc}",
        }
    if not ok:
        return {
            "ok": False,
            "error": "execution_failure",
            "message": f"Failed to create folder: {folder_path}",
        }
    return {"ok": True, "message": f"Folder created: {folder_path}"}


def _execute_create_content_asset_placeholder(params: Dict[str, Any]) -> Dict[str, Any]:
    unreal = try_import_unreal()
    if unreal is None:
        return {
            "ok": False,
            "error": "unreal_runtime_unavailable",
            "message": "Unreal Python runtime is unavailable.",
        }
    editor_asset_library = getattr(unreal, "EditorAssetLibrary", None)
    if editor_asset_library is None:
        return {
            "ok": False,
            "error": "asset_tools_unavailable",
            "message": "Asset tools are unavailable in this runtime.",
        }
    return {
        "ok": False,
        "error": "unsupported_content_operation",
        "message": "create asset/file placeholder is not supported yet; provide a concrete Unreal asset type workflow.",
    }


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _vector_to_dict(value: Any) -> Dict[str, float]:
    if value is None:
        return {"x": 0.0, "y": 0.0, "z": 0.0}
    return {
        "x": _to_float(getattr(value, "x", 0.0)),
        "y": _to_float(getattr(value, "y", 0.0)),
        "z": _to_float(getattr(value, "z", 0.0)),
    }


def _snapshot_actor(actor: Any) -> Dict[str, Any]:
    if actor is None:
        return {}
    path = ""
    name = ""
    actor_class = ""
    location = {"x": 0.0, "y": 0.0, "z": 0.0}

    try:
        path = str(actor.get_path_name() or "").strip()
    except Exception:
        path = ""
    try:
        name = str(actor.get_actor_label() or "").strip() or str(actor.get_name() or "").strip()
    except Exception:
        name = str(path.split(".")[-1] if path else "").strip()
    try:
        class_obj = actor.get_class()
        if class_obj is not None:
            actor_class = str(class_obj.get_path_name() or class_obj.get_name() or "").strip()
    except Exception:
        actor_class = ""
    try:
        location = _vector_to_dict(actor.get_actor_location())
    except Exception:
        location = {"x": 0.0, "y": 0.0, "z": 0.0}

    return {"name": name or path.split(".")[-1], "class": actor_class, "path": path, "location": location}


def _build_scene_from_raw_actors(raw_actors: Any, source: str) -> Dict[str, Any]:
    actors: List[Dict[str, Any]] = []
    if isinstance(raw_actors, list):
        for actor in raw_actors[:250]:
            snapshot = _snapshot_actor(actor)
            if snapshot.get("path") or snapshot.get("name"):
                actors.append(snapshot)
    return {"actors": actors, "source": source, "count": len(actors)}


def _execute_get_scene_context(_: Dict[str, Any]) -> Dict[str, Any]:
    unreal = try_import_unreal()
    if unreal is None:
        return {
            "ok": False,
            "error": "unreal_python_unavailable",
            "message": "Unreal Python is unavailable in runtime bridge worker.",
            "scene": {"actors": [], "source": "runtime_bridge_unavailable", "count": 0},
        }

    trace: List[Dict[str, Any]] = []

    try:
        subsystem_getter = getattr(unreal, "get_editor_subsystem", None)
        subsystem_class = getattr(unreal, "EditorActorSubsystem", None)
        if callable(subsystem_getter) and subsystem_class is not None:
            subsystem = subsystem_getter(subsystem_class)
            get_all_level_actors = getattr(subsystem, "get_all_level_actors", None)
            if callable(get_all_level_actors):
                scene = _build_scene_from_raw_actors(
                    get_all_level_actors() or [],
                    "runtime_bridge_editor_actor_subsystem",
                )
                trace.append(
                    {
                        "order": 1,
                        "source": "runtime_bridge_editor_actor_subsystem",
                        "status": "success",
                        "reason": "EditorActorSubsystem.get_all_level_actors succeeded.",
                        "actor_count": int(scene.get("count") or 0),
                    }
                )
                scene["source_trace"] = trace
                return {"ok": True, "scene": scene}
            trace.append(
                {
                    "order": 1,
                    "source": "runtime_bridge_editor_actor_subsystem",
                    "status": "fallback",
                    "reason": "EditorActorSubsystem.get_all_level_actors is unavailable.",
                }
            )
        else:
            trace.append(
                {
                    "order": 1,
                    "source": "runtime_bridge_editor_actor_subsystem",
                    "status": "skipped",
                    "reason": "EditorActorSubsystem is not exposed.",
                }
            )
    except Exception as exc:
        trace.append(
            {
                "order": 1,
                "source": "runtime_bridge_editor_actor_subsystem",
                "status": "fallback",
                "reason": str(exc),
            }
        )

    try:
        editor_level_library = getattr(unreal, "EditorLevelLibrary", None)
        get_all_level_actors = getattr(editor_level_library, "get_all_level_actors", None)
        if callable(get_all_level_actors):
            scene = _build_scene_from_raw_actors(
                get_all_level_actors() or [],
                "runtime_bridge_editor_level_library",
            )
            trace.append(
                {
                    "order": 2,
                    "source": "runtime_bridge_editor_level_library",
                    "status": "success",
                    "reason": "EditorLevelLibrary.get_all_level_actors succeeded.",
                    "actor_count": int(scene.get("count") or 0),
                }
            )
            scene["source_trace"] = trace
            return {"ok": True, "scene": scene}
        trace.append(
            {
                "order": 2,
                "source": "runtime_bridge_editor_level_library",
                "status": "failed",
                "reason": "EditorLevelLibrary.get_all_level_actors is unavailable.",
            }
        )
    except Exception as exc:
        trace.append(
            {
                "order": 2,
                "source": "runtime_bridge_editor_level_library",
                "status": "failed",
                "reason": str(exc),
            }
        )

    return {
        "ok": False,
        "error": "scene_context_unavailable",
        "message": "Editor-native scene APIs are unavailable in Unreal runtime.",
        "scene": {
            "actors": [],
            "source": "runtime_bridge_unavailable",
            "count": 0,
            "source_trace": trace,
        },
    }


def _handle_request(request: Dict[str, Any]) -> Dict[str, Any]:
    method = str(request.get("method") or "").strip().lower()
    params = request.get("params")
    if not isinstance(params, dict):
        params = {}

    if method == "ping":
        return {"ok": True, "message": "runtime bridge pong"}
    if method == "add_blueprint_variable":
        return _execute_add_blueprint_variable(params)
    if method == "add_blueprint_function":
        return _execute_add_blueprint_function(params)
    if method == "generate_blueprint_from_prompt":
        return _execute_generate_blueprint(params)
    if method == "create_content_folder":
        return _execute_create_content_folder(params)
    if method == "create_content_asset_placeholder":
        return _execute_create_content_asset_placeholder(params)
    if method == "get_scene_context":
        return _execute_get_scene_context(params)
    return {
        "ok": False,
        "error": "unsupported_method",
        "message": f"Unsupported runtime bridge method: {method}",
    }


def _write_heartbeat(force: bool = False) -> None:
    global _LAST_HEARTBEAT_AT, _HEARTBEAT_LOGGED
    now = time.time()
    if not force and (now - _LAST_HEARTBEAT_AT) < max(0.1, HEARTBEAT_INTERVAL_SEC):
        return

    _LAST_HEARTBEAT_AT = now
    payload = {
        "ok": True,
        "runtime": "unreal_editor",
        "timestamp": now,
        "pid": os.getpid(),
    }
    try:
        _atomic_write_json(_heartbeat_path(), payload)
        if not _HEARTBEAT_LOGGED:
            _HEARTBEAT_LOGGED = True
            _log_runtime(f"heartbeat written to {str(_heartbeat_path().resolve())}")
    except Exception as exc:
        _log_runtime(f"failed to write heartbeat: {exc}")


def _process_single_request(path: Path) -> None:
    request = _read_json(path)
    request_id = str(request.get("id") or path.stem).strip() or path.stem
    trace_request_id = str(request.get("trace_request_id") or request_id).strip() or request_id
    method = str(request.get("method") or "").strip().lower()
    _log_runtime(
        f"UNREAL_EXECUTION request_id={trace_request_id} bridge_request_id={request_id} method={method}"
    )

    if not request:
        response = {
            "id": request_id,
            "ok": False,
            "error": "invalid_request",
            "message": "request payload is invalid JSON object.",
        }
    else:
        try:
            _log_runtime(
                f"forwarded to bridge request_id={trace_request_id} bridge_request_id={request_id} method={method}"
            )
            result = _handle_request(request)
            response = {"id": request_id, **result}
            if bool(result.get("ok")):
                _log_runtime(
                    f"bridge success request_id={trace_request_id} bridge_request_id={request_id} method={method}"
                )
            else:
                _log_runtime(
                    "bridge failure request_id=%s bridge_request_id=%s method=%s error=%s"
                    % (trace_request_id, request_id, method, result.get("error"))
                )
        except Exception as exc:
            response = {
                "id": request_id,
                "ok": False,
                "error": "execution_failed",
                "message": str(exc),
            }
            _log_runtime(
                "bridge exception request_id=%s bridge_request_id=%s method=%s error=%s"
                % (trace_request_id, request_id, method, str(exc))
            )

    response_path = _responses_dir() / f"{request_id}.json"
    try:
        _atomic_write_json(response_path, response)
    finally:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def _tick(_: float) -> None:
    global _WORKER_LOOP_LOGGED
    _ensure_bridge_dirs()
    if not _WORKER_LOOP_LOGGED:
        _WORKER_LOOP_LOGGED = True
        _log_runtime("worker loop started")
    _write_heartbeat()

    request_files = sorted(
        _requests_dir().glob("*.json"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
    )
    for path in request_files[:MAX_REQUESTS_PER_TICK]:
        _process_single_request(path)


def start_runtime_bridge() -> bool:
    global _STARTED, _TICK_HANDLE
    _log_runtime("runtime bridge bootstrap started")
    _ensure_bridge_dirs()

    if _STARTED:
        _log_runtime("runtime bridge bootstrap skipped: worker already started")
        return False

    unreal = try_import_unreal()
    if unreal is None:
        _log_runtime("runtime bridge bootstrap failed: import unreal is unavailable")
        return False

    register = getattr(unreal, "register_slate_post_tick_callback", None)
    if not callable(register):
        register = getattr(unreal, "register_slate_pre_tick_callback", None)
    if not callable(register):
        _log_runtime("runtime bridge unavailable: no slate tick callback API")
        return False

    _log_runtime("starting unreal runtime worker")
    _TICK_HANDLE = register(_tick)
    _STARTED = True
    _write_heartbeat(force=True)
    _log_runtime("runtime bridge started successfully")
    return True


def is_runtime_bridge_running() -> bool:
    return bool(_STARTED)
