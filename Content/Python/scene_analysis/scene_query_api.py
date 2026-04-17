"""
Scene Query API - Programmatic interface for querying scanned scene data.
Provides MCP tool definitions for chat interface integration.
"""

from typing import Dict, List, Any, Optional
import json
import unreal
from datetime import datetime

from .scene_processor import SceneProcessor
from .scene_scanner import SceneScannerBase
from .actor_categorizer import ActorCategorizer, ActorCategory
from .light_analyzer import LightAnalyzer
from .scene_cache import SceneCacheManager, get_global_cache


class SceneQueryAPI:
    """
    Programmatic API for querying scene data.
    Provides methods for different query types and integrates with MCP.
    """

    def __init__(self, use_cache: bool = True, cache_ttl: int = 30):
        """
        Initialize the query API with scene processor.

        Args:
            use_cache: Whether to use caching (default: True)
            cache_ttl: Cache time-to-live in seconds (default: 30)
        """
        try:
            self.processor = SceneProcessor()
        except Exception as e:
            unreal.log_error(f"Failed to initialize SceneProcessor: {e}")
            self.processor = None

        try:
            self.scanner = (
                SceneScannerBase()
                if self.processor and self.processor.scanner
                else None
            )
        except Exception as e:
            unreal.log_error(f"Failed to initialize SceneScannerBase: {e}")
            self.scanner = None

        try:
            self.categorizer = ActorCategorizer() if self.scanner else None
        except Exception as e:
            unreal.log_error(f"Failed to initialize ActorCategorizer: {e}")
            self.categorizer = None

        try:
            self.light_analyzer = LightAnalyzer() if self.scanner else None
        except Exception as e:
            unreal.log_error(f"Failed to initialize LightAnalyzer: {e}")
            self.light_analyzer = None

        # Caching
        self.use_cache = use_cache
        self.cache_ttl = cache_ttl
        self._cache_manager = SceneCacheManager(cache_ttl) if use_cache else None
        self._cached_result: Optional[Dict[str, Any]] = None
        self._cache_timestamp: Optional[datetime] = None

    def get_all_lights(self) -> List[Dict[str, Any]]:
        """
        Get all light actors in the scene.

        Returns:
            List of light actor data with properties
        """
        lights = self.light_analyzer.analyze_lights()
        return lights

    def get_by_category(self, category_name: str) -> List[Dict[str, Any]]:
        """
        Get actors by category name.
        Returns dictionaries only - no actor references to prevent memory leaks.

        Args:
            category_name: Name of the category (Light, StaticMesh, etc.)

        Returns:
            List of actors in that category
        """
        try:
            category = ActorCategory(category_name)
        except ValueError:
            return []

        # Use dict method to avoid storing actor references
        categorized = self.categorizer.categorize_actors_dict()
        return categorized.get(category, [])

    def get_scene_bounds(self) -> Optional[Dict[str, Any]]:
        """
        Get the spatial bounds of the scene.

        Returns:
            Dictionary with min/max coordinates, center, and size
        """
        result = self.processor.process_scene()
        return result.bounds

    def get_scene_summary(self) -> Dict[str, Any]:
        """
        Get a complete summary of the scene.

        Returns:
            Full scene analysis data including counts, lights, bounds
        """
        result = self.processor.process_scene()
        return result.to_dict()

    def get_quick_summary(self) -> Dict[str, Any]:
        """
        Get a quick summary without detailed actor lists.

        Returns:
            Summary statistics only
        """
        return self.processor.get_quick_summary()

    def query_scene(self, query_type: str, **kwargs) -> Any:
        """
        Generic query method for flexible scene queries.

        Args:
            query_type: Type of query (lights, category, bounds, summary)
            **kwargs: Additional query parameters

        Returns:
            Query results
        """
        if query_type == "lights":
            return self.get_all_lights()
        elif query_type == "category":
            category = kwargs.get("category", "Other")
            return self.get_by_category(category)
        elif query_type == "bounds":
            return self.get_scene_bounds()
        elif query_type == "summary":
            return self.get_scene_summary()
        elif query_type == "quick":
            return self.get_quick_summary()
        else:
            return {"error": f"Unknown query type: {query_type}"}


# MCP Tool Definitions for Chat Interface Integration
TOOL_DEFINITIONS = [
    {
        "name": "scan_scene",
        "description": "Scan the current Unreal Engine level and generate a comprehensive scene summary. Returns all actors, lighting information, and spatial bounds.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "description": "No parameters required. Scans the entire current level.",
        },
    },
    {
        "name": "get_scene_lights",
        "description": "Get all light actors in the current scene with their properties (type, intensity, color, location).",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "description": "No parameters required.",
        },
    },
    {
        "name": "get_scene_actors",
        "description": "Get all actors of a specific category in the scene (Light, StaticMesh, DynamicActor, Volume, Player, Camera, Audio, Other).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": [
                        "Light",
                        "StaticMesh",
                        "DynamicActor",
                        "Volume",
                        "Player",
                        "Camera",
                        "Audio",
                        "Other",
                    ],
                    "description": "The category of actors to retrieve",
                }
            },
            "required": ["category"],
        },
    },
    {
        "name": "get_scene_bounds",
        "description": "Get the spatial bounds of the scene (minimum and maximum coordinates, center point, and size).",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "description": "No parameters required.",
        },
    },
    {
        "name": "get_scene_quick_summary",
        "description": "Get a quick summary of scene statistics without detailed actor lists. Fast for large scenes.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "description": "No parameters required.",
        },
    },
    {
        "name": "query_scene",
        "description": "Flexible scene query with various query types.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query_type": {
                    "type": "string",
                    "enum": ["lights", "category", "bounds", "summary", "quick"],
                    "description": "Type of query to execute",
                },
                "category": {
                    "type": "string",
                    "description": "Category name (required if query_type is 'category')",
                },
            },
            "required": ["query_type"],
        },
    },
]


def create_tool_handlers() -> Dict[str, callable]:
    """
    Create handler functions for MCP tools.

    Returns:
        Dictionary mapping tool name to handler function
    """
    api = SceneQueryAPI()

    def handle_scan_scene(params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            result = api.get_scene_summary()
            return {"ok": True, "result": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def handle_get_scene_lights(params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            lights = api.get_all_lights()
            return {"ok": True, "result": lights}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def handle_get_scene_actors(params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            category = params.get("category", "Other")
            actors = api.get_by_category(category)
            return {"ok": True, "result": actors}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def handle_get_scene_bounds(params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            bounds = api.get_scene_bounds()
            return {"ok": True, "result": bounds}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def handle_get_scene_quick_summary(params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            summary = api.get_quick_summary()
            return {"ok": True, "result": summary}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def handle_query_scene(params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            query_type = params.get("query_type", "summary")
            category = params.get("category")
            result = api.query_scene(query_type, category=category)
            return {"ok": True, "result": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return {
        "scan_scene": handle_scan_scene,
        "get_scene_lights": handle_get_scene_lights,
        "get_scene_actors": handle_get_scene_actors,
        "get_scene_bounds": handle_get_scene_bounds,
        "get_scene_quick_summary": handle_get_scene_quick_summary,
        "query_scene": handle_query_scene,
    }


# Example usage:
# api = SceneQueryAPI()
# lights = api.get_all_lights()
# actors = api.get_by_category("StaticMesh")
# bounds = api.get_scene_bounds()
# summary = api.get_scene_summary()
#
# # Using tool handlers with MCP
# handlers = create_tool_handlers()
# result = handlers["scan_scene"]({})
# print(json.dumps(result, indent=2))
