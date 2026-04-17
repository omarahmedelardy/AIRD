from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from game_thread import run_on_game_thread_sync
from runtime_config import load_runtime_config
from run_utils import bridge_call, try_import_unreal
from unreal_runtime_bridge_client import call_runtime_bridge, is_runtime_bridge_connected

LOGGER = logging.getLogger("aird.scene")

REMOTE_CONTROL_OBJECT_CALL_PATH = "/remote/object/call"


def _normalize_remote_control_base_url(raw_url: str) -> str:
    normalized = str(raw_url or "").strip().rstrip("/")
    suffix = REMOTE_CONTROL_OBJECT_CALL_PATH
    if normalized.lower().endswith(suffix):
        normalized = normalized[: -len(suffix)].rstrip("/")
    return normalized


REMOTE_CONTROL_TIMEOUT_SEC = float(
    os.getenv("AIRD_REMOTE_CONTROL_TIMEOUT", "2.5") or "2.5"
)
REMOTE_CONTROL_MAX_ACTORS = max(
    1, int(os.getenv("AIRD_REMOTE_CONTROL_MAX_ACTORS", "250") or "250")
)

SCENE_SOURCE_UNAVAILABLE = {"", "unavailable", "pending", "pending_game_thread", "empty_json"}


def _trace_entry(
    order: int,
    source: str,
    role: str,
    status: str,
    reason: str,
    actor_count: Optional[int] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "order": int(order),
        "source": str(source or "").strip() or "unknown",
        "role": str(role or "").strip() or "fallback",
        "status": str(status or "").strip() or "fallback",
        "reason": str(reason or "").strip() or "unspecified",
    }
    if actor_count is not None:
        payload["actor_count"] = int(actor_count)
    return payload


def _scene_is_valid(scene: Dict[str, Any]) -> bool:
    if not isinstance(scene, dict):
        return False
    source = str(scene.get("source") or "").strip().lower()
    actors = scene.get("actors")
    if source in SCENE_SOURCE_UNAVAILABLE:
        return False
    return isinstance(actors, list)


def _editor_world_context_status() -> tuple[bool, str]:
    unreal = try_import_unreal()
    if unreal is None:
        return False, "unreal module unavailable"
    editor_level_library = getattr(unreal, "EditorLevelLibrary", None)
    get_editor_world = getattr(editor_level_library, "get_editor_world", None)
    if not callable(get_editor_world):
        return False, "EditorLevelLibrary.get_editor_world unavailable"
    try:
        world = get_editor_world()
    except Exception as exc:
        return False, f"get_editor_world failed: {exc}"
    if world is None:
        return False, "editor world is None"
    return True, "editor world resolved"


def _remote_control_candidate_urls() -> list[str]:
    cfg = load_runtime_config()
    primary = _normalize_remote_control_base_url(
        f"http://127.0.0.1:{cfg.get('remote_control_http_port', 30010)}"
    )
    legacy = _normalize_remote_control_base_url(
        f"http://127.0.0.1:{cfg.get('legacy_port', 30000)}"
    )
    if primary == legacy:
        return [primary]
    return [primary, legacy]


def _rc_post(
    path: str, payload: Dict[str, Any], timeout: float = REMOTE_CONTROL_TIMEOUT_SEC
) -> Dict[str, Any]:
    candidates = _remote_control_candidate_urls()
    if not candidates:
        raise RuntimeError("Remote Control URL is empty")

    target_path = (path or "").strip()
    last_error: Exception | None = None

    for base_url in candidates:
        if not target_path:
            target_url = f"{base_url}{REMOTE_CONTROL_OBJECT_CALL_PATH}"
        elif target_path.startswith("http://") or target_path.startswith("https://"):
            target_url = target_path.rstrip("/")
        elif target_path.startswith("/"):
            target_url = f"{base_url}{target_path}"
        else:
            target_url = f"{base_url}/{target_path}"

        request = urllib.request.Request(
            target_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="PUT",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=max(0.2, float(timeout))
            ) as response:
                raw = response.read().decode("utf-8")
                data = json.loads(raw) if raw else {}
                if isinstance(data, dict):
                    return data
                return {"ReturnValue": data}
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="ignore")
            last_error = RuntimeError(
                f"Remote Control HTTP {exc.code}: {details[:400]}"
            )
        except Exception as exc:
            last_error = RuntimeError(f"Remote Control request failed: {exc}")

    if last_error is not None:
        raise last_error
    raise RuntimeError("Remote Control request failed: unknown error")


