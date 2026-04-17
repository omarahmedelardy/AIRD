"use strict";

const fs = require("fs");
const path = require("path");
const http = require("http");
const https = require("https");
const crypto = require("crypto");
const { URL } = require("url");

const HOST = process.env.AIRD_CONTEXT_HOST || "127.0.0.1";
const PORT = Number(process.env.AIRD_CONTEXT_PORT || 8787);
const REQUEST_TIMEOUT_MS = Number(process.env.AIRD_CONTEXT_TIMEOUT_MS || 60000);
const MISSING_SCENE_MESSAGE = "Missing Scene Context - Cannot analyze Unreal environment.";
const PRIMARY_MEMORY_FILE =
  process.env.AIRD_MEMORY_FILE ||
  path.join(process.env.LOCALAPPDATA || __dirname, "AIRD", "context_memory.json");
let memoryFilePath = PRIMARY_MEMORY_FILE;
const MAX_TIMELINE = 120;
const PROMPT_ACTOR_LIMIT = Number(process.env.AIRD_PROMPT_ACTOR_LIMIT || 60);
const PROMPT_GRAPH_NODE_LIMIT = Number(process.env.AIRD_PROMPT_GRAPH_NODE_LIMIT || 40);
const PROMPT_MEMORY_ENTRY_LIMIT = Number(process.env.AIRD_PROMPT_MEMORY_ENTRY_LIMIT || 3);
const CONTEXT_ERROR_PATTERN = /context length exceeded|maximum context length|too many tokens|prompt is too long|context window/i;

let latestSceneSnapshot = null;
let latestSceneGraph = null;
let latestSceneUpdatedAt = 0;
let memoryTimeline = [];

function log(...args) {
  console.log("[AIRD Context V2]", ...args);
}

function loadMemory() {
  try {
    if (!fs.existsSync(memoryFilePath)) {
      memoryTimeline = [];
      return;
    }
    const parsed = JSON.parse(fs.readFileSync(memoryFilePath, "utf8"));
    memoryTimeline = Array.isArray(parsed?.timeline) ? parsed.timeline : [];
  } catch (_) {
    memoryTimeline = [];
  }
}

function saveMemory() {
  const payload = { timeline: memoryTimeline.slice(-MAX_TIMELINE) };
  const candidates = [
    memoryFilePath,
    path.join(__dirname, "context_memory.json"),
  ];

  let lastError = null;
  for (const candidate of candidates) {
    try {
      const dir = path.dirname(candidate);
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(candidate, JSON.stringify(payload, null, 2), "utf8");
      memoryFilePath = candidate;
      return;
    } catch (err) {
      lastError = err;
    }
  }

  if (lastError) {
    throw lastError;
  }
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let raw = "";
    req.on("data", (chunk) => {
      raw += chunk.toString("utf8");
      if (raw.length > 12 * 1024 * 1024) reject(new Error("Payload too large"));
    });
    req.on("end", () => {
      if (!raw.trim()) return resolve({});
      try {
        resolve(JSON.parse(raw));
      } catch (_) {
        reject(new Error("Invalid JSON payload"));
      }
    });
    req.on("error", reject);
  });
}

function writeJson(res, statusCode, payload) {
  res.statusCode = statusCode;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type,Authorization");
  res.end(JSON.stringify(payload));
}

function safeScene(scene) {
  if (!scene || typeof scene !== "object") return null;
  if (!Array.isArray(scene.actors)) return null;
  const source = String(scene.source || "").toLowerCase().trim();
  if (!source || source === "unavailable") return null;
  return scene;
}

