"""
AIRD Scene Analysis Module
Provides automated scene scanning and context analysis for Unreal Engine levels.
"""

from .scene_scanner import SceneScannerBase
from .actor_categorizer import ActorCategory, ActorCategorizer
from .light_analyzer import LightAnalyzer, LightInfo
from .scene_processor import SceneProcessor, SceneScanResult, SceneBounds, scan_scene
from .scene_query_api import SceneQueryAPI, TOOL_DEFINITIONS, create_tool_handlers
from .scene_visualization import SceneVisualizationData, get_scene_visualization_html
from .scene_cache import (
    SceneCacheManager,
    IncrementalSceneScanner,
    get_global_cache,
    invalidate_global_cache,
)

__all__ = [
    "SceneScannerBase",
    "ActorCategory",
    "ActorCategorizer",
    "LightAnalyzer",
    "LightInfo",
    "SceneProcessor",
    "SceneScanResult",
    "SceneBounds",
    "scan_scene",
    "SceneQueryAPI",
    "TOOL_DEFINITIONS",
    "create_tool_handlers",
    "SceneVisualizationData",
    "get_scene_visualization_html",
    "SceneCacheManager",
    "IncrementalSceneScanner",
    "get_global_cache",
    "invalidate_global_cache",
]