def _rc_object_call(
    object_path: str, function_name: str, parameters: Dict[str, Any] | None = None
) -> Any:
    payload: Dict[str, Any] = {
        "objectPath": object_path,
        "functionName": function_name,
        "parameters": parameters or {},
        "generateTransaction": False,
    }
    response = _rc_post(REMOTE_CONTROL_OBJECT_CALL_PATH, payload)
    return response.get("ReturnValue")


def _extract_actor_path(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("ObjectPath", "objectPath", "PathName", "pathName"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _call0(obj: Any, method_name: str) -> Any:
    fn = getattr(obj, method_name, None)
    if callable(fn):
        try:
            return fn()
        except Exception:
            return None
    return None


def _get_attr_value(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name)
    except Exception:
        return None


def _vector_to_dict(value: Any) -> Dict[str, float]:
    return {
        "x": _to_float(_get_attr_value(value, "x"), 0.0),
        "y": _to_float(_get_attr_value(value, "y"), 0.0),
        "z": _to_float(_get_attr_value(value, "z"), 0.0),
    }


def _snapshot_unreal_actor(actor: Any) -> Dict[str, Any]:
    actor_path = (
        str(_call0(actor, "get_path_name") or "").strip()
        or str(_call0(actor, "get_name") or "").strip()
    )
    actor_name = (
        str(_call0(actor, "get_actor_label") or "").strip()
        or str(_call0(actor, "get_name") or "").strip()
        or actor_path.split(".")[-1]
    )
    location = _vector_to_dict(_call0(actor, "get_actor_location"))

    actor_class = ""
    class_obj = _call0(actor, "get_class")
    if class_obj is not None:
        actor_class = (
            str(_call0(class_obj, "get_path_name") or "").strip()
            or str(_call0(class_obj, "get_name") or "").strip()
        )

    return {
        "name": actor_name,
        "class": actor_class,
        "path": actor_path,
        "location": location,
    }


def _get_scene_context_via_unreal() -> Optional[Dict[str, Any]]:
    unreal = try_import_unreal()
    if unreal is None:
        return None

    subsystem_getter = getattr(unreal, "get_editor_subsystem", None)
    subsystem_class = getattr(unreal, "EditorActorSubsystem", None)
    if not callable(subsystem_getter) or subsystem_class is None:
        return None

    def _collect_scene() -> Dict[str, Any]:
        actor_subsystem = subsystem_getter(subsystem_class)
        if actor_subsystem is None:
            raise RuntimeError("unreal.EditorActorSubsystem is unavailable")

        get_all_level_actors = getattr(actor_subsystem, "get_all_level_actors", None)
        if not callable(get_all_level_actors):
            raise RuntimeError(
                "EditorActorSubsystem.get_all_level_actors is unavailable"
            )

        raw_actors = get_all_level_actors() or []
        actors: List[Dict[str, Any]] = []
        for actor in raw_actors[:REMOTE_CONTROL_MAX_ACTORS]:
            snapshot = _snapshot_unreal_actor(actor)
            if snapshot.get("path") or snapshot.get("name"):
                actors.append(snapshot)

        scene = {
            "actors": actors,
            "source": "unreal_editor_actor_subsystem",
            "count": len(actors),
        }
        _enrich_scene_data(scene)
        return scene

    status, scene = run_on_game_thread_sync(_collect_scene, max_wait=0.25)
    world_ok, world_reason = _editor_world_context_status()
    LOGGER.info(
        "[AIRD] scene source probe: EditorActorSubsystem world_context_valid=%s reason=%s",
        world_ok,
        world_reason,
    )
    if status == "pending":
        LOGGER.info("[AIRD] get_scene_context: Unreal EditorActorSubsystem pending")
        return {"actors": [], "source": "pending_game_thread", "count": 0}
    if isinstance(scene, dict):
        LOGGER.info(
            "[AIRD] scene source result: EditorActorSubsystem actor_count=%s",
            int(scene.get("count") or 0),
        )
    return scene if isinstance(scene, dict) else None


def _get_scene_context_via_editor_level_library() -> Optional[Dict[str, Any]]:
    unreal = try_import_unreal()
    if unreal is None:
        return None

    editor_level_library = getattr(unreal, "EditorLevelLibrary", None)
    get_all_level_actors = getattr(editor_level_library, "get_all_level_actors", None)
    if not callable(get_all_level_actors):
        return None

    def _collect_scene() -> Dict[str, Any]:
        raw_actors = get_all_level_actors() or []
        actors: List[Dict[str, Any]] = []
        for actor in raw_actors[:REMOTE_CONTROL_MAX_ACTORS]:
            snapshot = _snapshot_unreal_actor(actor)
            if snapshot.get("path") or snapshot.get("name"):
                actors.append(snapshot)

        scene = {
            "actors": actors,
            "source": "unreal_editor_level_library",
            "count": len(actors),
        }
        _enrich_scene_data(scene)
        return scene

    status, scene = run_on_game_thread_sync(_collect_scene, max_wait=0.25)
    world_ok, world_reason = _editor_world_context_status()
    LOGGER.info(
        "[AIRD] scene source probe: EditorLevelLibrary world_context_valid=%s reason=%s",
        world_ok,
        world_reason,
    )
    if status == "pending":
        LOGGER.info("[AIRD] get_scene_context: Unreal EditorLevelLibrary pending")
        return {"actors": [], "source": "pending_game_thread", "count": 0}
    if isinstance(scene, dict):
        LOGGER.info(
            "[AIRD] scene source result: EditorLevelLibrary actor_count=%s",
            int(scene.get("count") or 0),
        )
    return scene if isinstance(scene, dict) else None


def _get_scene_context_via_runtime_bridge() -> Optional[Dict[str, Any]]:
    try:
        if not is_runtime_bridge_connected():
            return None
    except Exception:
        return None

    response = call_runtime_bridge("get_scene_context", {}, timeout_sec=3.0)
    if not isinstance(response, dict):
        return {
            "actors": [],
            "source": "runtime_bridge_unavailable",
            "count": 0,
            "error": "invalid_runtime_bridge_response",
        }

    if not bool(response.get("ok")):
        LOGGER.info(
            "[AIRD] scene source result: runtime bridge unavailable error=%s",
            str(response.get("error") or response.get("message") or "runtime_bridge_error"),
        )
        return {
            "actors": [],
            "source": "runtime_bridge_unavailable",
            "count": 0,
            "error": str(response.get("error") or response.get("message") or "runtime_bridge_error"),
        }

    scene = response.get("scene") if isinstance(response.get("scene"), dict) else {}
    if not scene:
        return {
            "actors": [],
            "source": "runtime_bridge_unavailable",
            "count": 0,
            "error": "runtime_bridge_missing_scene_payload",
        }

    actors = scene.get("actors")
    if not isinstance(actors, list):
        actors = []
    source = str(scene.get("source") or "unreal_editor_actor_subsystem").strip()
    if source and not source.startswith("runtime_bridge_"):
        source = f"runtime_bridge_{source}"

    result: Dict[str, Any] = {
        "actors": actors,
        "source": source or "runtime_bridge_unavailable",
        "count": len(actors),
    }
    if isinstance(scene.get("source_trace"), list):
        result["runtime_editor_trace"] = scene.get("source_trace")
    _enrich_scene_data(result)
    LOGGER.info(
        "[AIRD] scene source result: runtime bridge actor_count=%s source=%s",
        int(result.get("count") or 0),
        str(result.get("source") or "runtime_bridge_unavailable"),
    )
    return result


def _rc_get_all_level_actors_editor_safe() -> list[Any]:
    """
    Editor-safe remote-control fallback.
    Avoid GameplayStatics.GetAllActorsOfClass because it can require a non-null world context.
    """
    candidates = [
        ("/Script/UnrealEd.Default__EditorActorSubsystem", "GetAllLevelActors"),
        ("/Script/UnrealEd.Default__EditorLevelLibrary", "GetAllLevelActors"),
    ]
    last_error: Exception | None = None
    for object_path, function_name in candidates:
        try:
            value = _rc_object_call(object_path, function_name, {})
            if isinstance(value, list):
                LOGGER.info(
                    "[AIRD] RC editor-safe source succeeded: %s.%s actor_refs=%s",
                    object_path,
                    function_name,
                    len(value),
                )
                return value
            LOGGER.info(
                "[AIRD] RC editor-safe source returned non-list: %s.%s type=%s",
                object_path,
                function_name,
                type(value).__name__,
            )
        except Exception as exc:
            last_error = exc
            LOGGER.info(
                "[AIRD] RC editor-safe source failed: %s.%s error=%s",
                object_path,
                function_name,
                exc,
            )
    if last_error is not None:
        raise RuntimeError(str(last_error))
    raise RuntimeError("Remote Control editor-safe actor sources returned no list payload.")


def _read_actor_snapshot(actor_path: str) -> Dict[str, Any]:
    if not actor_path:
        return {}

    name = ""
    actor_class = ""
    location: Dict[str, float] = {"x": 0.0, "y": 0.0, "z": 0.0}

    try:
        name = str(_rc_object_call(actor_path, "GetActorLabel") or "").strip()
    except Exception:
        pass

    try:
        class_info = _rc_object_call(actor_path, "GetClass")
        if isinstance(class_info, dict):
            actor_class = str(
                class_info.get("PathName") or class_info.get("Name") or ""
            ).strip()
        elif class_info is not None:
            actor_class = str(class_info).strip()
    except Exception:
        pass

    try:
        raw_loc = _rc_object_call(actor_path, "GetActorLocation")
        if isinstance(raw_loc, dict):
            location = {
                "x": _to_float(raw_loc.get("X", raw_loc.get("x", 0.0))),
                "y": _to_float(raw_loc.get("Y", raw_loc.get("y", 0.0))),
                "z": _to_float(raw_loc.get("Z", raw_loc.get("z", 0.0))),
            }
    except Exception:
        pass

    return {
        "name": name or actor_path.split(".")[-1],
        "class": actor_class,
        "path": actor_path,
        "location": location,
    }


def get_scene_context() -> Dict[str, Any]:
    """Editor-first source order: local subsystem -> local editor library -> runtime bridge -> Remote Control."""
    trace: List[Dict[str, Any]] = []

    local_scene = _get_scene_context_via_unreal()
    if isinstance(local_scene, dict):
        local_scene.setdefault("actors", [])
        local_scene.setdefault("source", "unknown")
        local_actor_count = (
            len(local_scene["actors"]) if isinstance(local_scene.get("actors"), list) else 0
        )
        if _scene_is_valid(local_scene):
            trace.append(
                _trace_entry(
                    1,
                    str(local_scene.get("source")),
                    "primary",
                    "success",
                    "Used Unreal Python editor API in current process.",
                    local_actor_count,
                )
            )
            local_scene["source_trace"] = trace
            LOGGER.info(
                "[AIRD] scene source chosen: %s actor_count=%s",
                str(local_scene.get("source") or "unknown"),
                local_actor_count,
            )
            return local_scene
        trace.append(
            _trace_entry(
                1,
                str(local_scene.get("source")),
                "primary",
                "fallback",
                "EditorActorSubsystem source unavailable; trying EditorLevelLibrary.",
                local_actor_count,
            )
        )
    else:
        trace.append(
            _trace_entry(
                1,
                "unreal_python_editor_api",
                "primary",
                "skipped",
                "Unreal Python is unavailable in this MCP process.",
            )
        )

    editor_level_scene = _get_scene_context_via_editor_level_library()
    if isinstance(editor_level_scene, dict):
        editor_level_scene.setdefault("actors", [])
        editor_level_scene.setdefault("source", "unknown")
        editor_level_actor_count = (
            len(editor_level_scene["actors"])
            if isinstance(editor_level_scene.get("actors"), list)
            else 0
        )
        if _scene_is_valid(editor_level_scene):
            trace.append(
                _trace_entry(
                    2,
                    str(editor_level_scene.get("source")),
                    "primary_fallback",
                    "success",
                    "Used Unreal Python EditorLevelLibrary in current process.",
                    editor_level_actor_count,
                )
            )
            editor_level_scene["source_trace"] = trace
            LOGGER.info(
                "[AIRD] scene source chosen: %s actor_count=%s",
                str(editor_level_scene.get("source") or "unknown"),
                editor_level_actor_count,
            )
            return editor_level_scene
        trace.append(
            _trace_entry(
                2,
                str(editor_level_scene.get("source")),
                "primary_fallback",
                "fallback",
                "EditorLevelLibrary source unavailable; trying runtime bridge.",
                editor_level_actor_count,
            )
        )
    else:
        trace.append(
            _trace_entry(
                2,
                "unreal_python_editor_level_library",
                "primary_fallback",
                "skipped",
                "EditorLevelLibrary is unavailable in this MCP process.",
            )
        )

    bridge_scene = _get_scene_context_via_runtime_bridge()
    if isinstance(bridge_scene, dict):
        bridge_scene.setdefault("actors", [])
        bridge_scene.setdefault("source", "runtime_bridge_unavailable")
        bridge_actor_count = (
            len(bridge_scene["actors"])
            if isinstance(bridge_scene.get("actors"), list)
            else 0
        )
        if _scene_is_valid(bridge_scene):
            trace.append(
                _trace_entry(
                    3,
                    str(bridge_scene.get("source")),
                    "primary_fallback",
                    "success",
                    "Used Unreal runtime bridge editor-native source.",
                    bridge_actor_count,
                )
            )
            bridge_scene["source_trace"] = trace
            LOGGER.info(
                "[AIRD] scene source chosen: %s actor_count=%s",
                str(bridge_scene.get("source") or "unknown"),
                bridge_actor_count,
            )
            return bridge_scene
        trace.append(
            _trace_entry(
                3,
                str(bridge_scene.get("source")),
                "primary_fallback",
                "fallback",
                str(
                    bridge_scene.get("error")
                    or "Runtime bridge scene payload unavailable; trying Remote Control."
                ),
                bridge_actor_count,
            )
        )
    else:
        trace.append(
            _trace_entry(
                3,
                "runtime_bridge_editor_api",
                "primary_fallback",
                "skipped",
                "Runtime bridge is disconnected.",
            )
        )

    try:
        candidate_urls = _remote_control_candidate_urls()
        LOGGER.info(
            "[AIRD] get_scene_context: remote control fallback %s%s",
            candidate_urls[0] if candidate_urls else "N/A",
            REMOTE_CONTROL_OBJECT_CALL_PATH,
        )
        raw_actors = _rc_get_all_level_actors_editor_safe()
        if not isinstance(raw_actors, list):
            raw_actors = []

        actors: List[Dict[str, Any]] = []
        for item in raw_actors[:REMOTE_CONTROL_MAX_ACTORS]:
            actor_path = _extract_actor_path(item)
            if not actor_path:
                continue
            snapshot = _read_actor_snapshot(actor_path)
            if snapshot:
                actors.append(snapshot)

        scene = {
            "actors": actors,
            "source": "remote_control_api",
            "count": len(actors),
        }
        _enrich_scene_data(scene)
        trace.append(
            _trace_entry(
                4,
                "remote_control_api",
                "fallback",
                "success",
                "Remote Control fallback completed.",
                len(actors),
            )
        )
        scene["source_trace"] = trace
        LOGGER.info(
            "[AIRD] scene source chosen: remote_control_api actor_count=%s",
            len(actors),
        )
        return scene
    except Exception as exc:
        LOGGER.warning("[AIRD] get_scene_context via Remote Control failed: %s", exc)
        trace.append(
            _trace_entry(
                4,
                "remote_control_api",
                "fallback",
                "failed",
                str(exc),
                0,
            )
        )
        LOGGER.info("[AIRD] scene source chosen: unavailable actor_count=0")
        return {"actors": [], "source": "unavailable", "count": 0, "source_trace": trace}


def _enrich_scene_data(scene: Dict[str, Any]) -> None:
    actors = scene.get("actors")
    if not isinstance(actors, list):
        actors = []
        scene["actors"] = actors

    lights: List[Dict[str, Any]] = []
    cameras: List[Dict[str, Any]] = []
    static_meshes = 0

    for actor in actors:
        if not isinstance(actor, dict):
            continue
        actor_name = str(actor.get("name") or "")
        actor_class = str(actor.get("class") or actor.get("type") or "")
        kind = f"{actor_name} {actor_class}".lower()
        if "light" in kind:
            lights.append(actor)
        if "camera" in kind or "cinecamera" in kind:
            cameras.append(actor)
        if "staticmesh" in kind:
            static_meshes += 1

    scene["lights"] = lights
    scene["camera"] = cameras[0] if cameras else {}
    scene["cameras"] = cameras
    scene["stats"] = {
        "actors": len(actors),
        "lights": len(lights),
        "static_meshes": static_meshes,
    }
    scene["updated_at"] = datetime.now(timezone.utc).isoformat()


def capture_viewport_base64() -> str:
    """Viewport capture must run on the game thread like other AIRDBridge calls."""

    def _capture() -> Any:
        return bridge_call(["capture_viewport_screenshot", "CaptureViewportScreenshot"])

    try:
        status, value = run_on_game_thread_sync(_capture, max_wait=0.25)
    except Exception as exc:
        LOGGER.warning("[AIRD] capture_viewport_base64 failed: %s", exc)
        return ""

    if status == "pending":
        return ""

    if isinstance(value, tuple) and len(value) == 2:
        ok, payload = value
        if ok:
            return str(payload or "")
        return ""
    if isinstance(value, str):
        return value
    return ""
