import json
import logging
from typing import Any, Callable, Optional

LOGGER = logging.getLogger("aird")


def setup_logging(level: int = logging.INFO) -> None:
    if LOGGER.handlers:
        return
    logging.basicConfig(level=level, format="[AIRD] %(levelname)s: %(message)s")


def try_import_unreal():
    try:
        import unreal  # type: ignore
        return unreal
    except Exception:
        return None


def bridge_call(method_candidates: list[str], *args, **kwargs) -> Any:
    unreal = try_import_unreal()
    if unreal is None or not hasattr(unreal, "AIRDBridge"):
        raise RuntimeError("unreal.AIRDBridge is unavailable")

    bridge = unreal.AIRDBridge
    for name in method_candidates:
        fn = getattr(bridge, name, None)
        if callable(fn):
            return fn(*args, **kwargs)
    raise AttributeError(f"No bridge function found for {method_candidates}")


def demo_message() -> str:
    return "Demo Mode - API key required for live calls"


def safe_json_loads(text: str, fallback: Optional[Any] = None) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return {} if fallback is None else fallback
