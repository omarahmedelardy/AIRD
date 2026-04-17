# AIRD Unreal Plugin — Deep Engineering Review

## 1. Project Overview

**Project Name:** AIRD (AI-Ready Development Assistant for Unreal Editor)  
**Type:** Unreal Editor plugin + Python MCP server + Web UI + runtime bridge

### What AIRD is trying to solve

AIRD is designed to let a developer issue natural-language commands from an in-Editor web panel and have them executed as real Unreal actions (scene, blueprint, code, content operations), not only AI chat responses.

### Why it was built

The intent is to move from “chat assistant that explains” to “editor assistant that executes”:
- Understand project/runtime context
- Route command to the right domain agent
- Execute through Unreal runtime bridge when Unreal APIs are required
- Return structured actionable feedback (result + next_step + diagnostics)

### What makes AIRD different from generic ChatGPT-like usage

- It has **local execution paths** (runtime bridge + `UAIRDBridge`) instead of pure textual advice.
- It has **deterministic routing and parsers** for specific command classes.
- It maintains **runtime status semantics** (`mcp_online`, `unreal_runtime_connected`, `capability_ready`).
- It can return execution-state contracts rather than generic conversational answers.

---

## 2. System Architecture

### High-level layers

- **UI Layer:** `008.html` (active page loaded by Unreal tab) + optional `frontend.js` patcher
- **Server Layer:** `Content/Python/server.py` (JSON-RPC + command execution + status)
- **Orchestrator:** `Content/Python/agents/orchestrator.py` (keyword/deterministic routing)
- **Agents:**
  - `sceneagent` → scene operations
  - `blueprintagent` → deterministic blueprint parse/execute states
  - `codeagent` → project-aware code workflow
  - `contentagent` → `/Game` content operations (folder/create-asset-placeholder)
- **Runtime Bridge:** filesystem queue bridge between external MCP process and Unreal runtime worker:
  - client: `unreal_runtime_bridge_client.py`
  - worker in Unreal: `unreal_runtime_bridge.py`
  - bootstrap from plugin startup: `Source/AIRD/Private/AIRDModule.cpp`
- **Unreal C++ bridge:** `Source/AIRD/Private/AIRDBridge.cpp`

### Text flow

`UI -> server.py -> orchestrator -> domain agent -> runtime_bridge_client -> queue files -> unreal_runtime_bridge worker -> UAIRDBridge/editor APIs -> response -> UI`

### Important runtime paths

- Runtime bridge root path (actual):  
  `Plugins/AIRD/memory/runtime_bridge/`
- Expected artifacts:
  - `heartbeat.json`
  - `requests/*.json`
  - `responses/*.json`

---

## 3. Execution Flow (Real Behavior)

### A) `analyze the scene`

1. UI sends command (`UI_REQUEST_RECEIVED` in `008.html` trace logic).
2. `server.py::execute_command` logs `SERVER_RECEIVED`.
3. Orchestrator picks route (`sceneagent`) and logs `ORCHESTRATOR_ROUTE_SELECTED`.
4. If deferred scene pipeline and command is runtime-scene command, server calls runtime bridge directly:
   - `call_runtime_bridge("get_scene_context", ...)`
5. Unreal worker executes `_execute_get_scene_context` (editor APIs first).
6. Response returns with source trace and actor count.

### B) `create folder in /Game`

1. Orchestrator scores `contentagent`.
2. `ContentAgent.process()` runs deterministic `parse_content_command(...)`.
3. Action `create_content_folder` calls runtime bridge method `create_content_folder`.
4. Unreal worker uses `EditorAssetLibrary.make_directory`.
5. Returns success/failure structured; no provider API key path required for this local operation.

### C) `add blueprint variable`

1. Route should be `blueprintagent`.
2. `BlueprintAgent` deterministic parser (`parse_blueprint_command`) builds action payload.
3. Agent calls `add_variable_to_blueprint(...)` -> runtime bridge -> `UAIRDBridge::AddBlueprintVariable`.
4. C++ side validates path/name/type, prevents duplicate, compiles blueprint, returns explicit error code via `GetLastBlueprintEditError`.