function asNumber(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function vecFromActor(actor) {
  const loc = actor?.location || {};
  return {
    x: asNumber(loc.x, 0),
    y: asNumber(loc.y, 0),
    z: asNumber(loc.z, 0),
  };
}

function distance3(a, b) {
  const dx = asNumber(a.x) - asNumber(b.x);
  const dy = asNumber(a.y) - asNumber(b.y);
  const dz = asNumber(a.z) - asNumber(b.z);
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
}

function inferNodeType(actor) {
  const cls = String(actor?.class || actor?.type || "").toLowerCase();
  if (cls.includes("light")) return "Light";
  if (cls.includes("camera") || cls.includes("cine")) return "Camera";
  if (cls.includes("character") || cls.includes("pawn")) return "Character";
  if (cls.includes("staticmesh") || cls.includes("skeletal")) return "Mesh";
  return "Actor";
}

function inferGameplayRole(actor, nodeType) {
  const name = String(actor?.name || "").toLowerCase();
  const cls = String(actor?.class || actor?.type || "").toLowerCase();
  if (nodeType === "Light") return "Lighting";
  if (nodeType === "Camera") return "Viewpoint";
  if (name.includes("spawn") || cls.includes("spawner")) return "Spawner";
  if (name.includes("trigger") || name.includes("volume")) return "Interaction";
  if (name.includes("enemy")) return "Enemy";
  if (name.includes("player")) return "Player";
  if (name.includes("floor") || name.includes("wall")) return "Environment";
  if (nodeType === "Mesh") return "Obstacle";
  return "Generic";
}

function buildSceneGraph(scene) {
  const actors = Array.isArray(scene?.actors) ? scene.actors : [];
  const nodes = actors.map((actor) => {
    const nodeType = inferNodeType(actor);
    return {
      id: String(actor?.name || `Actor_${Math.random().toString(16).slice(2, 8)}`),
      type: nodeType,
      role: inferGameplayRole(actor, nodeType),
      class: String(actor?.class || actor?.type || "Actor"),
      position: vecFromActor(actor),
      relations: [],
      interactionPotential: "low",
    };
  });

  const edges = [];
  const nearThreshold = 500;
  const farThreshold = 2500;

  for (let i = 0; i < nodes.length; i += 1) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j += 1) {
      const b = nodes[j];
      const d = distance3(a.position, b.position);
      if (d <= nearThreshold) {
        a.relations.push(`near ${b.id}`);
        b.relations.push(`near ${a.id}`);
        edges.push({ from: a.id, to: b.id, relation: "near", distance: Math.round(d) });
      } else if (d >= farThreshold) {
        edges.push({ from: a.id, to: b.id, relation: "far", distance: Math.round(d) });
      }
    }
  }

  for (const node of nodes) {
    const hasNearbyLight = node.relations.some((r) => r.toLowerCase().includes("near") && r.toLowerCase().includes("light"));
    const hasNearbyCamera = node.relations.some((r) => r.toLowerCase().includes("near") && r.toLowerCase().includes("camera"));
    if (node.role === "Player" || node.role === "Enemy") node.interactionPotential = "high";
    if (node.role === "Interaction" || hasNearbyCamera || hasNearbyLight) node.interactionPotential = "medium";
  }

  const sceneIdBase = nodes.map((n) => `${n.id}:${n.position.x},${n.position.y},${n.position.z}`).join("|");
  const sceneId = crypto.createHash("sha1").update(sceneIdBase || "empty").digest("hex").slice(0, 16);

  return {
    scene_id: sceneId,
    nodes,
    edges,
    summary: {
      total_nodes: nodes.length,
      lights: nodes.filter((n) => n.type === "Light").length,
      cameras: nodes.filter((n) => n.type === "Camera").length,
      interactive: nodes.filter((n) => n.interactionPotential !== "low").length,
    },
  };
}

function mapById(nodes) {
  const m = new Map();
  for (const node of nodes) m.set(node.id, node);
  return m;
}

function detectSceneChanges(previousGraph, currentGraph) {
  if (!previousGraph || !Array.isArray(previousGraph.nodes)) {
    return {
      added: currentGraph.nodes.length,
      removed: 0,
      moved: 0,
      addedActors: currentGraph.nodes.map((n) => n.id).slice(0, 20),
      removedActors: [],
      movedActors: [],
    };
  }

  const prevMap = mapById(previousGraph.nodes);
  const currMap = mapById(currentGraph.nodes);

  const addedActors = [];
  const removedActors = [];
  const movedActors = [];

  for (const [id, curr] of currMap.entries()) {
    if (!prevMap.has(id)) {
      addedActors.push(id);
      continue;
    }
    const prev = prevMap.get(id);
    const d = distance3(prev.position || {}, curr.position || {});
    if (d > 1) movedActors.push({ id, distance: Math.round(d) });
  }

  for (const id of prevMap.keys()) {
    if (!currMap.has(id)) removedActors.push(id);
  }

  return {
    added: addedActors.length,
    removed: removedActors.length,
    moved: movedActors.length,
    addedActors: addedActors.slice(0, 20),
    removedActors: removedActors.slice(0, 20),
    movedActors: movedActors.slice(0, 20),
  };
}

