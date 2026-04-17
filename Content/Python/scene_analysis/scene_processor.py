"""
Main scene processor that coordinates scanning, categorization, and analysis.
Produces a unified JSON summary of the scene.
"""

from typing import Dict, List, Any, Optional
import json
import unreal
from datetime import datetime

from .scene_scanner import SceneScannerBase
from .actor_categorizer import ActorCategorizer, ActorCategory
from .light_analyzer import LightAnalyzer


class SceneBounds:
    """
    Represents the spatial bounds of the scene.
    """

    def __init__(self, min_location: unreal.Vector, max_location: unreal.Vector):
        """
        Initialize bounds with min and max coordinates.

        Args:
            min_location: Minimum corner of the bounding box
            max_location: Maximum corner of the bounding box
        """
        self.min_location = min_location
        self.max_location = max_location

    @property
    def center(self) -> unreal.Vector:
        """Get the center point of the bounds."""
        return unreal.Vector(
            (self.min_location.x + self.max_location.x) / 2,
            (self.min_location.y + self.max_location.y) / 2,
            (self.min_location.z + self.max_location.z) / 2,
        )

    @property
    def size(self) -> unreal.Vector:
        """Get the size of the bounding box."""
        return unreal.Vector(
            self.max_location.x - self.min_location.x,
            self.max_location.y - self.min_location.y,
            self.max_location.z - self.min_location.z,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "min": {
                "x": self.min_location.x,
                "y": self.min_location.y,
                "z": self.min_location.z,
            },
            "max": {
                "x": self.max_location.x,
                "y": self.max_location.y,
                "z": self.max_location.z,
            },
            "center": {"x": self.center.x, "y": self.center.y, "z": self.center.z},
            "size": {"x": self.size.x, "y": self.size.y, "z": self.size.z},
        }


