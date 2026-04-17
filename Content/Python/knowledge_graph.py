from __future__ import annotations

import math
from typing import Any, Dict, List


def _distance(a: Dict[str, float], b: Dict[str, float]) -> float:
    return math.sqrt((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2 + (a["z"] - b["z"]) ** 2)


def build_spatial_graph(scene_context: Dict[str, Any], max_edges_per_actor: int = 3) -> Dict[str, Any]:
    actors: List[Dict[str, Any]] = scene_context.get("actors", [])
    graph: Dict[str, Any] = {"nodes": [], "edges": []}

    for actor in actors:
        graph["nodes"].append({"id": actor.get("name"), "class": actor.get("class")})

    for actor in actors:
        origin = actor.get("location", {"x": 0, "y": 0, "z": 0})
        neighbors = []
        for other in actors:
            if other is actor:
                continue
            d = _distance(origin, other.get("location", {"x": 0, "y": 0, "z": 0}))
            neighbors.append((d, other))

        neighbors.sort(key=lambda item: item[0])
        for d, near in neighbors[:max_edges_per_actor]:
            graph["edges"].append(
                {
                    "from": actor.get("name"),
                    "to": near.get("name"),
                    "relation": "near",
                    "distance": round(d, 2),
                }
            )

    return graph