function detectIssues(sceneGraph) {
  const issues = [];
  const nodes = Array.isArray(sceneGraph?.nodes) ? sceneGraph.nodes : [];

  if (nodes.length === 0) {
    issues.push("Scene contains no actors.");
    return issues;
  }

  const meshNodes = nodes.filter((n) => n.type === "Mesh");
  for (let i = 0; i < meshNodes.length; i += 1) {
    const a = meshNodes[i];
    for (let j = i + 1; j < meshNodes.length; j += 1) {
      const b = meshNodes[j];
      const d = distance3(a.position, b.position);
      if (d < 5) {
        issues.push(`Potential overlapping meshes: ${a.id} and ${b.id}`);
        if (issues.length >= 8) return issues;
      }
    }
  }

  const lights = nodes.filter((n) => n.type === "Light").length;
  if (lights > 24) {
    issues.push(`High light count (${lights}) may impact performance.`);
  }

  return issues;
}

function appendMemory(sceneGraph, changes, issues, fixesApplied = []) {
  const entry = {
    timestamp: new Date().toISOString(),
    scene_id: sceneGraph.scene_id,
    changes,
    issues_found: issues,
    fixes_applied: fixesApplied,
  };
  memoryTimeline.push(entry);
  if (memoryTimeline.length > MAX_TIMELINE) {
    memoryTimeline = memoryTimeline.slice(memoryTimeline.length - MAX_TIMELINE);
  }
  saveMemory();
  return entry;
}

function trimMemoryTimeline(maxEntries = 12) {
  const safeMax = Math.max(0, Number(maxEntries) || 0);
  if (safeMax === 0) {
    memoryTimeline = [];
  } else if (memoryTimeline.length > safeMax) {
    memoryTimeline = memoryTimeline.slice(memoryTimeline.length - safeMax);
  }
  saveMemory();
  return memoryTimeline.length;
}

function slimScene(scene, maxActors = PROMPT_ACTOR_LIMIT) {
  const actors = Array.isArray(scene?.actors) ? scene.actors : [];
  return {
    source: String(scene?.source || "unknown"),
    count: actors.length,
    actors: actors.slice(0, Math.max(1, maxActors)).map((actor) => ({
      name: String(actor?.name || ""),
      class: String(actor?.class || actor?.type || "Actor"),
      location: {
        x: asNumber(actor?.location?.x, 0),
        y: asNumber(actor?.location?.y, 0),
        z: asNumber(actor?.location?.z, 0),
      },
    })),
  };
}

function slimSceneGraph(sceneGraph, maxNodes = PROMPT_GRAPH_NODE_LIMIT) {
  const nodes = Array.isArray(sceneGraph?.nodes) ? sceneGraph.nodes : [];
  const edges = Array.isArray(sceneGraph?.edges) ? sceneGraph.edges : [];
  return {
    scene_id: String(sceneGraph?.scene_id || "unknown"),
    summary: sceneGraph?.summary || {
      total_nodes: nodes.length,
      lights: 0,
      cameras: 0,
      interactive: 0,
    },
    nodes: nodes.slice(0, Math.max(1, maxNodes)).map((node) => ({
      id: String(node?.id || ""),
      type: String(node?.type || "Actor"),
      role: String(node?.role || "Generic"),
      class: String(node?.class || "Actor"),
      position: {
        x: asNumber(node?.position?.x, 0),
        y: asNumber(node?.position?.y, 0),
        z: asNumber(node?.position?.z, 0),
      },
      interactionPotential: String(node?.interactionPotential || "low"),
    })),
    sample_edges: edges.slice(0, Math.max(4, maxNodes)).map((edge) => ({
      from: String(edge?.from || ""),
      to: String(edge?.to || ""),
      relation: String(edge?.relation || ""),
      distance: asNumber(edge?.distance, 0),
    })),
  };
}