class SceneScanResult:
    """
    Data class containing the complete scene scan result.
    """

    def __init__(self):
        """Initialize empty scan result."""
        self.timestamp = datetime.now().isoformat()
        self.total_actors = 0
        self.actor_counts: Dict[str, int] = {}
        self.categories: Dict[str, List[Dict[str, Any]]] = {}
        self.lights: List[Dict[str, Any]] = []
        self.light_summary: Dict[str, Any] = {}
        self.bounds: Optional[Dict[str, Any]] = None
        self.scan_duration_ms: float = 0.0
        self.error_message: Optional[str] = None
        self.visual_confirmation: Optional[Dict[str, int]] = None

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary for JSON serialization.

        Returns:
            Complete scene scan result as dictionary
        """
        result = {
            "timestamp": self.timestamp,
            "total_actors": self.total_actors,
            "actor_counts": self.actor_counts,
            "categories": self.categories,
            "lights": self.lights,
            "light_summary": self.light_summary,
            "bounds": self.bounds,
            "scan_duration_ms": self.scan_duration_ms,
        }

        # Add error message if present
        if self.error_message:
            result["error_message"] = self.error_message

        # Add visual confirmation data
        if self.visual_confirmation:
            result["visual_confirmation"] = self.visual_confirmation

        return result

    def to_json(self) -> str:
        """
        Convert to JSON string.

        Returns:
            JSON string representation
        """
        return json.dumps(self.to_dict(), indent=2)


class SceneProcessor:
    """
    Main processor that coordinates all scene analysis components.
    Produces a unified JSON summary of the current level.
    """

    def __init__(self):
        """Initialize the scene processor with all required components."""
        try:
            self.scanner = SceneScannerBase()
        except RuntimeError as e:
            unreal.log_error(f"Failed to initialize SceneScannerBase: {e}")
            self.scanner = None

        try:
            self.categorizer = ActorCategorizer() if self.scanner else None
        except RuntimeError as e:
            unreal.log_error(f"Failed to initialize ActorCategorizer: {e}")
            self.categorizer = None

        try:
            self.light_analyzer = LightAnalyzer() if self.scanner else None
        except RuntimeError as e:
            unreal.log_error(f"Failed to initialize LightAnalyzer: {e}")
            self.light_analyzer = None

    def process_scene(self) -> SceneScanResult:
        """
        Process the entire scene and generate a comprehensive summary.
        Includes visual confirmation by selecting actors in the editor.

        Returns:
            SceneScanResult containing all analyzed data
        """
        import time

        start_time = time.perf_counter()

        result = SceneScanResult()

        # Check if scanner is initialized
        if not self.scanner or not self.categorizer:
            result.scan_duration_ms = (time.perf_counter() - start_time) * 1000
            result.error_message = "AIRD: فشل في تهيئة الماسح الضوئي"
            return result

        # Get all actors first for visual confirmation
        all_actors = self.scanner.get_all_actors()

        if not all_actors:
            error_msg = "AIRD: لم يتم العثور على ممثلين في المشهد - تأكد من فتح مستوى يحتوي على عناصر"
            unreal.log_error(error_msg)
            result.error_message = error_msg
            result.scan_duration_ms = (time.perf_counter() - start_time) * 1000
            return result

        # Step 1: Get actor counts by category (using dict method to avoid memory leaks)
        category_counts = self.categorizer.get_category_counts_dict()
        result.actor_counts = category_counts
        result.total_actors = sum(category_counts.values())

        # Step 2: Get categorized actors as dicts (no actor references)
        categorized = self.categorizer.categorize_actors_dict()
        result.categories = categorized

        # Step 3: Analyze lights
        if self.light_analyzer:
            result.lights = self.light_analyzer.analyze_lights()
            result.light_summary = self.light_analyzer.get_light_summary()

        # Step 4: Calculate scene bounds
        result.bounds = self._calculate_bounds()

        # Step 5: Visual confirmation - select actors in editor
        try:
            if self.scanner:
                # Get physical meshes for better visual feedback
                meshes = self.scanner.get_physical_meshes_only()
                lights = self.scanner.get_lights_only()

                # Combine meshes and lights for selection
                all_selectable = list(meshes) + list(lights)

                if all_selectable:
                    # Select them for visual glow
                    self.scanner.select_actors_for_visual_confirmation(
                        all_selectable[:100]
                    )

                result.visual_confirmation = {
                    "actors_selected": min(len(all_selectable), 100),
                    "actors_found": len(all_actors),
                    "meshes_found": len(meshes),
                    "lights_found": len(lights),
                }
        except Exception as e:
            unreal.log_warning(f"[AIRD] Visual confirmation failed: {e}")

        # Calculate scan duration
        result.scan_duration_ms = (time.perf_counter() - start_time) * 1000

        return result

    def _serialize_categories(
        self, categorized: Dict[ActorCategory, List[unreal.Actor]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Serialize categorized actors to dictionaries.

        Args:
            categorized: Dictionary of ActorCategory to list of actors

        Returns:
            Dictionary with category names as keys and actor data as values
        """
        result = {}

        for category, actors in categorized.items():
            category_actors = []
            for actor in actors:
                if self.scanner.is_valid_actor(actor):
                    location = actor.get_actor_location()
                    rotation = actor.get_actor_rotation()
                    category_actors.append(
                        {
                            "name": actor.get_name(),
                            "class": actor.get_class().get_name(),
                            "location": {
                                "x": location.x,
                                "y": location.y,
                                "z": location.z,
                            },
                            "rotation": {
                                "pitch": rotation.pitch,
                                "yaw": rotation.yaw,
                                "roll": rotation.roll,
                            },
                        }
                    )
            result[category.value] = category_actors

        return result

    def _calculate_bounds(self) -> Optional[Dict[str, Any]]:
        """
        Calculate the bounding box of all actors in the scene.

        Returns:
            Dictionary containing bounds data, or None if scene is empty
        """
        all_actors = list(self.scanner.iterate_actors())

        if not all_actors:
            return None

        # Initialize with first valid actor location
        first_valid = None
        for actor in all_actors:
            if self.scanner.is_valid_actor(actor):
                first_valid = actor.get_actor_location()
                break

        if first_valid is None:
            return None

        min_location = unreal.Vector(first_valid.x, first_valid.y, first_valid.z)
        max_location = unreal.Vector(first_valid.x, first_valid.y, first_valid.z)

        # Find min/max coordinates across all actors
        for actor in all_actors:
            if not self.scanner.is_valid_actor(actor):
                continue

            location = actor.get_actor_location()

            min_location.x = min(min_location.x, location.x)
            min_location.y = min(min_location.y, location.y)
            min_location.z = min(min_location.z, location.z)

            max_location.x = max(max_location.x, location.x)
            max_location.y = max(max_location.y, location.y)
            max_location.z = max(max_location.z, location.z)

        bounds = SceneBounds(min_location, max_location)
        return bounds.to_dict()

    def get_quick_summary(self) -> Dict[str, Any]:
        """
        Get a quick summary without full actor details.

        Returns:
            Dictionary with summary statistics
        """
        category_counts = self.categorizer.get_category_counts()
        light_summary = self.light_analyzer.get_light_summary()

        return {
            "total_actors": sum(category_counts.values()),
            "actor_counts": {
                category.value: count for category, count in category_counts.items()
            },
            "total_lights": light_summary.get("total_lights", 0),
            "light_types": light_summary.get("by_type", {}),
        }

    def query_by_category(self, category: ActorCategory) -> List[Dict[str, Any]]:
        """
        Query actors by category.

        Args:
            category: ActorCategory to query

        Returns:
            List of actor data dictionaries
        """
        actors = self.categorizer.get_actors_by_category(category)
        result = []

        for actor in actors:
            if self.scanner.is_valid_actor(actor):
                location = actor.get_actor_location()
                result.append(
                    {
                        "name": actor.get_name(),
                        "class": actor.get_class().get_name(),
                        "location": {"x": location.x, "y": location.y, "z": location.z},
                    }
                )

        return result


# Convenience function for quick access
def scan_scene() -> str:
    """
    Convenience function to scan the scene and return JSON.

    Returns:
        JSON string containing complete scene analysis
    """
    processor = SceneProcessor()
    result = processor.process_scene()
    return result.to_json()


# Example usage:
# processor = SceneProcessor()
# result = processor.process_scene()
# print(result.to_json())
#
# # Quick summary
# summary = processor.get_quick_summary()
# print(f"Total actors: {summary['total_actors']}")
#
# # Query specific category
# lights = processor.query_by_category(ActorCategory.LIGHT)
# for light in lights:
#     print(f"Light: {light['name']}")
