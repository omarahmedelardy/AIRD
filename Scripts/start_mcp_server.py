import asyncio
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(THIS_DIR, ".."))
PYTHON_DIR = os.path.join(PLUGIN_ROOT, "Content", "Python")

if PYTHON_DIR not in sys.path:
    sys.path.insert(0, PYTHON_DIR)

from mcp_server import run_server


if __name__ == "__main__":
    asyncio.run(run_server("127.0.0.1", 8765))