function buildMemorySummary(maxEntries = PROMPT_MEMORY_ENTRY_LIMIT) {
  const safeMax = Math.max(1, Number(maxEntries) || PROMPT_MEMORY_ENTRY_LIMIT);
  const recentEntries = memoryTimeline.slice(-safeMax).map((entry) => ({
    timestamp: entry?.timestamp || "",
    scene_id: entry?.scene_id || "",
    changes: entry?.changes || {},
    issues_found: Array.isArray(entry?.issues_found) ? entry.issues_found.slice(0, 4) : [],
    fixes_applied: Array.isArray(entry?.fixes_applied) ? entry.fixes_applied.slice(0, 4) : [],
  }));
  return {
    total_entries: memoryTimeline.length,
    recent_entries: recentEntries,
    latest_scene_id: recentEntries.length ? recentEntries[recentEntries.length - 1].scene_id : null,
  };
}

function buildInjectedPrompt(payload) {
  const { scene, sceneGraph, memorySummary, userMessage, visionAttached, issues } = payload;
  return [
    "You are Unreal Autonomous Scene Intelligence Agent V2.",
    "You are not a chatbot. You must reason from provided data only.",
    "Never hallucinate actors. Never respond without scene context.",
    "Keep the answer compact and prioritize the latest scene state.",
    "",
    "Scene Graph Summary JSON:",
    JSON.stringify(slimSceneGraph(sceneGraph), null, 2),
    "",
    "Scene Snapshot JSON:",
    JSON.stringify(slimScene(scene), null, 2),
    "",
    "Memory Timeline Summary:",
    JSON.stringify(memorySummary || buildMemorySummary(), null, 2),
    "",
    `Vision Attached: ${visionAttached ? "yes" : "no"}`,
    "Detected Engine Issues:",
    JSON.stringify(issues, null, 2),
    "",
    "User Request:",
    String(userMessage || ""),
    "",
    "Return JSON only with schema:",
    JSON.stringify({
      scene_understanding: {
        current_state: "",
        scene_graph_insight: "",
        spatial_analysis: "",
      },
      memory_comparison: {
        previous_state: "",
        changes_detected: "",
        evolution: "",
      },
      vision_insight: {
        visual_state: "",
        issues_detected: "",
        ui_world_interpretation: "",
      },
      deep_reasoning: {
        what_is_happening: "",
        why_it_is_happening: "",
        design_correctness: "",
        risks_issues: "",
      },
      action_plan: {
        proposed_actions: [""],
        optional_fixes: [""],
        optimization_steps: [""],
      },
      execution_actions: [
        {
          action: "spawn_actor|move_actor|generate_blueprint|analyze_scene|delete_actor",
          target: "",
          params: {},
          reason: "",
          safe: true,
        },
      ],
    }),
  ].join("\n");
}

