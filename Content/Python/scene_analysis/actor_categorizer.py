"""
Categorizes actors into logical groups for scene analysis.
"""

from enum import Enum
from typing import List, Dict, Any
import unreal
from .scene_scanner import SceneScannerBase


class ActorCategory(Enum):
    """Categories of actors for scene analysis."""

    LIGHT = "Light"
    STATIC_MESH = "StaticMesh"
    DYNAMIC_ACTOR = "DynamicActor"
    VOLUME = "Volume"
    PLAYER = "Player"
    CAMERA = "Camera"
    AUDIO = "Audio"
    OTHER = "Other"


class ActorCategorizer:
    """
    Categorizes actors into predefined groups based on their class.
    """

    def __init__(self):
        """Initialize the categorizer with a scene scanner."""
        self.scanner = SceneScannerBase()

    def categorize_actors(self) -> Dict[ActorCategory, List[unreal.Actor]]:
        """
        Categorize all actors in the current level.

        Returns:
            Dictionary mapping ActorCategory to list of actors in that category
        """
        categories = {
            ActorCategory.LIGHT: [],
            ActorCategory.STATIC_MESH: [],
            ActorCategory.DYNAMIC_ACTOR: [],
            ActorCategory.VOLUME: [],
            ActorCategory.PLAYER: [],
            ActorCategory.CAMERA: [],
            ActorCategory.AUDIO: [],
            ActorCategory.OTHER: [],
        }

        # Get all actors and categorize each one
        for actor in self.scanner.iterate_actors():
            if not self.scanner.is_valid_actor(actor):
                continue

            category = self._get_actor_category(actor)
            categories[category].append(actor)

        return categories

    def _get_actor_category(self, actor: unreal.Actor) -> ActorCategory:
        """
        Determine the category of a single actor.

        Args:
            actor: Actor to categorize

        Returns:
            ActorCategory enum value
        """
        actor_class = actor.get_class()

        # Check for lights
        if actor_class.is_child_of(unreal.Light):
            return ActorCategory.LIGHT

        # Check for static meshes
        if actor_class.is_child_of(unreal.StaticMeshActor):
            return ActorCategory.STATIC_MESH

        # Check for volumes (including trigger volumes, blocking volumes, etc.)
        if actor_class.is_child_of(unreal.Volume):
            return ActorCategory.VOLUME

        # Check for players
        if actor_class.is_child_of(unreal.PlayerController) or actor_class.is_child_of(
            unreal.Pawn
        ):
            return ActorCategory.PLAYER

        # Check for cameras
        if actor_class.is_child_of(unreal.CameraActor):
            return ActorCategory.CAMERA

        # Check for audio
        if actor_class.is_child_of(unreal.AudioActor):
            return ActorCategory.AUDIO

        # Check for dynamic actors (any actor that can move or has physics)
        # We consider actors with mobility set to movable as dynamic
        if (
            hasattr(actor, "mobility")
            and actor.mobility == unreal.ComponentMobility.Movable
        ):
            return ActorCategory.DYNAMIC_ACTOR

        # Default to other
        return ActorCategory.OTHER

    def get_category_counts(self) -> Dict[ActorCategory, int]:
        """
        Get the count of actors in each category.

        Returns:
            Dictionary mapping ActorCategory to count
        """
        categorized = self.categorize_actors()
        return {category: len(actors) for category, actors in categorized.items()}

    def get_actors_by_category(self, category: ActorCategory) -> List[unreal.Actor]:
        """
        Get all actors in a specific category.

        Args:
            category: The category to retrieve actors for

        Returns:
            List of actors in the specified category
        """
        categorized = self.categorize_actors()
        return categorized.get(category, [])

    def categorize_actors_dict(self) -> Dict[ActorCategory, List[Dict[str, Any]]]:
        """
        Categorize actors and return as dictionaries (no actor references).
        Prevents memory leaks by not storing actor objects.

        Returns:
            Dictionary mapping ActorCategory to list of actor data dicts
        """
        categories = {
            ActorCategory.LIGHT: [],
            ActorCategory.STATIC_MESH: [],
            ActorCategory.DYNAMIC_ACTOR: [],
            ActorCategory.VOLUME: [],
            ActorCategory.PLAYER: [],
            ActorCategory.CAMERA: [],
            ActorCategory.AUDIO: [],
            ActorCategory.OTHER: [],
        }

        for actor in self.scanner.iterate_actors():
            if not self.scanner.is_valid_actor(actor):
                continue

            category = self._get_actor_category(actor)
            location = actor.get_actor_location()
            rotation = actor.get_actor_rotation()

            # Store only data, not actor reference
            actor_data = {
                "name": actor.get_name(),
                "class": actor.get_class().get_name(),
                "location": {"x": location.x, "y": location.y, "z": location.z},
                "rotation": {
                    "pitch": rotation.pitch,
                    "yaw": rotation.yaw,
                    "roll": rotation.roll,
                },
            }
            categories[category].append(actor_data)

        return categories

    def get_category_counts_dict(self) -> Dict[str, int]:
        """
        Get actor counts by category as dictionary.

        Returns:
            Dictionary mapping category name to count
        """
        categorized = self.categorize_actors_dict()
        return {category.value: len(data) for category, data in categorized.items()}


# Example usage (for documentation purposes):
# categorizer = ActorCategorizer()
# counts = categorizer.get_category_counts()
# for category, count in counts.items():
#     print(f"{category.value}: {count}")
#
# lights = categorizer.get_actors_by_category(ActorCategory.LIGHT)
# for light in lights:
#     print(f"Light: {light.get_name()}")
