import os
import sys


def _plugin_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _ensure_paths() -> None:
    root = _plugin_root()
    python_dir = os.path.join(root, "Content", "Python")
    scripts_dir = os.path.join(root, "Scripts")
    if python_dir not in sys.path:
        sys.path.insert(0, python_dir)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)


def main() -> None:
    try:
        _ensure_paths()
        import mcp_server

        started = mcp_server.start_mcp_server("127.0.0.1")
        if started:
            print("AIRD MCP started successfully inside Unreal.")
        else:
            print("AIRD MCP already running inside Unreal.")
    except Exception as exc:
        print(f"AIRD MCP bootstrap failed safely: {exc}")


if __name__ == "__main__":
    main()