function parseModelJson(content) {
  const text = String(content || "{}").trim() || "{}";
  try {
    const parsed = JSON.parse(text);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (_) {
    return {};
  }
}

function ensureText(v, fallback = "Not available.") {
  const s = String(v || "").trim();
  return s || fallback;
}

function toList(value, fallback = []) {
  if (Array.isArray(value)) return value.map((v) => String(v)).filter(Boolean);
  return fallback;
}

function sanitizeAction(action) {
  if (!action || typeof action !== "object") return null;
  const type = String(action.action || action.type || "").trim().toLowerCase();
  const target = String(action.target || action.actor_name || "").trim();
  const params = action.params && typeof action.params === "object" ? action.params : {};
  const reason = ensureText(action.reason, "No reason provided.");
  const safe = Boolean(action.safe);

  if (!["spawn_actor", "move_actor", "generate_blueprint", "analyze_scene", "delete_actor"].includes(type)) {
    return null;
  }

  if (type === "delete_actor" && (!safe || reason.length < 10)) {
    return null;
  }

  const mapped = {
    type,
    description: reason,
    actor_name: target,
    prompt: String(params.prompt || ""),
    location: {
      x: asNumber(params.x ?? params.location?.x, 0),
      y: asNumber(params.y ?? params.location?.y, 0),
      z: asNumber(params.z ?? params.location?.z, 100),
    },
  };

  return mapped;
}

function buildMandatoryReply(structured) {
  const su = structured.scene_understanding || {};
  const mc = structured.memory_comparison || {};
  const vi = structured.vision_insight || {};
  const dr = structured.deep_reasoning || {};
  const ap = structured.action_plan || {};

  const proposed = toList(ap.proposed_actions, ["No action proposed."]);
  const optionalFixes = toList(ap.optional_fixes, ["No optional fix."]);
  const optimizations = toList(ap.optimization_steps, ["No optimization step."]);

  return [
    "🎬 SCENE UNDERSTANDING",
    `Current State: ${ensureText(su.current_state)}`,
    `Scene Graph Insight: ${ensureText(su.scene_graph_insight)}`,
    `Spatial Analysis: ${ensureText(su.spatial_analysis)}`,
    "",
    "🧠 MEMORY COMPARISON",
    `Previous State: ${ensureText(mc.previous_state)}`,
    `Changes Detected: ${ensureText(mc.changes_detected)}`,
    `Evolution: ${ensureText(mc.evolution)}`,
    "",
    "👁️ VISION INSIGHT (IF EXISTS)",
    `Visual State: ${ensureText(vi.visual_state)}`,
    `Issues Detected: ${ensureText(vi.issues_detected)}`,
    `UI / World Interpretation: ${ensureText(vi.ui_world_interpretation)}`,
    "",
    "🧠 DEEP REASONING",
    `What is happening: ${ensureText(dr.what_is_happening)}`,
    `Why it is happening: ${ensureText(dr.why_it_is_happening)}`,
    `Design correctness: ${ensureText(dr.design_correctness)}`,
    `Risks / Issues: ${ensureText(dr.risks_issues)}`,
    "",
    "⚙️ ACTION PLAN",
    `Proposed Actions: ${proposed.join(" | ")}`,
    `Optional Fixes: ${optionalFixes.join(" | ")}`,
    `Optimization Steps: ${optimizations.join(" | ")}`,
  ].join("\n");
}

function requestJson(urlString, body, headers = {}) {
  return new Promise((resolve, reject) => {
    const url = new URL(urlString);
    const payload = JSON.stringify(body || {});
    const transport = url.protocol === "https:" ? https : http;

    const req = transport.request(
      {
        protocol: url.protocol,
        hostname: url.hostname,
        port: url.port || (url.protocol === "https:" ? 443 : 80),
        path: `${url.pathname}${url.search}`,
        method: "POST",
        timeout: REQUEST_TIMEOUT_MS,
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(payload),
          ...headers,
        },
      },
      (res) => {
        let raw = "";
        res.on("data", (chunk) => {
          raw += chunk.toString("utf8");
        });
        res.on("end", () => {
          if (res.statusCode >= 400) {
            return reject(new Error(`HTTP ${res.statusCode}: ${raw.slice(0, 600)}`));
          }
          try {
            resolve(raw ? JSON.parse(raw) : {});
          } catch (_) {
            reject(new Error("Provider response is not valid JSON"));
          }
        });
      }
    );

    req.on("timeout", () => req.destroy(new Error("Provider timeout")));
    req.on("error", reject);
    req.write(payload);
    req.end();
  });
}

function isContextLengthError(error) {
  return CONTEXT_ERROR_PATTERN.test(String(error?.message || error || ""));
}

