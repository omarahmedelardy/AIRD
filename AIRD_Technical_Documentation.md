# AIRD Technical Documentation

## AIRD - Autonomous AI for Unreal Engine

### Table of Contents

1. [Project Overview](#project-overview)
2. [Architecture](#architecture)
3. [Directory Structure](#directory-structure)
4. [Core Modules](#core-modules)
5. [Scene Analysis Module](#scene-analysis-module)
6. [API & Integration](#api--integration)
7. [MCP Protocol](#mcp-protocol)
8. [UI Components](#ui-components)

---

## 1. Project Overview

AIRD is an Unreal Engine plugin that provides autonomous AI capabilities for scene analysis, asset management, and intelligent command execution. The plugin integrates with Large Language Models (LLMs) to enable natural language interactions with the Unreal Editor.

**Key Features:**
- Scene context analysis and scanning
- AI-powered command execution
- Real-time viewport capture
- Knowledge graph generation
- Blueprint generation from prompts

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     AIRD Architecture                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐     ┌──────────────┐     ┌─────────────┐  │
│  │   UI Layer   │────▶│  MCP Server │────▶│  Unreal    │  │
│  │ (AIRDPro.html)     │  (Python)   │     │  Engine    │  │
│  └──────────────┘     └──────────────┘     └─────────────┘  │
│         │                     │                    │        │
│         │              ┌──────▼──────┐              │        │
│         │              │ Scene       │              │        │
│         │              │ Analysis    │              │        │
│         │              │ Module      │              │        │
│         │              └─────────────┘              │        │
│         │                     │                    │        │
│         │              ┌──────▼──────┐              │        │
│         │              │ LLM         │              │        │
│         │              │ Integration │              │        │
│         │              │ (OpenAI,    │              │        │
│         │              │  Anthropic) │              │        │
│         │              └─────────────┘              │        │
│         │                                          │        │
│  ┌──────▼──────┐                                 │        │
│  │ WebSocket   │◀────────────────────────────────┘        │
│  │ Connection  │                                         │
│  └─────────────┘                                         │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Directory Structure

```
AIRD/
├── Content/
│   ├── Python/
│   │   ├── __init__.py
│   │   ├── server.py              # Main MCP server
│   │   ├── mcp_server.py          # MCP protocol handler
│   │   ├── scene_perception.py    # Scene data collection
│   │   ├── scene_analysis/        # NEW: Scene scanning module
│   │   │   ├── __init__.py
│   │   │   ├── scene_scanner.py   # Actor scanning
│   │   │   ├── actor_categorizer.py
│   │   │   ├── light_analyzer.py
│   │   │   ├── scene_processor.py
│   │   │   ├── scene_query_api.py
│   │   │   ├── scene_visualization.py
│   │   │   ├── scene_cache.py
│   │   │   └── test_scene_analysis.py
│   │   ├── blueprint_generator.py
│   │   ├── knowledge_graph.py
│   │   ├── runtime_config.py
│   │   ├── game_thread.py
│   │   └── run_utils.py
│   │
│   └── UI/
│       ├── AIRDPro.html           # Main UI
│       └── 008.html
│
├── specs/
│   └── 006-scene-context-analysis/
│       ├── spec.md
│       ├── plan.md
│       └── tasks.md
│
└── (Unreal Engine plugin files)
```

---

## 4. Core Modules

### 4.1 server.py

**Purpose:** Main MCP (Model Context Protocol) server that handles all communications.

**Key Functions:**
- WebSocket server initialization
- JSON-RPC command handling
- LLM provider integration (OpenAI, Anthropic, OpenRouter, etc.)
- Scene context management

**Port Configuration:**
- Default MCP WebSocket port: `8765`
- Context server URL: `http://127.0.0.1:8787`

### 4.2 scene_perception.py

**Purpose:** Collects scene context from Unreal Editor.

**Key Functions:**
- `get_scene_context()` - Main entry point
- `_get_scene_context_via_unreal()` - Uses EditorActorSubsystem
- Viewport capture for visual context

### 4.3 knowledge_graph.py

**Purpose:** Builds spatial relationships between scene objects.

### 4.4 blueprint_generator.py

**Purpose:** Generates Unreal blueprints from text prompts.

---

## 5. Scene Analysis Module

### Overview

The Scene Analysis module (`scene_analysis/`) provides comprehensive scene scanning capabilities with visual confirmation.

### Files and Responsibilities

| File | Responsibility |
|------|-----------------|
| `scene_scanner.py` | Core actor scanning, world initialization, visual selection |
| `actor_categorizer.py` | Actor classification into 8 categories |
| `light_analyzer.py` | Light property extraction |
| `scene_processor.py` | Main processor, JSON generation |
| `scene_query_api.py` | Public API with MCP tools |
| `scene_visualization.py` | Visualization data generation |
| `scene_cache.py` | Caching and incremental scanning |
| `test_scene_analysis.py` | Unit tests |

### Actor Categories

```python
class ActorCategory(Enum):
    LIGHT = "Light"           # All light types
    STATIC_MESH = "StaticMesh"  # Static 3D meshes
    DYNAMIC_ACTOR = "DynamicActor"  # Movable actors
    VOLUME = "Volume"         # Volume actors
    PLAYER = "Player"         # Player controllers/pawns
    CAMERA = "Camera"         # Camera actors
    AUDIO = "Audio"           # Audio actors
    OTHER = "Other"           # Everything else
```

### Key Features

1. **World Initialization** (3 fallback methods):
   - EditorSubsystem (most reliable)
   - EditorLevelLibrary
   - Level enumeration

2. **Visual Confirmation:**
   - Uses `set_selected_level_actors()` for selection glow
   - Selects meshes + lights for visual feedback

3. **Caching:**
   - TTL-based cache (default 30 seconds)
   - Dirty flag tracking for change detection
   - Incremental scan support

### Usage

```python
from scene_analysis import SceneQueryAPI

api = SceneQueryAPI()
summary = api.get_quick_summary()
lights = api.get_all_lights()
```

---

## 6. API & Integration

### MCP Tools (RPC Methods)

| Method | Description |
|--------|-------------|
| `scan_scene` | Full scene scan with visual confirmation |
| `get_scene_lights` | Get all lights with properties |
| `get_scene_actors` | Get actors by category |
| `get_scene_bounds` | Get scene spatial bounds |
| `get_scene_quick_summary` | Quick stats summary |
| `get_scene_pie_chart` | Visualization data |
| `execute_command` | AI command execution |

### Command Patterns

Natural language commands supported:
- "scan the scene" → `scan_scene`
- "show me the lights" → `get_scene_lights`
- "scene summary" → `get_scene_quick_summary`

### JSON-RPC Response Format

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "total_actors": 150,
    "actor_counts": {
      "StaticMesh": 80,
      "Light": 5
    },
    "visual_confirmation": {
      "actors_selected": 100,
      "actors_found": 150,
      "meshes_found": 80,
      "lights_found": 5
    },
    "scan_duration_ms": 45.2
  }
}
```

---

## 7. MCP Protocol

### Connection Flow

```
Client                    Server (AIRD)
   |                          |
   |──── WebSocket Connect ───▶|
   |                          |
   |──── JSON-RPC Request ───▶|
   |   {method: "scan_scene"} |
   |                          |
   |◀─── JSON-RPC Response ───|
   |   {result: {...}}        |
   |                          |
```

### Error Handling

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32000,
    "message": "AIRD: Failed to get editor world"
  }
}
```

---

## 8. UI Components

### AIRDPro.html

**Main Interface Features:**
- Chat input for natural language commands
- Viewport canvas with capture
- Scene visualization widget
- Model selection panel
- API configuration

### Scene Visualization Widget

**Displays:**
- Pie chart of actor distribution
- Total actor count
- Light count
- Static mesh count
- Selected (glow) count
- Scan time

### Visual Confirmation

When scanning:
1. Gets all actors from level
2. Selects first 100 for editor selection
3. Actors glow with selection outline (green/orange)
4. Stats displayed in UI

---

## 9. Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AIRD_CONTEXT_SERVER_URL` | `http://127.0.0.1:8787` | Context server URL |
| `AIRD_SCENE_SYNC_INTERVAL` | `2.0` | Scene sync interval (seconds) |
| `AIRD_HEARTBEAT_INTERVAL` | `0` | Heartbeat interval |
| `OPENAI_API_KEY` | - | OpenAI API key |

### Runtime Config

```json
{
  "mcp_websocket_port": 8765,
  "remote_control_http_port": 30010,
  "legacy_port": 30000,
  "enable_agent_selector_ui": false,
  "enable_history_ui": false
}
```

### Phase 6 (US4) Optional Controls

- Optional controls are **disabled by default** and are driven by `get_runtime_config`.
- `enable_agent_selector_ui=true` shows `agentSelector` in Settings and forwards `agent_override` for command routing.
- `enable_history_ui=true` shows History quick button + History tab and enables:
  - `get_history`
  - `search_history`
  - `clear_history` (may require RPC mutation authorization token)
- When flags are disabled, UI behavior is graceful no-op:
  - controls are hidden/disabled
  - History tab auto-falls back to Models
  - no hidden fallback execution path is triggered
- `frontend.js` now skips legacy RPC patching when modern UI (`verifyMcpProtocol`) is detected to avoid overriding active runtime behavior in `008.html`.

### Phase 1 Capability Matrix (T034)

`get_runtime_status` is the runtime-status source for UI/backend compatibility and should be treated as a capability matrix.

#### Capability Fields

| Field | Meaning | Client Behavior |
|-------|---------|-----------------|
| `mcp_online` | MCP server health | If `false`, treat all actions as unavailable |
| `unreal_runtime_connected` | Unreal session bridge/local runtime availability | Required for Unreal-dependent commands |
| `runtime_connection_mode` | `local_airdb` / `runtime_bridge_queue` / `none` | Show execution mode in diagnostics |
| `unreal_python_available` | Unreal Python import availability in current runtime | Advisory for local runtime-only paths |
| `airdb_bridge_available` | AIRDBridge availability in Unreal Python | Enables direct bridge operations |
| `runtime_bridge_connected` | Queue/heartbeat connection state | Enables Unreal execution when server is external |
| `scene_context_valid` | Scene readiness (source + schema valid) | Required for scene-dependent actions |
| `scene_source` | Active scene source token | Display in diagnostics and source trace |
| `scene_provider_layer` | Provider class (`unreal_runtime`, `remote_control_api`, etc.) | Debug routing/fallback decisions |
| `actor_count` | Scene actor count | Informational only; not sole readiness signal |
| `runtime_ready` | Composite readiness (`unreal_runtime_connected && scene_context_valid`) | Gate scene+runtime actions |
| `status` | Human-readable status summary | UI badge/label text |

#### Status Semantics

- `runtime_ready=true` means AIRD can run scene-aware Unreal actions reliably.
- `unreal_runtime_connected=true` with `scene_context_valid=false` means runtime exists but scene provider is not ready yet.
- `scene_context_valid=true` with `unreal_runtime_connected=false` means scene data exists but runtime mutation path is unavailable.
- `actor_count=0` does **not** imply failure by itself when source/provider is valid editor-native.
- Capability-limited responses should return explicit `status="partial"` or `error_code` instead of synthetic success.

#### Backward Compatibility Policy

- Existing clients can continue reading legacy fields (`status`, `scene_source`, `actor_count`) without breakage.
- New fields are additive; unknown fields must be ignored by older clients.
- Runtime status consumers should prefer capability flags over heuristic checks.

---

### Phase 1 UI Compatibility Notes (T035)

#### Active UI Path Resolution

- `Source/AIRDEditor/Private/AIRDWidget.cpp` loads UI in this order:
  - `008.html` (plugin root, preferred)
  - `Content/UI/008.html` (fallback)
- Both `008.html` variants attempt runtime bridge script loading in this order:
  - `frontend.js`
  - `Content/UI/frontend.js`
  - `../frontend.js`
- `frontend.js` is compatibility-only for legacy UI classes and **must not** override modern runtime wiring:
  - when `verifyMcpProtocol` + `refreshRuntimeStatus` exist, patching is skipped.

#### UI-to-Contract Compatibility Map

| UI Path | RPC Methods Used | Required Fields (for compatibility) | Recommended Fields | Optional/Forward Fields |
|---------|------------------|--------------------------------------|--------------------|-------------------------|
| `008.html` (root, modern path) | `execute_command`, `get_runtime_status`, `get_runtime_config`, `get_scene_context`, `sync_scene_context` | `execute_command`: `message` (or `assistant`/`payload` fallback); `get_runtime_status`: `mcp_online`, `unreal_runtime_connected`, `scene_source`, `actor_count` | `runtime_status` in command result, `runtime_connection_mode`, `runtime_bridge.connected`, `runtime_bridge.reason`, `scene_provider_layer` | `schema_version`, `status`, `ok`, `error_code`, `next_step`, `diagnostics`, additive metadata |
| `Content/UI/008.html` (legacy fallback path) | `execute_command`, `get_scene_context`, `sync_scene_context` | `execute_command`: `message` (or `assistant`/`payload` fallback); `get_scene_context`: `actors[]`, `source` | `ok`/`status` flags and explicit `error` messages | `runtime_status` and richer diagnostics (ignored safely if present) |
| `Content/UI/AIRDPro.html` (alternate legacy UI) | `execute_command` (primary), optional mutating calls (`update_runtime_config`, `clear_history`, `apply_scene_perception_fix`) | `execute_command`: `message`; mutating RPC errors must remain explicit | `ok` + `error`/`message` for mutating calls | token transport via `auth_token` (from `window.AIRD_RPC_AUTH_TOKEN` or `localStorage`) |

#### Backward-Aware Rules

1. Command responses should keep legacy text fields (`message`, or `assistant`/`payload` aliases) while additive structured fields roll out.
2. Runtime status responses must keep legacy summary fields (`status`, `scene_source`, `actor_count`) even when capability matrix fields are present.
3. Older UIs must ignore unknown fields; server-side additions should remain additive unless a versioned breaking change is explicitly introduced.
4. Mutating RPC methods must continue accepting token via params (`auth_token`/legacy aliases) so legacy UI paths do not break when auth is enforced.

---

### Phase 6 UI Action Mapping (T052)

`get_action_response_contract` now defines a practical UI-facing mapping under `ui_response_mapping`.

#### Classification (Deterministic Order)

1. `unavailable_or_capability_limited`
2. `error`
3. `warning`
4. `partial`
5. `success`

#### State Distinctions

| State | Trigger (summary) | UI Expectation |
|-------|--------------------|----------------|
| `success` | `ok=true` and `status=success` | Show result, no blocking next step required |
| `partial` | `status=partial` | Show partial result + explicit next step + diagnostics |
| `warning` | `status=warning` or warning diagnostics | Show result with warning banner + diagnostics |
| `error` | `ok=false` or `status=error` | Show error-first card + mandatory next step + diagnostics |
| `unavailable` | `status=unavailable` or capability-limited `error_code` | Show unavailable/capability-limited card, never success styling |

#### Field-to-UI Slots

- `result` -> `action_result` (fallback: `message`)
- `next_step` -> `assistant_next_step` (required for `partial/error/unavailable`)
- `diagnostics` -> `diagnostics_panel` (fallback: `trace`, `runtime_status`)

This mapping is additive/backward-aware: legacy clients can still rely on `ok/status/message`, while modern UIs use `result + next_step + diagnostics` deterministically.

---

### Phase 6 UI Structured Consumption (T053)

- **Active UI path**: `008.html` (plugin root) selected first by `Source/AIRDEditor/Private/AIRDWidget.cpp`.
- **Legacy fallback path**: `Content/UI/008.html` only when root `008.html` is not found.

`008.html` now consumes structured command payloads safely by:
- classifying `status` + `error_code` (including `unavailable/capability-limited`),
- rendering action-oriented content from `message`, `result`, `next_step`, and `diagnostics/trace/runtime_status`,
- and falling back to legacy text fields when newer fields are missing.

When structured failure payload exists, UI shows it directly instead of forcing generic non-structured fallback rendering.

---

### Phase 6 Runtime Status Semantics (T054)

`get_runtime_status` now exposes explicit, display-friendly semantics to avoid mixing transport connectivity with runtime/capability readiness.

#### Distinctions

| Layer | Field(s) | Meaning |
|-------|----------|---------|
| MCP connectivity | `mcp_online`, `status_semantics.mcp_state` | Whether MCP endpoint itself is reachable (`connected` / `unavailable`) |
| Unreal runtime connectivity | `unreal_runtime_connected`, `status_semantics.runtime_state` | Whether Unreal execution bridge/session is available (`connected` / `unavailable`) |
| Capability readiness | `runtime_ready`, `capability_ready`, `status_semantics.capability_readiness`, `status_state` | Operational readiness (`ready` / `partially_ready` / `unavailable`) |

#### Readiness Rules

- `ready`: Unreal runtime connected **and** scene context valid.
- `partially_ready`: only one side is available (runtime without valid scene, or valid scene without runtime).
- `unavailable`: runtime unavailable and scene context unavailable.

Active UI (`008.html`) consumes these semantics directly for labeling and diagnostics; legacy UI fallback remains additive-safe.

---

### Phase 7 Performance Budgets + Reliability SLOs (T056)

This section defines **core-operation budgets** from current runtime guards/timeouts (not theoretical targets).

#### Core Budget Distinctions

- `target_latency_ms`: expected response under normal editor load (performance target).
- `hard_timeout_ms`: enforced upper bound from current timeout/guard logic.
- `degraded_mode`: behavior when target/hard conditions are missed.
- `acceptable_partial_result`: allowed partial shape without false success.

#### Core Operations Budget Table

| Operation | target_latency_ms | hard_timeout_ms | degraded_mode | acceptable_partial_result | Basis in Current Code |
|----------|-------------------|------------------|---------------|---------------------------|-----------------------|
| `runtime_status_poll` | `300` | `2000` | return `unavailable`/`partially_ready` semantics with runtime-bridge reason | `mcp_online=true` + `unreal_runtime_connected=false` is valid partial readiness | `server.py:get_runtime_status` + RC probe timeout (`2.0s`) |
| `scene_context_acquisition` | `700` | `3000` | preserve last valid editor-native snapshot as stale when fallback RC returns 0 actors | `scene_stale=true` with source trace/fallback reason | `scene_perception.py` editor-first order + runtime bridge scene timeout (`3.0s`) |
| `blueprint_runtime_mutation` | `1200` | `8000` (default) | explicit `unreal_runtime_unavailable`/`capability_limited`, never synthetic success | `status=partial` only for true partial capability outcomes | `unreal_runtime_bridge_client.REQUEST_TIMEOUT_SEC` + blueprint error normalization |
| `code_workflow_explicit_target` | `1200` | `1200` | bounded scan with `scan_guards.timeout_hit`/`truncated` | actionable workflow output + truncation metadata | `CodeAgent._mode_limits.explicit` + analyzer guards |
| `code_workflow_inferred_or_fallback` | `1800` | `3000` | bounded inferred/fallback scan, hard file limit enforced | workflow keeps `targeting` + `scan_guards` when truncated | `CodeAgent._mode_limits.inferred/fallback` + hard limit (`800`) |
| `scene_sync_to_context_server` | `6000` per attempt | `18000` total (`3` retries) | bounded retry sequence, no blocking false success in command contract | command may succeed while `sync.ok=false` with explicit error | `MAX_SCENE_SYNC_RETRIES` + `_sync_scene_snapshot` timeout (`6s`) |

#### Reliability SLO Targets (Core Only)

| SLO | Target | Window | Degraded Interpretation |
|-----|--------|--------|-------------------------|
| Runtime status freshness | `>=99%` fresh runtime snapshots | rolling 15 minutes | heartbeat stale -> runtime becomes unavailable with explicit reason |
| Structured core command response | `>=99%` responses remain structured | rolling 24 hours | failures must stay explicit (`error_code`/diagnostics), never false success |
| Bounded code scan enforcement | `100%` scans honor hard file/time guards | per request | return truncated scan metadata instead of unbounded execution |

#### RPC Contract Exposure

- Read-only method: `get_reliability_profile`
- Contract fields:
  - `operations.<op>.target_latency_ms`
  - `operations.<op>.hard_timeout_ms`
  - `operations.<op>.degraded_mode`
  - `operations.<op>.acceptable_partial_result`
  - `reliability_slos.*`
- Additive/backward-aware: no existing RPC or UI flow is broken.

---

### Phase 7 Lightweight Caching/Indexing Strategy (T057)

Scope is intentionally limited to repeated **scene** and **project context** lookups (no heavy indexer).

#### Scene Cache (in-memory, short TTL)

- Cache key scope: current MCP process memory only.
- Read path: `_safe_scene_context()` checks cache first unless `force_scene_refresh=true`.
- TTL policy:
  - `cacheable_snapshot`: `AIRD_SCENE_CONTEXT_CACHE_TTL` (default `1.2s`)
  - `stale_snapshot`: `AIRD_SCENE_CONTEXT_STALE_CACHE_TTL` (default `0.35s`)
  - `non_cacheable_response`: not cached

#### Project Context Cache (existing + clarified metadata)

- Existing in-memory TTL cache remains active via `AIRD_PROJECT_CONTEXT_CACHE_TTL` (default `10s`).
- `get_project_context` now returns additive metadata:
  - `cache_state`
  - `cache_invalidation_trigger`

#### Required Distinctions (Operational)

| Type | Meaning |
|------|---------|
| `cacheable_snapshot` | Valid payload safe for short reuse |
| `stale_snapshot` | Reusable degraded payload with explicit stale/diagnostic semantics |
| `non_cacheable_response` | Pending/unavailable/error payload; must not be reused |
| `cache_invalidation_trigger` | Why cache was bypassed/refreshed (`force_scene_refresh`, `project_context_refresh`, `ttl_expired_or_cache_miss`, `collector_error`, etc.) |

#### Lightweight Indexing Note

- Project context reuses collected structured fields (`modules/plugins/source_roots`) as lookup-ready summaries.
- No large persistent index or full project re-index pass is introduced in T057.

---

### Phase 7 Non-Editor Fallback Retry/Timeout Policy (T058)

Scope is limited to **non-editor fallback HTTP paths only**:
- Context server calls (`/health`, `/llm/chat`, `/maintenance/trim-memory`)
- Remote provider chat calls (OpenAI/OpenRouter/Anthropic/Together)

#### Timeout Normalization

- All fallback HTTP calls now pass through normalized timeout bounds:
  - min: `AIRD_FALLBACK_TIMEOUT_MIN_SEC` (default `1.0s`)
  - max: `AIRD_FALLBACK_TIMEOUT_MAX_SEC` (default `90.0s`)
- Prevents overly small/huge ad-hoc timeouts per call site.

#### Retry/Backoff Policy

- Retry budget: `AIRD_FALLBACK_RETRY_ATTEMPTS` (default `3`, capped)
- Backoff: exponential (`base=0.25s`, `max=1.5s`, no jitter)
- No random retries; retries happen only for explicitly retryable classes.

#### Failure Class Distinctions

| Class | Behavior |
|------|----------|
| `retryable_timeout` | Retry with backoff (timeouts, HTTP 408/504) |
| `transient_failure` | Retry with backoff (HTTP 429/5xx, transient socket/url failures) |
| `hard_failure` | Fail current request (non-transient transport/provider failure) |
| `immediate_no_retry_condition` | Fail fast with no retry (HTTP 401/403/404/422, invalid payload shape) |

#### Notes

- Existing editor-native execution paths are unchanged.
- Existing RPC/UI contracts remain additive-compatible; behavior is improved for fallback resilience only.

---

## 10. Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| Empty world | Ensure a level is open in Editor |
| No actors found | Check level has content |
| Connection failed | Check WebSocket port |
| API errors | Verify API keys in config |

### Debug Logging

```python
import logging
logging.getLogger("aird.mcp").setLevel(logging.DEBUG)
```

---

## 11. Technical Specifications

### Dependencies
- Python 3.11+
- Unreal Engine 5.x
- websockets library
- OpenAI/Anthropic API (optional)

### Performance
- Scene scan: ~50ms for 1000 actors
- Viewport capture: ~200ms
- LLM response: Varies by provider

### File Statistics

| Module | Lines |
|--------|-------|
| server.py | ~1900 |
| scene_analysis/ | ~1800 |
| Total Python | ~4000+ |

---

## 12. Future Enhancements

- Incremental scanning optimization
- Real-time scene watching
- Enhanced visualization
- Multi-level support
- Blueprint code generation

---

*Generated on: 2026-04-15*
*Version: 1.0.0*
