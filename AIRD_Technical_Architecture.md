# AIRD - Comprehensive Technical Architecture Documentation

**Version:** 1.0.0  
**Engine:** Unreal Engine 5.7.0  
**Last Updated:** April 2026  
**Author:** AIRD Architecture Team

---

## Table of Contents

1. [Project Structure](#1-project-structure)
2. [Full System Architecture](#2-full-system-architecture)
3. [Logic Flow](#3-logic-flow)
4. [Key Components Explained](#4-key-components-explained)
5. [Technical Challenges & Solutions](#5-technical-challenges--solutions)

---

## 1. Project Structure

### 1.1 File Tree Overview

```
AIRD/
├── AIRD.uplugin                    # Plugin manifest (UE 5.7 compatibility)
├── config.json                      # Runtime port configuration
├── context_server.js               # Context server (Node.js)
├── context_memory.json             # Memory state persistence
│
├── Content/
│   ├── Python/
│   │   ├── server.py               # Main MCP server (WebSocket + HTTP)
│   │   ├── mcp_server.py          # MCP startup/shutdown wrapper
│   │   ├── scene_perception.py    # Scene data collection (EditorActorSubsystem)
│   │   ├── runtime_config.py      # Port configuration management
│   │   ├── run_utils.py           # AIRDBridge utility functions
│   │   ├── game_thread.py         # Game thread execution helper
│   │   ├── blueprint_generator.py # Blueprint generation from prompts
│   │   ├── knowledge_graph.py     # Spatial relationship mapping
│   │   └── __pycache__/           # Compiled Python bytecode
│   │
│   └── UI/
│       ├── AIRDPro.html           # Main UI (AIRDApp JavaScript class)
│       └── frontend.js            # WebSocket RPC patch for RDStudioUltimate
│
├── _Package/
│   ├── Source/
│   │   ├── AIRD/
│   │   │   └── Public/
│   │   │       └── AIRDBridge.h  # C++ Blueprint function library
│   │   │
│   │   └── AIRDEditor/
│   │       ├── Private/
│   │       │   └── AIRDWidget.cpp # Slate widget implementation
│   │       └── Public/
│   │           └── AIRDEditor.h   # Editor module header
│   │
│   └── Content/UI/                 # Packaged UI resources
│
├── Binaries/
│   └── Win64/
│       ├── UnrealEditor-AIRD.dll      # Main plugin binary
│       ├── UnrealEditor-AIRDEditor.dll # Editor module binary
│       └── *.pdb                      # Debug symbols
│
├── Config/
│   ├── DefaultAIRD.ini           # Default plugin settings
│   └── FilterPlugin.ini          # Content cooking filters
│
└── Resources/
    └── Icon128.png               # Plugin icon
```

### 1.2 Folder Role Explanation

| Folder | Purpose | Key Files |
|--------|---------|-----------|
| `Content/Python/` | Python backend for AI inference, scene perception, MCP communication | `server.py`, `scene_perception.py`, `mcp_server.py` |
| `Content/UI/` | Web-based UI served via WebBrowserWidget | `AIRDPro.html`, `frontend.js` |
| `_Package/Source/AIRD/Public/` | C++ bridge exposing UE APIs to Python | `AIRDBridge.h` |
| `_Package/Source/AIRDEditor/` | Editor module for UI integration | `AIRDWidget.cpp` |
| `Binaries/Win64/` | Compiled plugin DLLs | `UnrealEditor-AIRD.dll` |
| `Config/` | Unreal Engine configuration | `DefaultAIRD.ini` |

---

## 2. Full System Architecture

### 2.1 Frontend-Backend Bridge Architecture

The AIRD system uses a **multi-layer communication stack** to connect the HTML/JavaScript frontend with the Python backend running inside Unreal Engine.

```
┌─────────────────────────────────────────────────────────────────┐
│                    AIRD Communication Flow                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐     WebSocket      ┌────────────────────────┐ │
│  │   HTML UI   │ ──────────────►   │   MCP Server (Python)  │ │
│  │ AIRDPro.html│    JSON-RPC        │   Port: 30020          │ │
│  └─────────────┘                    └────────────────────────┘ │
│        │                                    │                  │
│        │                                    │                  │
│  ┌─────▼─────┐                       ┌──────▼──────────────┐  │
│  │frontend.js│                       │   AIRDBridge (C++)   │  │
│  │RPC Patch  │                       │   Blueprint Library  │  │
│  └───────────┘                       └──────────────────────┘ │
│                                              │                  │
│                                              │ Unreal API       │
│                                         ┌────▼─────────────┐    │
│                                         │ Unreal Engine    │    │
│                                         │ Editor Thread    │    │
│                                         └──────────────────┘    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

#### Key Bridge Components

1. **AIRDPro.html** - Contains `AIRDApp` JavaScript class with WebSocket client
2. **frontend.js** - Patches `RDStudioUltimate` class to inject RPC capabilities
3. **AIRDBridge.h** - C++ functions callable from Python via `unreal.AIRDBridge`

#### Communication Protocol

```javascript
// Frontend: Send command via WebSocket JSON-RPC
ws.send(JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    method: "execute_command",
    params: {
        text: "create a cube at 0,0,100",
        model: "GPT-4 Turbo"
    }
}));

// Backend: Return response
{
    jsonrpc: "2.0",
    id: 1,
    result: {
        ok: true,
        message: "Cube created successfully",
        actions: [...]
    }
}
```

### 2.2 MCP Infrastructure

The **Model Context Protocol (MCP)** server provides a standardized interface for AI model interactions.

```
┌─────────────────────────────────────────────────────────────────┐
│                      MCP Server Architecture                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    mcp_server.py                         │   │
│  │  - Runs in separate thread (daemon)                      │   │
│  │  - Manages asyncio event loop                            │   │
│  │  - Handles graceful shutdown                             │   │
│  └──────────────────────────┬───────────────────────────────┘   │
│                             │                                    │
│  ┌──────────────────────────▼───────────────────────────────┐   │
│  │                    server.py                              │   │
│  │                                                          │   │
│  │  WebSocket Server (Port 30020)  ◄─── JSON-RPC 2.0       │   │
│  │  ┌─────────────────────────┐                              │   │
│  │  │ execute_command()      │ - Main entry point          │   │
│  │  │ get_scene_context()    │ - Scene data retrieval      │   │
│  │  │ health_check()         │ - Server status             │   │
│  │  └─────────────────────────┘                              │   │
│  │                                                          │   │
│  │  Provider Integration:                                     │   │
│  │  - OpenAI (gpt-4o, gpt-4o-mini)                          │   │
│  │  - Anthropic (Claude 3.5 Sonnet)                        │   │
│  │  - OpenRouter (multi-provider)                           │   │
│  │  - Together AI (Llama 3.1)                               │   │
│  │  - Ollama/LM Studio (local)                              │   │
│  │                                                          │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

#### MCP Server Ports

| Port | Purpose | Configuration |
|------|---------|---------------|
| **30020** | WebSocket (MCP) - Primary communication with frontend | `mcp_websocket_port` in `config.json` |
| **30010** | Remote Control HTTP - Unreal Engine API access | `remote_control_http_port` in `config.json` |
| **30000** | Legacy port - Fallback for older versions | `legacy_port` in `config.json` |

### 2.3 Communication Protocol Details

#### Dynamic Port Allocation

The system uses **runtime configuration** to determine ports:

```python
# runtime_config.py
DEFAULT_CONFIG = {
    "mcp_websocket_port": 30020,
    "remote_control_http_port": 30010,
    "legacy_port": 30000,
}
```

Ports are loaded from `config.json` at startup, allowing runtime changes without recompilation.

#### WebSocket Message Flow

```
Client                              Server
  │                                    │
  │──── JSON-RPC Request ─────────────►│
  │  {                                │
  │    "jsonrpc": "2.0",             │
  │    "method": "execute_command",  │
  │    "params": {...},               │
  │    "id": 1                        │
  │  }                                │
  │                                    │
  │◄──── Response ────────────────────│
  │  {                                │
  │    "jsonrpc": "2.2",             │
  │    "result": {...},              │
  │    "id": 1                        │
  │  }                                │
```

### 2.4 AIRD 2.0 Multi-Agent Extension (Phase 1 Baseline)

To support AIRD 2.0, the architecture is extended with dedicated extension points while preserving the existing runtime flow.

#### New Python Module Layout

- `Content/Python/agents/` - Agent abstractions and orchestrator (routing layer)
- `Content/Python/memory/` - Persistent conversation context management
- `Content/Python/tools/` - Reusable analysis/tooling helpers shared by agents

#### Runtime Feature Flags (Additive, Backward-Compatible)

`runtime_config.py` now includes optional UI flags:

- `enable_agent_selector_ui` (default: `false`)
- `enable_history_ui` (default: `false`)

These flags are additive only, so current AIRD UI behavior remains unchanged unless explicitly enabled.

---

## 3. Logic Flow

### 3.1 Request Lifecycle (Start Button → Statistics Display)

```
┌─────────────────────────────────────────────────────────────────┐
│                  Request Lifecycle Sequence                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. USER ACTION                                                 │
│     ┌────────────────┐                                          │
│     │ User clicks    │                                          │
│     │ "Start Engine" │                                          │
│     └───────┬────────┘                                          │
│             │                                                   │
│             ▼                                                   │
│  2. FRONTEND WebSocket Connect                                  │
│     ┌─────────────────────────────────────────────────────┐    │
│     │ AIRDApp.connectWebSocket()                           │    │
│     │ - Creates WebSocket to ws://127.0.0.1:30020         │    │
│     │ - Registers onopen, onmessage, onerror handlers     │    │
│     └───────────────────────────┬───────────────────────────┘    │
│                                 │                                 │
│                                 ▼                                 │
│  3. MCP SERVER Accepts Connection                               │
│     ┌─────────────────────────────────────────────────────┐    │
│     │ server.py: run_server()                             │    │
│     │ - Accepts WebSocket connection                      │    │
│     │ - Registers ping/pong heartbeat                     │    │
│     │ - Starts scene sync loop (every 2 seconds)          │    │
│     └───────────────────────────┬───────────────────────────┘    │
│                                 │                                 │
│                                 ▼                                 │
│  4. SCENE CONTEXT RETRIEVAL                                     │
│     ┌─────────────────────────────────────────────────────┐    │
│     │ scene_perception.py: get_scene_context()            │    │
│     │                                                     │    │
│     │ ┌─────────────────────────────────────────────────┐ │    │
│     │ │ Step 1: Try EditorActorSubsystem (fast)        │ │    │
│     │ │ - get_editor_subsystem(EditorActorSubsystem)   │ │    │
│     │ │ - get_all_level_actors()                        │ │    │
│     │ └─────────────────────────────────────────────────┘ │    │
│     │                                                     │    │
│     │ ┌─────────────────────────────────────────────────┐ │    │
│     │ │ Step 2: Fallback to Remote Control API         │ │    │
│     │ │ - HTTP PUT to 127.0.0.1:30010                   │ │    │
│     │ │ - /remote/object/call → GetAllActorsOfClass    │ │    │
│     │ └─────────────────────────────────────────────────┘ │    │
│     │                                                     │    │
│     │ ┌─────────────────────────────────────────────────┐ │    │
│     │ │ Step 3: Enrich scene data                      │ │    │
│     │ │ - Count lights, cameras, static meshes          │    │
│     │ │ - Add timestamp (ISO 8601)                    │    │
│     │ └─────────────────────────────────────────────────┘ │    │
│     └───────────────────────────┬───────────────────────────┘    │
│                                 │                                 │
│                                 ▼                                 │
│  5. FRONTEND RECEIVES SCENE DATA                                │
│     ┌─────────────────────────────────────────────────────┐    │
│     │ handleRpcSocketMessage()                            │    │
│     │ - Parses JSON payload                               │    │
│     │ - Updates actorTree (left panel)                    │    │
│     │ - Updates sceneStats (statistics)                   │    │
│     │ - Renders actors on viewport canvas                 │    │
│     └─────────────────────────────────────────────────────┘    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 Error Handling & Crash Prevention

The system implements **multiple layers of timeout protection** to prevent editor crashes:

```
┌─────────────────────────────────────────────────────────────────┐
│                    Error Handling Architecture                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Layer 1: Game Thread Timeout (game_thread.py)            │  │
│  │                                                          │  │
│  │ run_on_game_thread_sync(fn, max_wait=0.25)              │  │
│  │   - Returns ("pending", None) if timeout exceeded       │  │
│  │   - Never blocks > max_wait seconds                      │  │
│  │   - Prevents UI freeze during scene collection           │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                   │
│                              ▼                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Layer 2: Remote Control HTTP Timeout                     │  │
│  │                                                          │  │
│  │ REMOTE_CONTROL_TIMEOUT_SEC = 2.5                        │  │
│  │   - Timeout for each HTTP request                        │  │
│  │   - Fallback to legacy port if primary fails             │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                   │
│                              ▼                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Layer 3: WebSocket RPC Timeout (frontend.js)             │  │
│  │                                                          │  │
│  │ RPC_TIMEOUT_MS = 20000 (20 seconds)                     │  │
│  │   - Rejects pending promise after timeout               │  │
│  │   - Logs error message to user                          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                   │
│                              ▼                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Layer 4: Graceful Degradation                            │  │
│  │                                                          │  │
│  │ - Returns cached scene if current is stale              │  │
│  │ - Falls back to demo mode if API key missing            │  │
│  │ - Displays diagnostic message with next steps          │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Key Components Explained

### 4.1 Scene Perception (EditorActorSubsystem)

The **Scene Perception** module collects actor data from the Unreal Editor for AI analysis.

```python
# scene_perception.py - Core implementation

def get_scene_context() -> Dict[str, Any]:
    """Read scene via Unreal EditorActorSubsystem first, then Remote Control fallback."""
    
    # Try EditorActorSubsystem (primary method - fastest)
    local_scene = _get_scene_context_via_unreal()
    if isinstance(local_scene, dict):
        return local_scene
    
    # Fallback: Remote Control HTTP API
    try:
        raw_actors = _rc_object_call(
            "/Script/Engine.Default__GameplayStatics",
            "GetAllActorsOfClass",
            {"ActorClass": "Class'/Script/Engine.Actor'"},
        )
        # ... process actors
        return scene
    except Exception as exc:
        return {"actors": [], "source": "unavailable", "count": 0}
```

#### How EditorActorSubsystem Works

1. **Import Unreal Module** - Access UE Python API
2. **Get Subsystem** - `unreal.get_editor_subsystem(unreal.EditorActorSubsystem)`
3. **Call Function** - `subsystem.get_all_level_actors()`
4. **Snapshot Each Actor** - Extract name, class, path, location

```python
# Key function: _snapshot_unreal_actor()
def _snapshot_unreal_actor(actor: Any) -> Dict[str, Any]:
    actor_path = str(_call0(actor, "get_path_name") or "").strip()
    actor_name = str(_call0(actor, "get_actor_label") or "").strip()
    location = _vector_to_dict(_call0(actor, "get_actor_location"))
    actor_class = str(_call0(actor, "get_class") or "").strip()
    
    return {
        "name": actor_name,
        "class": actor_class,
        "path": actor_path,
        "location": location,
    }
```

#### Data Enrichment

After collection, `_enrich_scene_data()` adds statistics:

```python
scene["lights"] = lights           # All actors with "light" in name/class
scene["cameras"] = cameras         # Camera + CineCamera actors
scene["stats"] = {
    "actors": len(actors),
    "lights": len(lights),
    "static_meshes": static_meshes,
}
scene["updated_at"] = datetime.now(timezone.utc).isoformat()
```

### 4.2 Autonomous Self-Healing

The system can **auto-diagnose and propose fixes** for scene perception failures.

```
┌─────────────────────────────────────────────────────────────────┐
│                 Autonomous Self-Healing Flow                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Detection Phase                                                │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ _build_missing_scene_context_diagnostics()               │   │
│  │   - Probe EditorActorSubsystem availability              │   │
│  │   - Probe Remote Control API reachability               │   │
│  │   - Generate actionable next-step instructions          │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│                              ▼                                   │
│  Proposal Phase                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ _build_scene_perception_fix_proposal()                   │   │
│  │   - Analyze current scene_perception.py code            │   │
│  │   - Generate replacement with fallback collectors       │   │
│  │   - Create backup (.py.bak) before applying             │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│                              ▼                                   │
│  Application Phase (Requires User Confirmation)                │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ _apply_scene_perception_fix(proposed_content)            │   │
│  │   - Write new content to scene_perception.py            │   │
│  │   - Keep backup file for rollback                       │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**AI Capability**: The system can modify its own source code (`scene_perception.py`) to add fallback collectors (`EditorLevelLibrary`) when `EditorActorSubsystem` fails.

### 4.3 Configuration Management

Two files manage configuration:

#### config.json (Runtime Ports)

```json
{
  "mcp_websocket_port": 30020,
  "remote_control_http_port": 30010,
  "legacy_port": 30000
}
```

#### runtime_config.py (Access Layer)

```python
def load_runtime_config() -> Dict[str, int]:
    """Load from config.json with validation."""
    path = _config_path()  # /Plugins/AIRD/config.json
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    # Validate port range: 1024-65535
    return validate_config(parsed)

def save_runtime_config(raw: Dict[str, Any]) -> Dict[str, int]:
    """Save validated config to disk."""
    validated = validate_config(raw)
    path.write_text(json.dumps(validated, indent=2))
    return validated
```

**Usage in server.py**:

```python
cfg = load_runtime_config()
port = cfg.get("mcp_websocket_port", DEFAULT_CONFIG["mcp_websocket_port"])
# Used in WebSocket server startup
```

---

## 5. Technical Challenges & Solutions

### 5.1 Context Window Limit

| Challenge | Solution |
|-----------|----------|
| **Problem**: Large scenes exceed LLM context window (128K tokens) | Implemented token budget management |
| **Implementation**: | |
| - Scene truncation | `scene.get("actors", [])[:300]` - Limit to 300 actors |
| - Image size reduction | Base64 encoding with compression |
| - Context server memory trim | Call `/maintenance/trim-memory` before large requests |

```python
# server.py - Token budget management
scene_payload = {
    "actors": scene.get("actors", [])[:300],  # Limit actors
    "source": scene.get("source", "unknown"),
}
```

### 5.2 Deprecated Functions (UE 5.7)

| Deprecated API | Replacement |
|---------------|-------------|
| `unreal.EditorActorSubsystem.get_all_actors()` | `get_all_level_actors()` |
| `unreal.Actor.get_actor_label()` (old) | `get_actor_label()` (still works) |

**Detection Code**:
```python
def _probe_editor_actor_subsystem_status() -> Dict[str, Any]:
    subsystem_getter = getattr(unreal, "get_editor_subsystem", None)
    subsystem_class = getattr(unreal, "EditorActorSubsystem", None)
    if not callable(subsystem_getter) or subsystem_class is None:
        return {"available": False, "detail": "EditorActorSubsystem not exposed"}
    
    get_all_level_actors = getattr(subsystem, "get_all_level_actors", None)
    if not callable(get_all_level_actors):
        return {"available": False, "detail": "get_all_level_actors unavailable"}
    
    return {"available": True, "detail": "OK"}
```

### 5.3 Game Thread Blocking

| Problem | Solution |
|---------|----------|
| Python calling Unreal API from non-game thread causes crash | `game_thread.py` uses `register_slate_post_tick_callback` |

```python
# game_thread.py - Non-blocking game thread execution
def run_on_game_thread_sync(fn, *, max_wait=0.25):
    try:
        return ("ok", fn())  # Try immediate execution
    except RuntimeError as exc:
        if not _is_thread_guard_error(exc):
            raise
    
    # Deferred execution via post-tick callback
    handle = unreal.register_slate_post_tick_callback(on_post_tick)
    if done.wait(max_wait):
        return ("ok", result[0])
    return ("pending", None)  # Timeout - don't block
```

### 5.4 WebSocket Connection Reliability

| Problem | Solution |
|---------|----------|
| Connection drops during long operations | Auto-reconnect with exponential backoff |

```javascript
// frontend.js - Reconnection logic
var RECONNECT_BASE_MS = 1000;
var RECONNECT_MAX_MS = 5000;

Klass.prototype.scheduleRpcReconnect = function () {
    var attempts = this.rpcReconnectAttempts + 1;
    var delay = Math.min(RECONNECT_MAX_MS, RECONNECT_BASE_MS * attempts);
    setTimeout(() => this.connectRpc(true), delay);
};
```

### 5.5 Missing API Key Handling

| Problem | Solution |
|---------|----------|
| User hasn't configured API key | Graceful fallback to demo mode |

```python
# server.py - Demo mode fallback
if provider_id not in ("ollama", "lmstudio") and not api_key:
    return {
        "ok": False,
        "message": f"Missing API key for {provider_name}.",
        "provider": provider_id,
        "scene": scene,
        "actions": [],
    }
```

---

## Appendix: File Summary Table

| File | Lines | Purpose |
|------|-------|---------|
| `server.py` | 1500+ | Main MCP server with AI integration |
| `mcp_server.py` | 160 | Thread management for MCP |
| `scene_perception.py` | 368 | Scene data collection |
| `runtime_config.py` | 64 | Port configuration |
| `game_thread.py` | 87 | Game thread execution helper |
| `AIRDPro.html` | 533 | Main UI with AIRDApp class |
| `frontend.js` | 407 | WebSocket RPC patch |
| `AIRDBridge.h` | 34 | C++ Blueprint functions |

---

## End of Documentation

*This document provides comprehensive architectural guidance for the AIRD plugin. For additional technical details, refer to inline code comments in each module.*