async function callProvider(body, context) {
  const providerId = String(body.providerId || "openrouter").toLowerCase();
  const apiKey = String(body.apiKey || "").trim();
  const model = String(body.model || "openai/gpt-4o-mini").trim();
  const userMessage = String(body.userMessage || "").trim();
  const temperature = Number(body.temperature ?? 0.3);
  const maxTokens = Number(body.max_tokens ?? 1200);
  const visionContext = String(body.vision_context || "").trim();

  if (!userMessage) throw new Error("userMessage is required");
  if (!apiKey && providerId !== "ollama" && providerId !== "lmstudio") {
    throw new Error(`Missing API key for provider: ${providerId}`);
  }

  const memorySummary = buildMemorySummary();

  const injectedPrompt = buildInjectedPrompt({
    scene: context.scene,
    sceneGraph: context.sceneGraph,
    memorySummary,
    userMessage,
    visionAttached: Boolean(visionContext),
    issues: context.issues,
  });

  if (providerId === "ollama" || providerId === "lmstudio") {
    const structuredLocal = {
      scene_understanding: {
        current_state: `Scene has ${context.sceneGraph.summary.total_nodes} nodes and ${context.sceneGraph.summary.lights} lights.`,
        scene_graph_insight: "Semantic graph built locally from Unreal actors.",
        spatial_analysis: "Nearby/far relations computed using actor transforms.",
      },
      memory_comparison: {
        previous_state: context.previousState,
        changes_detected: context.changeText,
        evolution: "Timeline updated in persistent memory file.",
      },
      vision_insight: {
        visual_state: visionContext ? "Screenshot attached and forwarded." : "No screenshot attached.",
        issues_detected: context.issues.join("; ") || "No visual issue inferred.",
        ui_world_interpretation: "Local mode: vision interpretation is limited.",
      },
      deep_reasoning: {
        what_is_happening: "Autonomous scene context mode is active.",
        why_it_is_happening: "Provider is local and does not require external API.",
        design_correctness: "Core context flow is correct.",
        risks_issues: context.issues.join("; ") || "No high-risk issue found.",
      },
      action_plan: {
        proposed_actions: ["Refresh scene context", "Inspect high-interaction nodes"],
        optional_fixes: ["Reduce clustered lights if performance drops"],
        optimization_steps: ["Track moved actors over timeline"],
      },
      execution_actions: [{ action: "analyze_scene", target: "scene", params: {}, reason: "Keep context fresh", safe: true }],
    };

    return {
      structured: structuredLocal,
      usage_tokens: 0,
      provider: providerId,
      model,
    };
  }

  const messages = [{
    role: "system",
    content: "Return JSON only. Never return plain text. Do not hallucinate entities.",
  }];

  if (visionContext) {
    messages.push({
      role: "user",
      content: [
        { type: "text", text: injectedPrompt },
        { type: "image_url", image_url: { url: `data:image/png;base64,${visionContext}` } },
      ],
    });
  } else {
    messages.push({ role: "user", content: injectedPrompt });
  }

  let endpoint = "https://openrouter.ai/api/v1/chat/completions";
  const headers = {
    Authorization: `Bearer ${apiKey}`,
    "Content-Type": "application/json",
  };
  if (providerId === "openai") {
    endpoint = "https://api.openai.com/v1/chat/completions";
  } else {
    headers["HTTP-Referer"] = "https://aird.local";
    headers["X-Title"] = "AIRD Autonomous Context Server";
  }

  const payload = {
    model,
    temperature,
    max_tokens: maxTokens,
    response_format: { type: "json_object" },
    messages,
  };

  let data;
  try {
    data = await requestJson(endpoint, payload, headers);
  } catch (error) {
    if (!isContextLengthError(error)) throw error;
    trimMemoryTimeline(Math.max(4, PROMPT_MEMORY_ENTRY_LIMIT));
    data = await requestJson(endpoint, {
      ...payload,
      max_tokens: Math.min(maxTokens, 900),
      messages: [{
        role: "system",
        content: "Return JSON only. Never return plain text. Use summarized scene context only.",
      }, {
        role: "user",
        content: buildInjectedPrompt({
          scene,
          sceneGraph: context.sceneGraph,
          memorySummary: buildMemorySummary(2),
          userMessage,
          visionAttached: Boolean(visionContext),
          issues: context.issues.slice(0, 4),
        }),
      }],
    }, headers);
  }
  const content = data?.choices?.[0]?.message?.content || "{}";
  const structured = parseModelJson(content);

  return {
    structured,
    usage_tokens: Number(data?.usage?.total_tokens || 0),
    provider: providerId,
    model,
  };
}

async function handleSceneSync(req, res) {
  const body = await readJsonBody(req);
  const scene = safeScene(body.scene);
  const sceneLength = Array.isArray(body?.scene?.actors) ? body.scene.actors.length : -1;
  log("SCENE RECEIVED:", sceneLength);
  if (!scene) {
    writeJson(res, 400, { status: "error", message: MISSING_SCENE_MESSAGE });
    return;
  }

  const previousGraph = latestSceneGraph;
  const sceneGraph = buildSceneGraph(scene);
  const changes = detectSceneChanges(previousGraph, sceneGraph);
  const issues = detectIssues(sceneGraph);

  latestSceneSnapshot = scene;
  latestSceneGraph = sceneGraph;
  latestSceneUpdatedAt = Date.now();

  appendMemory(sceneGraph, changes, issues, []);

  writeJson(res, 200, {
    status: "success",
    message: "Scene snapshot updated",
    actors: scene.actors.length,
    scene_id: sceneGraph.scene_id,
    changes,
    issues,
    updatedAt: latestSceneUpdatedAt,
  });
}

