from __future__ import annotations

import posixpath
import re
from typing import Any, Dict

PARSER_POLICY = (
    "deterministic_content_order: create folder > create asset/file placeholder; "
    "requires create-like verb and /Game target; no LLM inference."
)

_CREATE_VERBS = (
    "create",
    "make",
    "new",
    "انشئ",
    "أنشئ",
    "انشاء",
    "إنشاء",
    "اعمل",
    "قم بإنشاء",
)
_FOLDER_TERMS = ("folder", "dir", "directory", "مجلد", "فولدر")
_ASSET_TERMS = ("asset", "file", "اصل", "أصل", "ملف")
_GAME_PATH_RE = re.compile(r"(/Game(?:/[A-Za-z0-9_.\-/]+)?)", flags=re.IGNORECASE)
_NAMED_RE = re.compile(
    r"(?:named|name|باسم|اسمه|سميه|سمه)\s+([A-Za-z0-9_][A-Za-z0-9_.-]*)",
    flags=re.IGNORECASE,
)
_EN_FOLDER_RE = re.compile(
    r"create\s+(?:new\s+)?folder\s+([A-Za-z0-9_][A-Za-z0-9_.-]*)\s+(?:in|under|inside)\s+(/Game(?:/[A-Za-z0-9_.\-/]+)?)",
    flags=re.IGNORECASE,
)


def _normalize_game_path(path: str) -> str:
    raw = str(path or "").strip().replace("\\", "/")
    if not raw:
        return "/Game"
    if raw.lower() == "/game":
        return "/Game"
    if raw.lower().startswith("/game/"):
        suffix = raw[6:]
        normalized = posixpath.normpath("/Game/" + suffix.lstrip("/"))
        if not normalized.startswith("/Game"):
            return "/Game"
        return normalized
    return "/Game"


def _has_create_signal(text: str) -> bool:
    low = str(text or "").lower()
    return any(token in low for token in _CREATE_VERBS)


def _extract_game_path(text: str) -> str:
    match = _GAME_PATH_RE.search(str(text or ""))
    if match is None:
        return "/Game"
    return _normalize_game_path(str(match.group(1) or "/Game"))


def _extract_name(text: str) -> str:
    named_match = _NAMED_RE.search(str(text or ""))
    if named_match is not None:
        return str(named_match.group(1) or "").strip()
    return ""


def parse_content_command(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    low = raw.lower()
    has_game = "/game" in low
    has_create = _has_create_signal(raw)
    has_folder = any(token in low for token in _FOLDER_TERMS)
    has_asset = any(token in low for token in _ASSET_TERMS)

    if not has_game or not has_create:
        return {"kind": "none", "parser_policy": PARSER_POLICY}

    en_folder = _EN_FOLDER_RE.search(raw)
    if en_folder is not None:
        folder_name = str(en_folder.group(1) or "").strip()
        target_path = _normalize_game_path(str(en_folder.group(2) or "/Game"))
        return {
            "kind": "action",
            "action": "create_content_folder",
            "payload": {
                "action": "create_content_folder",
                "target_root": "/Game",
                "target_path": target_path,
                "folder_name": folder_name,
                "target_folder_path": _normalize_game_path(
                    posixpath.join(target_path, folder_name)
                ),
                "asset_name": "",
                "inferred_type": "folder",
            },
            "parser_policy": PARSER_POLICY,
        }

    path = _extract_game_path(raw)
    explicit_name = _extract_name(raw)
    if has_folder:
        folder_name = explicit_name
        target_path = path
        if not folder_name and path.lower() != "/game":
            folder_name = path.split("/")[-1]
            target_path = _normalize_game_path(posixpath.dirname(path))
        if not folder_name:
            return {
                "kind": "parse_failure",
                "action": "create_content_folder",
                "reason": "Folder name is required.",
                "payload": {
                    "action": "create_content_folder",
                    "target_root": "/Game",
                    "target_path": target_path,
                },
                "parser_policy": PARSER_POLICY,
            }
        return {
            "kind": "action",
            "action": "create_content_folder",
            "payload": {
                "action": "create_content_folder",
                "target_root": "/Game",
                "target_path": target_path,
                "folder_name": folder_name,
                "target_folder_path": _normalize_game_path(
                    posixpath.join(target_path, folder_name)
                ),
                "asset_name": "",
                "inferred_type": "folder",
            },
            "parser_policy": PARSER_POLICY,
        }

    if has_asset:
        asset_name = explicit_name
        target_path = path
        if not asset_name and path.lower() != "/game":
            asset_name = path.split("/")[-1]
            target_path = _normalize_game_path(posixpath.dirname(path))
        if not asset_name:
            return {
                "kind": "parse_failure",
                "action": "create_asset_placeholder",
                "reason": "Asset/File name is required.",
                "payload": {
                    "action": "create_asset_placeholder",
                    "target_root": "/Game",
                    "target_path": target_path,
                },
                "parser_policy": PARSER_POLICY,
            }
        inferred_type = "file_placeholder" if "file" in low or "ملف" in low else "asset_placeholder"
        return {
            "kind": "action",
            "action": "create_asset_placeholder",
            "payload": {
                "action": "create_asset_placeholder",
                "target_root": "/Game",
                "target_path": target_path,
                "folder_name": "",
                "asset_name": asset_name,
                "target_asset_path": _normalize_game_path(
                    posixpath.join(target_path, asset_name)
                ),
                "inferred_type": inferred_type,
            },
            "parser_policy": PARSER_POLICY,
        }

    return {
        "kind": "parse_failure",
        "action": "create_content_folder",
        "reason": "Unsupported content operation format.",
        "payload": {"target_root": "/Game", "target_path": path},
        "parser_policy": PARSER_POLICY,
    }