### Where parse/routing/execution/fallback happen

- **Parsing:** inside domain agents parsers (`blueprint_parser.py`, `content_parser.py`)
- **Routing:** `RequestOrchestrator.route/process`
- **Execution:** agent-specific local logic + runtime bridge + C++ bridge
- **Fallback:** still exists in `server.py` scene/LLM pipeline and provider-dependent path when command is not handled by a direct local executor

---

## 4. Phases Breakdown (Phase 1 -> Phase 8)

Based on `specs/007-aura-grade-editor-assistant/tasks.md` + `spec.md`.

### Phase 1 (Foundation Contracts)
- Implemented: T033-T035 marked complete.
- Outcome: unified response contract + compatibility notes.
- Assessment: good foundation; contract-first approach is clear.

### Phase 2 (Editor-first Scene Strategy)
- Implemented: T036-T039 complete.
- Outcome: source order trace + fallback policy.
- Assessment: mostly correct; editor-first intent is visible in code.

### Phase 3 (Project Context Layer)
- Implemented: T040-T043 complete.
- Outcome: project context model/collector/endpoint + tests.
- Assessment: practical and additive.

### Phase 4 (Blueprint Workflow Reliability)
- Implemented: T044-T047 complete.
- Outcome: parse/validate/execute/verify/report state model + integration tests.
- Assessment: major improvement in deterministic behavior and error mapping.

### Phase 5 (Code Workflow Reliability)
- Implemented: T048-T051 complete.
- Outcome: structured code outputs + targeting + scan guards + tests.
- Assessment: stronger bounded behavior under large scans.

### Phase 6 (Structured UI/UX + Status)
- Implemented: T052-T055 complete.
- Outcome: structured payload rendering + status semantics tests.
- Assessment: semantics improved, but UI complexity still risks drift.

### Phase 7 (Performance/Reliability hardening)
- Implemented: T056-T059 complete.
- Outcome: SLO/budget docs + lightweight cache + retry/backoff + regression tests.
- Assessment: practical hardening added.

### Phase 8 (Verification/closure)
- `spec.md` records conditional-go decision (T063).
- `tasks.md` currently shows T062/T063 checked, but T060/T061 unchecked.
- Assessment: closure intent documented, but task checklist state is inconsistent and should be normalized.

---

## 5. Current Capabilities (What works now vs partial vs fallback)

### Scene Analysis
- Works through editor-first chain with runtime bridge fallback/selection trace.
- Real logs show repeated successful runtime bridge scene calls.
- Limitation: current sessions can return `actor_count=0` while status still resolves to `ready`.

### Blueprint Execution
- Real C++ bridge support exists:
  - `AddBlueprintVariable`
  - `AddBlueprintFunction`
- Validation + compile + duplicate checks implemented.
- Proper explicit failures (`invalid_name`, `duplicate_name`, `compile_failed`, `editor_only`, etc.).

### Code Workflow
- Structured output contract implemented.
- Project-context aware targeting + reliability guards exist.
- Provider-dependent path still exists for LLM-heavy reasoning.

### Content Creation (`/Game`)
- Implemented minimal real operation:
  - `create_content_folder` (actual Unreal execution)
- `create asset/file` currently explicit unsupported placeholder (not fake success).

### Fallback behavior (still present)
- Generic provider chat path still used for unhandled/non-deterministic commands.
- If provider key missing in that path, returns provider/API-key-related failures.

---

## 6. Failure Analysis (Critical)

## 6.1 Routing issues / generic chat fallback

- **Symptom:** some Unreal-intent messages still get conversational/general handling.
- **Where:** `server.py::execute_command` fallback LLM path after deferred agent path.
- **Root cause:** not every Unreal-intent phrase maps to deterministic parser/agent action; unparsed/deferred flow eventually reaches provider gate.
- **Impact:** user sees non-execution behavior for commands expected to execute.

