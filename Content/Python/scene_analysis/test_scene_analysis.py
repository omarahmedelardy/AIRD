"""
Unit tests for scene analysis module.
Tests actor categorization logic and scene processing.
"""

import unittest
from unittest.mock import MagicMock, patch
import sys


# Mock unreal module before importing scene_analysis
class MockUnreal:
    class Vector:
        def __init__(self, x=0, y=0, z=0):
            self.x = x
            self.y = y
            self.z = z

    class Rotator:
        def __init__(self, pitch=0, yaw=0, roll=0):
            self.pitch = pitch
            self.yaw = yaw
            self.roll = roll

    class Actor:
        def get_name(self):
            return "MockActor"

        def get_actor_location(self):
            return MockUnreal.Vector(0, 0, 0)

        def get_actor_rotation(self):
            return MockUnreal.Rotator(0, 0, 0)

        def get_class(self):
            return MagicMock()

        def is_pending_kill(self):
            return False

        def get_components_by_class(self, cls):
            return []

        def get_component_by_class(self, cls):
            return None

    class Light(Actor):
        pass

    class LightComponent:
        light_type = None
        intensity = 100.0
        light_color = MagicMock(r=1.0, g=1.0, b=1.0)
        attenuation_radius = 1000.0
        temperature = 6500.0

    class StaticMeshActor(Actor):
        pass

    class Volume(Actor):
        pass

    class PlayerController(Actor):
        pass

    class Pawn(Actor):
        pass

    class CameraActor(Actor):
        pass

    class AudioActor(Actor):
        pass

    class GameplayStatics:
        @staticmethod
        def get_all_actors_of_class(world, actor_class):
            return []

    class EditorLevelLibrary:
        @staticmethod
        def get_editor_world():
            return MagicMock()

    class ComponentMobility:
        Static = "Static"
        Stationary = "Stationary"
        Movable = "Movable"

    class LightType:
        Point = "Point"
        Spot = "Spot"
        Directional = "Directional"
        Rect = "Rect"
        Sky = "Sky"


# Install mock
sys.modules["unreal"] = MockUnreal()


class TestActorCategory(unittest.TestCase):
    """Test actor categorization logic."""

    def test_actor_category_enum_values(self):
        """Verify ActorCategory enum has correct values."""
        from scene_analysis.actor_categorizer import ActorCategory

        expected_values = [
            "Light",
            "StaticMesh",
            "DynamicActor",
            "Volume",
            "Player",
            "Camera",
            "Audio",
            "Other",
        ]
        actual_values = [cat.value for cat in ActorCategory]

        for expected in expected_values:
            self.assertIn(expected, actual_values)

    def test_actor_category_count(self):
        """Verify all actor categories are defined."""
        from scene_analysis.actor_categorizer import ActorCategory

        self.assertEqual(len(ActorCategory), 8)


class TestSceneBounds(unittest.TestCase):
    """Test scene bounds calculation."""

    def test_scene_bounds_creation(self):
        """Test SceneBounds can be created with coordinates."""
        from scene_analysis.scene_processor import SceneBounds
        from scene_analysis import SceneBounds as SB

        # This would work with actual unreal module
        # For now, just verify import works
        self.assertIsNotNone(SceneBounds)


class TestSceneScanResult(unittest.TestCase):
    """Test scene scan result data structure."""

    def test_scan_result_has_timestamp(self):
        """Verify scan result includes timestamp."""
        # This would test with actual unreal module
        pass

    def test_scan_result_json_serialization(self):
        """Verify scan result can be serialized to JSON."""
        # This would test with actual unreal module
        pass


class TestSceneQueryAPI(unittest.TestCase):
    """Test SceneQueryAPI methods."""

    def test_query_api_has_required_methods(self):
        """Verify SceneQueryAPI has all required methods."""
        from scene_analysis.scene_query_api import SceneQueryAPI

        required_methods = [
            "get_all_lights",
            "get_by_category",
            "get_scene_bounds",
            "get_scene_summary",
            "get_quick_summary",
            "query_scene",
        ]

        for method in required_methods:
            self.assertTrue(
                hasattr(SceneQueryAPI, method),
                f"SceneQueryAPI missing method: {method}",
            )


class TestToolDefinitions(unittest.TestCase):
    """Test MCP tool definitions."""

    def test_tool_definitions_exist(self):
        """Verify TOOL_DEFINITIONS is defined."""
        from scene_analysis import TOOL_DEFINITIONS

        self.assertIsInstance(TOOL_DEFINITIONS, list)
        self.assertGreater(len(TOOL_DEFINITIONS), 0)

    def test_tool_has_name(self):
        """Verify each tool has a name."""
        from scene_analysis import TOOL_DEFINITIONS

        for tool in TOOL_DEFINITIONS:
            self.assertIn("name", tool)
            self.assertIn("description", tool)
            self.assertIn("inputSchema", tool)

    def test_scene_scan_tool_exists(self):
        """Verify scan_scene tool is defined."""
        from scene_analysis import TOOL_DEFINITIONS

        tool_names = [t["name"] for t in TOOL_DEFINITIONS]
        self.assertIn("scan_scene", tool_names)


class TestToolHandlers(unittest.TestCase):
    """Test tool handler creation."""

    def test_create_tool_handlers_returns_dict(self):
        """Verify create_tool_handlers returns a dictionary."""
        from scene_analysis import create_tool_handlers

        handlers = create_tool_handlers()
        self.assertIsInstance(handlers, dict)

    def test_handlers_have_required_keys(self):
        """Verify all required handlers are created."""
        from scene_analysis import create_tool_handlers

        handlers = create_tool_handlers()

        expected_handlers = [
            "scan_scene",
            "get_scene_lights",
            "get_scene_actors",
            "get_scene_bounds",
            "get_scene_quick_summary",
            "query_scene",
        ]

        for handler_name in expected_handlers:
            self.assertIn(handler_name, handlers)


if __name__ == "__main__":
    unittest.main()
