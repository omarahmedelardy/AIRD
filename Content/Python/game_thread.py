"""
Run Python callables on the Unreal Editor game thread without long blocking.

MCP workers must not hold the process for seconds: we only wait up to ``max_wait``
(default 0.05s). If the game thread does not finish in time, return status
``pending`` so callers can respond without freezing the editor.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Literal, Optional, Tuple, TypeVar

T = TypeVar("T")

# Substrings Unreal raises when Python calls engine API off the game thread
_THREAD_GUARD_MARKERS = (
    "outside the main game thread",
    "attempted to access unreal api",
)

GameThreadStatus = Literal["ok", "pending"]


def _is_thread_guard_error(exc: BaseException) -> bool:
    if not isinstance(exc, RuntimeError):
        return False
    msg = str(exc).lower()
    return any(m in msg for m in _THREAD_GUARD_MARKERS)


def run_on_game_thread_sync(
    fn: Callable[[], T],
    *,
    max_wait: float = 0.05,
) -> Tuple[GameThreadStatus, Optional[T]]:
    """
    Run ``fn`` on the game thread if needed.

    - Always returns quickly: ``Event.wait`` uses at most ``max_wait`` seconds.
    - ``("ok", value)`` — ``fn`` completed (immediate path or within ``max_wait``).
    - ``("pending", None)`` — no result yet; the next Slate tick will still run
      the one-shot callback to unregister and skip ``fn`` if abandoned.

    Never raises ``TimeoutError``; real errors from ``fn`` are still raised when
    the callback completes within ``max_wait``.
    """
    import unreal  # type: ignore

    try:
        return ("ok", fn())
    except RuntimeError as exc:
        if not _is_thread_guard_error(exc):
            raise

    result: list[Any] = []
    errors: list[BaseException] = []
    done = threading.Event()
    abandoned = threading.Event()
    handle_holder: list[Any] = [None]

    def on_post_tick(delta_time: float) -> None:
        try:
            if not abandoned.is_set():
                try:
                    result.append(fn())
                except BaseException as err:  # noqa: BLE001
                    errors.append(err)
        finally:
            h = handle_holder[0]
            handle_holder[0] = None
            if h is not None:
                try:
                    unreal.unregister_slate_post_tick_callback(h)
                except Exception:
                    pass
            done.set()

    handle_holder[0] = unreal.register_slate_post_tick_callback(on_post_tick)

    if done.wait(max_wait):
        if errors:
            raise errors[0]
        return ("ok", result[0] if result else None)

    abandoned.set()
    return ("pending", None)