## 6.2 Runtime bridge/status inconsistencies

- **Symptom:** runtime can be connected but UI/behavior appears ambiguous in edge cases.
- **Where:** status assembly in `server.py` (`_runtime_status_snapshot` logic) and UI rendering in `008.html`.
- **Root cause:** “connected” and “capability_ready” can appear optimistic when scene source is valid transport-wise but context value is low (`actor_count=0`).
- **Impact:** perceived mismatch between technical connection and practical readiness.

## 6.3 Scene context `actor_count=0` ambiguity

- **Symptom:** repeated scene success with zero actors from runtime bridge.
- **Where:** logs + runtime bridge scene methods (`unreal_runtime_bridge.py::_execute_get_scene_context`).
- **Root cause:** zero actors is treated as valid if source is editor-native.
- **Impact:** valid for empty levels, but can mask user expectation failures in non-empty scenes.

## 6.4 Null world context warnings (historical path still risky)

- **Symptom:** warnings about null world context were observed earlier.
- **Where risk remains:** `Content/Python/scene_analysis/scene_scanner.py` still calls `unreal.GameplayStatics.get_all_actors_of_class`.
- **Root cause:** API needs valid world context in some execution contexts.
- **Impact:** if these paths are exercised, warnings/errors can reappear.

## 6.5 API-key gate on local-intent commands (partially fixed)

- **Current:** local scene/blueprint/content execution paths now largely bypass provider key when deterministic.
- **Residual risk:** any command that misses deterministic route still falls into provider path and can fail on missing API key.

## 6.6 UI path complexity / mixed active-legacy code

- `AIRDWidget.cpp` loads root `008.html` first, then fallback `Content/UI/008.html`.
- `008.html` is large and injects multiple legacy/patch layers including dynamic loading of `frontend.js`.
- This increases risk of status/render behavior divergence between environments.

---

## 7. Logs & Runtime Signals Analysis

From `Content/Python/AIRD_MCP.log`:

- Confirmed:
  - Runtime bridge forwarding works (`Runtime bridge command forwarded`).
  - Runtime bridge responds (`ok=True`).
  - Source is editor-native runtime bridge (`runtime_bridge_editor_actor_subsystem`).
  - Heartbeat-based connection is active (`runtime_bridge.connected=true`).
- Observed concern:
  - Many snapshots show `actor_count=0` while status still returns:
    - `runtime_ready=true`
    - `capability_ready=true`
    - `status_state=ready`
- Interpretation:
  - **Flow is alive** (not disconnected), but readiness semantics may overstate operational readiness in some user expectations.
- Additional signal:
  - Periodic scene sync retries to context server show transient network failures (handled by retry policy).

---

## 8. AI vs Local Execution Separation

### Should be local (Unreal-dependent)
- Scene acquisition inside editor/runtime bridge
- Blueprint mutations
- Content browser folder creation

### Should be AI/provider-dependent
- Open-ended reasoning/chat
- Non-deterministic plan generation where no direct command parser route exists

### Current separation quality
- Improved significantly via deterministic agents and runtime bridge routes.
- Still imperfect because unresolved intents can drift into generic/provider path.
- Recommendation: stronger deterministic intent thresholds before allowing chat fallback for Unreal-targeted language.

---

## 9. Architectural Weaknesses

1. **Monolithic `server.py`**
- Too many responsibilities in one file (routing, contracts, status, scene, provider fallback, RPC methods).
- Harder to reason/test regressions by responsibility boundary.

2. **UI complexity in single `008.html`**
- Very large script with additive legacy patches and optional frontend overrides.
- High chance of subtle behavior drift and difficult debugging.

3. **Dual-path/legacy overlap**
- Root vs `Content/UI` file resolution + runtime script loading fallback.
- Increases ambiguity on what code is actually active during manual tests.

