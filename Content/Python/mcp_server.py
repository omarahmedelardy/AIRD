from __future__ import annotations

import asyncio
import threading
import time
from typing import Optional

# Ensures game-thread helper is loadable with MCP; scene_perception uses it for AIRDBridge.
import game_thread  # noqa: F401

from server import run_server
from runtime_config import DEFAULT_CONFIG, load_runtime_config

_START_LOCK = threading.Lock()
_STARTED = False
_LOOP: Optional[asyncio.AbstractEventLoop] = None
_SERVER_TASK: Optional[asyncio.Task[None]] = None
_SERVER_THREAD: Optional[threading.Thread] = None


def _resolve_mcp_port(explicit_port: Optional[int] = None) -> int:
    if explicit_port is not None:
        return int(explicit_port)
    cfg = load_runtime_config()
    return int(cfg.get("mcp_websocket_port", DEFAULT_CONFIG["mcp_websocket_port"]))


def _run_loop(host: str, port: int) -> None:
    global _LOOP, _SERVER_TASK, _STARTED

    loop = asyncio.new_event_loop()
    _LOOP = loop
    asyncio.set_event_loop(loop)
    task = loop.create_task(run_server(host, port))
    _SERVER_TASK = task

    try:
        loop.run_until_complete(task)
    except asyncio.CancelledError:
        pass
    except Exception:
        # Keep failures isolated from Unreal editor thread.
        pass
    finally:
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for pending_task in pending:
                pending_task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass

        try:
            loop.close()
        except Exception:
            pass

        _LOOP = None
        _SERVER_TASK = None
        with _START_LOCK:
            _STARTED = False


def start_mcp_server(host: str = "127.0.0.1", port: Optional[int] = None) -> bool:
    global _STARTED, _SERVER_THREAD

    try:
        from server import _prepare_runtime_buffers

        _prepare_runtime_buffers()
    except Exception:
        pass

    with _START_LOCK:
        if _SERVER_THREAD is not None and not _SERVER_THREAD.is_alive():
            _SERVER_THREAD = None
            _STARTED = False

        if _STARTED:
            return False

        resolved_port = _resolve_mcp_port(port)

        thread = threading.Thread(
            target=_run_loop,
            args=(host, resolved_port),
            daemon=True,
            name="aird_mcp_server",
        )
        _SERVER_THREAD = thread
        thread.start()
        _STARTED = True
        return True


def stop_mcp_server(timeout_sec: float = 1.0) -> bool:
    global _STARTED, _SERVER_THREAD

    with _START_LOCK:
        if not _STARTED:
            return False
        loop = _LOOP
        task = _SERVER_TASK
        thread = _SERVER_THREAD

    try:
        if loop is not None and task is not None and not task.done():
            loop.call_soon_threadsafe(task.cancel)
    except Exception:
        pass

    if thread is not None and thread.is_alive():
        thread.join(max(0.0, float(timeout_sec)))

    with _START_LOCK:
        if _SERVER_THREAD is not None and not _SERVER_THREAD.is_alive():
            _SERVER_THREAD = None
            _STARTED = False
        return not _STARTED


def is_mcp_running() -> bool:
    with _START_LOCK:
        return bool(_STARTED and _SERVER_THREAD is not None and _SERVER_THREAD.is_alive())


def update_scene_context(context_server_url: Optional[str] = None) -> bool:
    """
    Pull scene context from AIRDBridge immediately and sync once to the context server.
    Returns True when valid scene context is captured and sync succeeds.
    """
    from server import (
        DEFAULT_CONTEXT_SERVER_URL,
        _has_required_scene_context,
        _safe_scene_context,
        _sync_scene_snapshot_with_retry,
    )

    target_url = str(context_server_url or DEFAULT_CONTEXT_SERVER_URL).rstrip("/")
    scene = _safe_scene_context()
    if not _has_required_scene_context(scene):
        return False
    _sync_scene_snapshot_with_retry(target_url, scene)
    return True


def update_scene_context_async(delay_sec: float = 20.0, context_server_url: Optional[str] = None) -> None:
    def _worker() -> None:
        try:
            time.sleep(max(0.0, float(delay_sec)))
            update_scene_context(context_server_url=context_server_url)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True, name="aird_scene_context_sync").start()


if __name__ == "__main__":
    asyncio.run(run_server())
