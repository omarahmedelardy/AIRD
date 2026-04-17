# AIRD MCP Setup (Unreal Engine)

## 1) Enable required Unreal plugins
1. Open Unreal Editor.
2. Go to `Edit > Plugins`.
3. Enable:
   - `Python Editor Script Plugin`
   - `Editor Scripting Utilities`
   - `Web Browser Widget`
4. Restart Unreal Editor.

## 2) Install Python dependency (`websockets`)
Run this from plugin root:

```bat
Scripts\install_dependencies.bat
```

This installs `websockets` (required by the MCP server).

## 3) Start AIRD panel
1. Open the AIRD panel/tab in Unreal.
2. AIRD auto-starts MCP from:
   - `Content/Python/mcp_server.py`
3. MCP listens on:
   - `ws://127.0.0.1:8765`

## 3.1) Start Context Server (Node.js)
From plugin root:

```bat
Scripts\start_context_server.bat
```

Or:

```bat
node context_server.js
```

Default URL:

- `http://127.0.0.1:8787`

Endpoints:

- `GET /health`
- `POST /scene-sync`
- `POST /llm/chat`

## 4) Verify MCP is running
Check log file:

`Content/Python/AIRD_MCP.log`

Expected line:

`Starting MCP WebSocket server on ws://127.0.0.1:8765`

## 5) Test commands from UI
Examples:
- `create cube at 0 0 100`
- `create sphere at 300 0 120`
- `move actor cube_ab12cd34 to 0 200 100`
- `delete actor cube_ab12cd34`

## 6) Message formats supported
- Structured command:
```json
{ "type": "command", "payload": "create cube at 0 0 100" }
```
- Batch command:
```json
{
  "type": "batch",
  "commands": [
    { "type": "command", "payload": "create cube at 0 0 100" },
    { "type": "command", "payload": "create sphere at 200 0 100" }
  ]
}
```
- JSON-RPC compatibility remains supported:
  - `get_scene_context`
  - `execute_command`
  - `capture_viewport`
