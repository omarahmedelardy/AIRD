import os
import sys
import traceback


def _plugin_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _log(message: str) -> None:
    line = f"[AIRD Runtime Bridge Bootstrap] {message}"
    print(line)
    try:
        import unreal  # type: ignore

        unreal.log(line)
    except Exception:
        pass


def _ensure_paths() -> None:
    root = _plugin_root()
    python_dir = os.path.join(root, "Content", "Python")
    scripts_dir = os.path.join(root, "Scripts")
    if python_dir not in sys.path:
        sys.path.insert(0, python_dir)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    _log(f"python path ensured: plugin_root={root}")
    _log(f"python path ensured: content_python={python_dir}")
    _log(f"python path ensured: scripts={scripts_dir}")


def main() -> None:
    _log("runtime bridge bootstrap started")
    try:
        _ensure_paths()

        try:
            import unreal  # type: ignore

            _log("import unreal succeeded")
            try:
                _log(f"unreal project dir = {unreal.Paths.project_dir()}")
            except Exception:
                pass
        except Exception as exc:
            _log(f"import unreal failed: {exc}")

        _log("importing unreal_runtime_bridge module")
        import unreal_runtime_bridge

        try:
            root_path = unreal_runtime_bridge.get_runtime_bridge_root_path()
            _log(f"runtime bridge root path = {root_path}")
        except Exception as exc:
            _log(f"runtime bridge root path read failed: {exc}")

        _log("starting unreal runtime worker")
        started = unreal_runtime_bridge.start_runtime_bridge()
        if started:
            _log("worker loop started")
        else:
            _log("worker loop did not start (already running or unavailable)")
    except Exception as exc:
        _log(f"bootstrap failed: {exc}")
        _log(traceback.format_exc())


if __name__ == "__main__":
    main()