async function handleLlmChat(req, res) {
  const body = await readJsonBody(req);
  const scene = safeScene(body.scene) || safeScene(latestSceneSnapshot);

  if (!scene) {
    writeJson(res, 409, { status: "error", message: MISSING_SCENE_MESSAGE });
    return;
  }

  const currentGraph = buildSceneGraph(scene);
  const previousEntry = memoryTimeline.length > 1 ? memoryTimeline[memoryTimeline.length - 2] : null;
  const previousState = previousEntry
    ? `Scene ${previousEntry.scene_id} with ${previousEntry.changes?.added ?? 0} additions and ${previousEntry.changes?.removed ?? 0} removals.`
    : "No previous scene snapshot.";
  const changes = detectSceneChanges(latestSceneGraph, currentGraph);
  const changeText = `Added: ${changes.added}, Removed: ${changes.removed}, Moved: ${changes.moved}`;
  const issues = detectIssues(currentGraph);

  try {
    const providerResult = await callProvider(body, {
      scene,
      sceneGraph: currentGraph,
      previousState,
      changeText,
      issues,
    });

    const structured = providerResult.structured || {};
    const executionActionsRaw = Array.isArray(structured.execution_actions) ? structured.execution_actions : [];
    const executionActions = executionActionsRaw.map(sanitizeAction).filter(Boolean).slice(0, 8);
    const reply = buildMandatoryReply(structured);

    latestSceneSnapshot = scene;
    latestSceneGraph = currentGraph;
    latestSceneUpdatedAt = Date.now();

    appendMemory(currentGraph, changes, issues, executionActions.map((a) => `${a.type}:${a.actor_name || "scene"}`));

    writeJson(res, 200, {
      status: "success",
      message: "Context-aware response generated",
      reply,
      actions: executionActions,
      usage_tokens: providerResult.usage_tokens,
      provider: providerResult.provider,
      model: providerResult.model,
      scene_graph: currentGraph,
      memory: {
        total_entries: memoryTimeline.length,
        last_entry: memoryTimeline[memoryTimeline.length - 1] || null,
      },
      vision_context: body.vision_context ? "attached" : "none",
    });
  } catch (err) {
    writeJson(res, 500, {
      status: "error",
      message: err instanceof Error ? err.message : String(err),
    });
  }
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.method === "OPTIONS") {
      writeJson(res, 204, {});
      return;
    }

    if (req.method === "GET" && req.url === "/health") {
      writeJson(res, 200, {
        status: "ok",
        service: "aird-context-server-v2",
        hasScene: Boolean(safeScene(latestSceneSnapshot)),
        hasGraph: Boolean(latestSceneGraph),
        memory_entries: memoryTimeline.length,
        stable: memoryTimeline.length <= 24,
        updatedAt: latestSceneUpdatedAt,
      });
      return;
    }

    if (req.method === "GET" && req.url === "/memory") {
      writeJson(res, 200, { status: "ok", timeline: memoryTimeline });
      return;
    }

    if (req.method === "GET" && req.url === "/scene-graph") {
      writeJson(res, 200, { status: "ok", scene_graph: latestSceneGraph || null });
      return;
    }

    if (req.method === "POST" && req.url === "/scene-sync") {
      await handleSceneSync(req, res);
      return;
    }

    if (req.method === "POST" && req.url === "/llm/chat") {
      await handleLlmChat(req, res);
      return;
    }

    if (req.method === "POST" && req.url === "/maintenance/trim-memory") {
      const body = await readJsonBody(req);
      const before = memoryTimeline.length;
      const after = trimMemoryTimeline(Number(body.maxEntries ?? 8));
      if (body.resetScene === true) {
        latestSceneSnapshot = null;
        latestSceneGraph = null;
        latestSceneUpdatedAt = 0;
      }
      writeJson(res, 200, {
        status: "ok",
        message: "Memory timeline trimmed",
        before,
        after,
        stable: after <= 24,
      });
      return;
    }

    writeJson(res, 404, { status: "error", message: "Not found" });
  } catch (err) {
    writeJson(res, 500, {
      status: "error",
      message: err instanceof Error ? err.message : String(err),
    });
  }
});

loadMemory();
server.listen(PORT, HOST, () => {
  log(`Context server listening on http://${HOST}:${PORT}`);
});