4. **Status semantics coupling**
- Connection status and capability status can be optimistic under sparse scene data.
- Needs stricter distinction between transport connectivity and operational readiness for user tasks.

5. **Partial execution matrix**
- Content operations only partially implemented (folder only, asset creation unsupported).
- Good that unsupported is explicit, but intent coverage is still limited.

---

## 10. Recommendations (Critical)

### 10.1 Make Unreal-intent routing deterministic-first
- Add a strict pre-classifier: if command contains strong Unreal/action tokens (`/Game`, blueprint verbs, scene control verbs), force executable route or explicit execution failure.
- Prevent such requests from reaching generic conversational denial.

### 10.2 Harden readiness semantics
- Separate:
  - transport connected
  - runtime connected
  - scene operational
  - capability readiness per domain
- Do not present `ready` as a global status when scene signal quality is low unless explicitly intended.

### 10.3 Lock active UI path
- Pick one canonical active UI file and remove runtime fallback ambiguity in production profile.
- Keep legacy path only for dev switch with explicit flag.

### 10.4 Continue narrowing `server.py`
- Without broad refactor now: extract one module at a time (status builder, route executor, provider gateway).
- Preserve contracts and keep additive changes.

### 10.5 Expand deterministic content operations
- Keep current minimal scope but add next concrete operation with real runtime execution (e.g., asset creation by explicit class/template) or maintain explicit unsupported with guided next_step.

### 10.6 Keep strict “no fake success”
- Current direction is correct; continue normalizing all bridge/raw failures to explicit machine-readable error codes.

---

## 11. Readiness Assessment

**Current grade:** **Late Beta / Conditional Go**, not production-ready yet.

### Why not production-ready
- Connected live Unreal smoke remains a gating condition in spec closure.
- Some user-facing intents can still slip to generic/provider fallback.
- Status semantics can still be perceived as misleading in edge runtime contexts.
- Active-vs-legacy UI overlap increases operational risk.

### What blocks full launch
1. Final live Unreal connected smoke verification on real workflow paths.
2. Stronger deterministic guard against generic chat fallback for executable Unreal intents.
3. Small cleanup of status semantics and UI path ambiguity.

---

## 12. Final Summary

### Overall

AIRD has evolved from a mixed toolchain into a much stronger execution-oriented assistant architecture: deterministic agents, runtime bridge execution, explicit contracts, and broad regression coverage. The core direction is correct and materially better than generic chat-only assistants.

### Top 3 current problems

1. **Some executable intents can still fall into generic/provider path** instead of deterministic execution/failure.
2. **Readiness semantics may overstate capability** when runtime is connected but scene signal is weak (`actor_count=0` context).
3. **Operational complexity from mixed UI/legacy loading paths** can cause behavioral inconsistency across environments.

### Top 3 highest-value next improvements

1. Enforce Unreal-intent execution policy: executable route or explicit structured failure, never generic denial.
2. Tighten runtime/capability status semantics and UI badges to remove ambiguity.
3. Stabilize active UI/runtime path (single source of truth) and keep legacy under explicit opt-in.

---

## Code Anchors Referenced

- `Content/Python/server.py`
- `Content/Python/agents/orchestrator.py`
- `Content/Python/agents/blueprint_agent.py`
- `Content/Python/agents/content_agent.py`
- `Content/Python/agents/content_parser.py`
- `Content/Python/unreal_runtime_bridge.py`
- `Content/Python/unreal_runtime_bridge_client.py`
- `Content/Python/scene_perception.py`
- `Content/Python/scene_analysis/scene_scanner.py`
- `Source/AIRD/Private/AIRDBridge.cpp`
- `Source/AIRD/Private/AIRDModule.cpp`
- `Source/AIRDEditor/Private/AIRDWidget.cpp`
- `Content/UI/008.html`
- `Content/UI/frontend.js`
- `specs/007-aura-grade-editor-assistant/spec.md`
- `specs/007-aura-grade-editor-assistant/tasks.md`
- `Content/Python/AIRD_MCP.log`
