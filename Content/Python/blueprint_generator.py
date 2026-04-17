from __future__ import annotations

import logging
import re
from typing import Dict

from run_utils import bridge_call, try_import_unreal
from unreal_runtime_bridge_client import call_runtime_bridge

LOGGER = logging.getLogger("aird.mcp")


def _slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_]+", "_", text.strip())
    text = re.sub(r"_+", "_", text)
    return text.strip("_") or "AIRDAsset"


def _normalize_blueprint_path(path: str) -> str:
    cleaned = str(path or "").strip().strip("\"'")
    cleaned = re.sub(r"^blueprint\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+blueprint$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _get_last_blueprint_edit_error() -> str:
    try:
        code = bridge_call(
            ["get_last_blueprint_edit_error", "GetLastBlueprintEditError"]
        )
        text = str(code or "").strip().lower()
        if text and text != "none":
            return text
    except Exception:
        pass
    return "unsupported"


def _build_blueprint_bridge_error(action: str, blueprint_path: str) -> Dict[str, str]:
    error_code = _get_last_blueprint_edit_error()
    known_messages = {
        "editor_only": "Blueprint editing is editor-only in this runtime.",
        "unsupported": "Blueprint editing is unsupported in this runtime or Unreal API context.",
        "invalid_blueprint_path": f"Blueprint path is invalid or not found: {blueprint_path}",
        "duplicate_name": f"{action} name already exists in blueprint: {blueprint_path}",
        "invalid_name": f"{action} name is invalid for Blueprint identifiers.",
        "compile_failed": f"Blueprint compile failed after {action.lower()} operation.",
        "operation_failed": f"{action} operation did not persist in target blueprint.",
    }
    message = known_messages.get(
        error_code,
        f"{action} failed via AIRDBridge (error={error_code}).",
    )
    return {"status": "error", "error": error_code, "message": message}


def generate_blueprint(prompt: str, request_id: str | None = None) -> Dict[str, str]:
    # Prefer bridge path so C++ KismetCompiler workflow is used.
    try:
        ok = bridge_call(["generate_blueprint_from_prompt", "GenerateBlueprintFromPrompt"], prompt)
        if bool(ok):
            return {"status": "ok", "message": "Blueprint generated via AIRDBridge."}
    except Exception:
        response = call_runtime_bridge(
            "generate_blueprint_from_prompt",
            {"prompt": str(prompt or "AIRD Generated Actor")},
            request_id=request_id,
        )
        if bool(response.get("ok")):
            return {"status": "ok", "message": str(response.get("message") or "Blueprint generated in Unreal runtime.")}
        return {
            "status": "error",
            "error": str(response.get("error") or "unreal_runtime_unavailable"),
            "message": str(
                response.get("message")
                or "Unreal runtime is unavailable for blueprint generation."
            ),
        }

    unreal = try_import_unreal()
    if unreal is None:
        return {"status": "error", "message": "Unreal Python is not available."}

    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    package_path = "/Game/AIRD"
    asset_name = f"BP_{_slugify(prompt)[:42]}"

    try:
        factory = unreal.BlueprintFactory()
        factory.set_editor_property("ParentClass", unreal.Actor)
        bp = asset_tools.create_asset(asset_name, package_path, unreal.Blueprint, factory)
        unreal.KismetEditorUtilities.compile_blueprint(bp)
        return {"status": "ok", "message": f"Blueprint created: {package_path}/{asset_name}"}
    except Exception as exc:
        return {"status": "error", "message": f"Blueprint generation failed: {exc}"}


def add_variable_to_blueprint(
    blueprint_path: str,
    variable_name: str,
    variable_type: str = "float",
    request_id: str | None = None,
) -> Dict[str, str]:
    bp_path = _normalize_blueprint_path(blueprint_path)
    var_name = _slugify(variable_name)
    var_type = str(variable_type or "float").strip().lower()
    if not bp_path or not var_name:
        return {"status": "error", "message": "blueprint_path and variable_name are required."}

    bridge_error: Exception | None = None
    try:
        ok = bridge_call(
            ["add_blueprint_variable", "AddBlueprintVariable"],
            bp_path,
            var_name,
            var_type,
        )
        if bool(ok):
            return {
                "status": "ok",
                "message": f"Variable '{var_name}' ({var_type}) added to {bp_path} via AIRDBridge.",
            }
    except Exception as exc:
        bridge_error = exc

    if bridge_error is None:
        return _build_blueprint_bridge_error("Variable", bp_path)

    unreal = try_import_unreal()
    if unreal is None:
        LOGGER.info(
            "Blueprint variable command received for Unreal forwarding: blueprint=%s variable=%s type=%s",
            bp_path,
            var_name,
            var_type,
        )
        response = call_runtime_bridge(
            "add_blueprint_variable",
            {
                "blueprint_path": bp_path,
                "variable_name": var_name,
                "variable_type": var_type,
            },
            request_id=request_id,
        )
        if bool(response.get("ok")):
            return {
                "status": "ok",
                "message": str(
                    response.get("message")
                    or f"Variable '{var_name}' ({var_type}) added to {bp_path} in Unreal runtime."
                ),
            }
        return {
            "status": "error",
            "error": str(response.get("error") or "unreal_runtime_unavailable"),
            "message": (
                str(response.get("message") or "").strip()
                or "Unreal runtime is unavailable for Blueprint editing. Start AIRD Engine inside Unreal and reconnect this UI session."
            ),
        }
    return _build_blueprint_bridge_error("Variable", bp_path)


def add_function_to_blueprint(
    blueprint_path: str, function_name: str, request_id: str | None = None
) -> Dict[str, str]:
    bp_path = _normalize_blueprint_path(blueprint_path)
    fn_name = _slugify(function_name)
    if not bp_path or not fn_name:
        return {"status": "error", "message": "blueprint_path and function_name are required."}

    bridge_error: Exception | None = None
    try:
        ok = bridge_call(
            ["add_blueprint_function", "AddBlueprintFunction"], bp_path, fn_name
        )
        if bool(ok):
            return {
                "status": "ok",
                "message": f"Function '{fn_name}' added to {bp_path} via AIRDBridge.",
            }
    except Exception as exc:
        bridge_error = exc

    if bridge_error is None:
        return _build_blueprint_bridge_error("Function", bp_path)

    unreal = try_import_unreal()
    if unreal is None:
        LOGGER.info(
            "Blueprint function command received for Unreal forwarding: blueprint=%s function=%s",
            bp_path,
            fn_name,
        )
        response = call_runtime_bridge(
            "add_blueprint_function",
            {
                "blueprint_path": bp_path,
                "function_name": fn_name,
            },
            request_id=request_id,
        )
        if bool(response.get("ok")):
            return {
                "status": "ok",
                "message": str(
                    response.get("message")
                    or f"Function '{fn_name}' added to {bp_path} in Unreal runtime."
                ),
            }
        return {
            "status": "error",
            "error": str(response.get("error") or "unreal_runtime_unavailable"),
            "message": (
                str(response.get("message") or "").strip()
                or "Unreal runtime is unavailable for Blueprint editing. Start AIRD Engine inside Unreal and reconnect this UI session."
            ),
        }
    return _build_blueprint_bridge_error("Function", bp_path)
