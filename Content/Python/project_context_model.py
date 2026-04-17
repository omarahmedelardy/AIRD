from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Tuple

PROJECT_CONTEXT_SCHEMA_VERSION = "1.0.0"
PROJECT_CONTEXT_MODEL_NAME = "aird_project_context"

PROJECT_CONTEXT_REQUIRED_FIELDS = [
    "schema_version",
    "project_root",
    "project_name",
    "collected_at",
    "source_roots",
    "modules",
    "plugins",
]

PROJECT_CONTEXT_RECOMMENDED_FIELDS = [
    "assets_index_summary",
    "blueprint_widget_links",
    "cpp_module_map",
    "workspace_hints",
]

PROJECT_CONTEXT_OPTIONAL_FIELDS = [
    "unresolved_references",
    "diagnostics",
    "performance_metrics",
]


PROJECT_CONTEXT_MODEL: Dict[str, Any] = {
    "model": PROJECT_CONTEXT_MODEL_NAME,
    "schema_version": PROJECT_CONTEXT_SCHEMA_VERSION,
    "required_fields": list(PROJECT_CONTEXT_REQUIRED_FIELDS),
    "recommended_fields": list(PROJECT_CONTEXT_RECOMMENDED_FIELDS),
    "optional_fields": list(PROJECT_CONTEXT_OPTIONAL_FIELDS),
    "backward_compatibility": {
        "policy": "additive",
        "unknown_fields_allowed": True,
        "notes": (
            "Collectors may append new fields. Consumers should rely on required_fields "
            "and treat other fields as optional enhancements."
        ),
    },
    "json_schema": {
        "type": "object",
        "required": list(PROJECT_CONTEXT_REQUIRED_FIELDS),
        "properties": {
            "schema_version": {"type": "string"},
            "project_root": {"type": "string"},
            "project_name": {"type": "string"},
            "collected_at": {"type": "string"},
            "source_roots": {"type": "array", "items": {"type": "string"}},
            "modules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "type", "path"],
                    "properties": {
                        "name": {"type": "string"},
                        "type": {"type": "string"},
                        "path": {"type": "string"},
                    },
                },
            },
            "plugins": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "path", "enabled"],
                    "properties": {
                        "name": {"type": "string"},
                        "path": {"type": "string"},
                        "enabled": {"type": "boolean"},
                    },
                },
            },
            "assets_index_summary": {"type": "object"},
            "blueprint_widget_links": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["blueprint_path", "widget_path", "confidence"],
                    "properties": {
                        "blueprint_path": {"type": "string"},
                        "widget_path": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                },
            },
            "cpp_module_map": {"type": "object"},
            "workspace_hints": {"type": "array", "items": {"type": "string"}},
            "unresolved_references": {"type": "array", "items": {"type": "string"}},
            "diagnostics": {"type": "object"},
            "performance_metrics": {"type": "object"},
        },
    },
    "example_minimal": {
        "schema_version": PROJECT_CONTEXT_SCHEMA_VERSION,
        "project_root": "D:/02-Unreal_Project/My_Own_Widget",
        "project_name": "My_Own_Widget",
        "collected_at": "2026-04-16T12:00:00Z",
        "source_roots": [
            "Source",
            "Plugins/AIRD/Source",
            "Plugins/AIRD/Content/Python",
        ],
        "modules": [
            {
                "name": "AIRD",
                "type": "plugin_module",
                "path": "Plugins/AIRD/Source/AIRD",
            }
        ],
        "plugins": [
            {
                "name": "AIRD",
                "path": "Plugins/AIRD",
                "enabled": True,
            }
        ],
    },
}


def get_project_context_model() -> Dict[str, Any]:
    return deepcopy(PROJECT_CONTEXT_MODEL)


def validate_project_context_payload(payload: Any) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(payload, dict):
        return False, ["payload must be an object"]

    for field in PROJECT_CONTEXT_REQUIRED_FIELDS:
        if field not in payload:
            errors.append(f"missing required field: {field}")

    if errors:
        return False, errors

    if not isinstance(payload.get("schema_version"), str):
        errors.append("schema_version must be a string")
    elif payload.get("schema_version") != PROJECT_CONTEXT_SCHEMA_VERSION:
        errors.append(
            f"schema_version mismatch: expected {PROJECT_CONTEXT_SCHEMA_VERSION}"
        )

    if not isinstance(payload.get("project_root"), str):
        errors.append("project_root must be a string")
    if not isinstance(payload.get("project_name"), str):
        errors.append("project_name must be a string")
    if not isinstance(payload.get("collected_at"), str):
        errors.append("collected_at must be a string")

    source_roots = payload.get("source_roots")
    if not isinstance(source_roots, list) or not all(
        isinstance(item, str) for item in source_roots
    ):
        errors.append("source_roots must be an array of strings")

    modules = payload.get("modules")
    if not isinstance(modules, list):
        errors.append("modules must be an array")
    else:
        for index, item in enumerate(modules):
            if not isinstance(item, dict):
                errors.append(f"modules[{index}] must be an object")
                continue
            for key in ("name", "type", "path"):
                if not isinstance(item.get(key), str):
                    errors.append(f"modules[{index}].{key} must be a string")

    plugins = payload.get("plugins")
    if not isinstance(plugins, list):
        errors.append("plugins must be an array")
    else:
        for index, item in enumerate(plugins):
            if not isinstance(item, dict):
                errors.append(f"plugins[{index}] must be an object")
                continue
            if not isinstance(item.get("name"), str):
                errors.append(f"plugins[{index}].name must be a string")
            if not isinstance(item.get("path"), str):
                errors.append(f"plugins[{index}].path must be a string")
            if not isinstance(item.get("enabled"), bool):
                errors.append(f"plugins[{index}].enabled must be a boolean")

    return (len(errors) == 0), errors
