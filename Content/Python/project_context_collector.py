from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from project_context_model import (
    PROJECT_CONTEXT_SCHEMA_VERSION,
    validate_project_context_payload,
)

ASSET_SCAN_LIMIT = 3000
ASSET_EXTENSIONS = {".uasset", ".umap"}


def _to_posix_path(value: Path | str) -> str:
    return str(value).replace("\\", "/")


def _read_json_file(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _find_uproject(project_root: Path) -> Optional[Path]:
    for candidate in project_root.glob("*.uproject"):
        if candidate.is_file():
            return candidate
    return None


def _discover_project_root(start_path: Path) -> Optional[Path]:
    cursor = start_path.resolve()
    for candidate in [cursor, *cursor.parents]:
        if _find_uproject(candidate) is not None:
            return candidate
    return None


def _plugin_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_project_root() -> Path:
    discovered = _discover_project_root(_plugin_root())
    if discovered is not None:
        return discovered
    return _plugin_root().parents[1]


def _relative_to_project(path: Path, project_root: Path) -> str:
    try:
        return _to_posix_path(path.resolve().relative_to(project_root.resolve()))
    except Exception:
        return _to_posix_path(path.resolve())


def _iter_plugin_descriptors(project_root: Path) -> Iterable[Path]:
    plugins_root = project_root / "Plugins"
    if not plugins_root.is_dir():
        return []
    return plugins_root.rglob("*.uplugin")


def _collect_source_roots(project_root: Path, plugin_root: Path) -> List[str]:
    candidates = [
        project_root / "Source",
        project_root / "Content",
        project_root / "Config",
        plugin_root / "Source",
        plugin_root / "Content" / "Python",
        plugin_root / "Content" / "UI",
    ]
    roots: List[str] = []
    for candidate in candidates:
        if candidate.exists():
            roots.append(_relative_to_project(candidate, project_root))
    return sorted(dict.fromkeys(roots))


def _collect_project_modules(project_root: Path, project_name: str) -> List[Dict[str, str]]:
    modules: List[Dict[str, str]] = []
    source_root = project_root / "Source"
    if not source_root.is_dir():
        return modules

    for module_dir in sorted(path for path in source_root.iterdir() if path.is_dir()):
        has_build_file = any(module_dir.glob("*.Build.cs"))
        if not has_build_file:
            continue
        module_name = module_dir.name
        module_type = "project_primary_module" if module_name == project_name else "project_module"
        modules.append(
            {
                "name": module_name,
                "type": module_type,
                "path": _relative_to_project(module_dir, project_root),
            }
        )
    return modules


def _collect_plugin_modules(project_root: Path) -> tuple[List[Dict[str, str]], List[Dict[str, Any]]]:
    modules: List[Dict[str, str]] = []
    plugins: List[Dict[str, Any]] = []
    for descriptor_path in sorted(_iter_plugin_descriptors(project_root)):
        payload = _read_json_file(descriptor_path)
        plugin_name = str(payload.get("FriendlyName") or payload.get("Name") or descriptor_path.stem)
        plugin_rel_path = _relative_to_project(descriptor_path.parent, project_root)
        plugins.append(
            {
                "name": plugin_name,
                "path": plugin_rel_path,
                "enabled": True,
            }
        )
        for module_item in payload.get("Modules", []) if isinstance(payload.get("Modules"), list) else []:
            if not isinstance(module_item, dict):
                continue
            module_name = str(module_item.get("Name") or "").strip()
            if not module_name:
                continue
            module_type = str(module_item.get("Type") or "plugin_module").strip().lower()
            modules.append(
                {
                    "name": module_name,
                    "type": f"plugin_{module_type}",
                    "path": _relative_to_project(descriptor_path.parent / "Source" / module_name, project_root),
                }
            )
    return modules, plugins


def _merge_plugin_enabled_states(
    plugins: List[Dict[str, Any]], uproject_data: Dict[str, Any]
) -> List[Dict[str, Any]]:
    enabled_map: Dict[str, bool] = {}
    for plugin in uproject_data.get("Plugins", []) if isinstance(uproject_data.get("Plugins"), list) else []:
        if not isinstance(plugin, dict):
            continue
        name = str(plugin.get("Name") or "").strip()
        if not name:
            continue
        enabled_map[name.lower()] = bool(plugin.get("Enabled", True))

    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in plugins:
        name = str(item.get("name") or "").strip()
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(
            {
                "name": name,
                "path": str(item.get("path") or ""),
                "enabled": enabled_map.get(key, bool(item.get("enabled", True))),
            }
        )
    return merged


def _collect_asset_index_summary(project_root: Path, scan_limit: int = ASSET_SCAN_LIMIT) -> Dict[str, Any]:
    content_root = project_root / "Content"
    if not content_root.is_dir():
        return {
            "mode": "partial",
            "content_root_exists": False,
            "asset_count": 0,
            "scan_limit": int(scan_limit),
            "truncated": False,
        }

    scanned_files = 0
    asset_count = 0
    truncated = False
    for root, _, files in os.walk(content_root):
        for file_name in files:
            scanned_files += 1
            extension = Path(file_name).suffix.lower()
            if extension in ASSET_EXTENSIONS:
                asset_count += 1
            if scanned_files >= scan_limit:
                truncated = True
                break
        if truncated:
            break

    return {
        "mode": "partial",
        "content_root_exists": True,
        "asset_count": int(asset_count),
        "scanned_files": int(scanned_files),
        "scan_limit": int(scan_limit),
        "truncated": bool(truncated),
    }


def collect_project_context(project_root: str | Path | None = None) -> Dict[str, Any]:
    started_at = time.perf_counter()
    resolved_project_root = (
        Path(project_root).resolve() if project_root is not None else _default_project_root().resolve()
    )
    plugin_root = _plugin_root().resolve()
    diagnostics: Dict[str, Any] = {
        "collector_mode": "read_only",
        "collection_limitations": [],
        "warnings": [],
    }

    descriptor_path = _find_uproject(resolved_project_root)
    uproject_payload = _read_json_file(descriptor_path) if descriptor_path else {}
    project_name = str(
        (descriptor_path.stem if descriptor_path is not None else "")
        or uproject_payload.get("FileVersionUE5", "")
        or resolved_project_root.name
    ).strip() or resolved_project_root.name

    project_modules = _collect_project_modules(resolved_project_root, project_name)
    plugin_modules, plugins = _collect_plugin_modules(resolved_project_root)
    plugins = _merge_plugin_enabled_states(plugins, uproject_payload)

    modules = project_modules + plugin_modules
    deduped_modules: List[Dict[str, str]] = []
    seen_module_keys: set[tuple[str, str, str]] = set()
    for module in modules:
        key = (
            str(module.get("name") or ""),
            str(module.get("type") or ""),
            str(module.get("path") or ""),
        )
        if key in seen_module_keys:
            continue
        seen_module_keys.add(key)
        deduped_modules.append(
            {
                "name": key[0],
                "type": key[1],
                "path": key[2],
            }
        )

    source_roots = _collect_source_roots(resolved_project_root, plugin_root)
    cpp_module_map = {
        module.get("name", ""): {
            "type": module.get("type", ""),
            "path": module.get("path", ""),
        }
        for module in deduped_modules
    }
    workspace_hints = [
        "Collector runs in read-only mode and does not mutate project files.",
        "Project context focuses on modules/plugins/source roots before deep indexing.",
    ]
    if descriptor_path is None:
        diagnostics["warnings"].append("uproject_not_found_in_project_root")
    diagnostics["collection_limitations"].append(
        "blueprint_widget_links not inferred in T041 without dedicated cross-reference indexer."
    )

    payload: Dict[str, Any] = {
        "schema_version": PROJECT_CONTEXT_SCHEMA_VERSION,
        "project_root": _to_posix_path(resolved_project_root),
        "project_name": project_name,
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_roots": source_roots,
        "modules": deduped_modules,
        "plugins": plugins,
        "assets_index_summary": _collect_asset_index_summary(resolved_project_root),
        "blueprint_widget_links": [],
        "cpp_module_map": cpp_module_map,
        "workspace_hints": workspace_hints,
        "unresolved_references": [],
        "diagnostics": diagnostics,
        "performance_metrics": {
            "collect_duration_ms": round((time.perf_counter() - started_at) * 1000.0, 2),
            "asset_scan_limit": ASSET_SCAN_LIMIT,
        },
    }

    ok, errors = validate_project_context_payload(payload)
    if not ok:
        diagnostics["warnings"].append("payload_validation_failed")
        diagnostics["validation_errors"] = errors
    return payload
