"""
Base class for scene scanning operations.
Provides common functionality for iterating over actors in the current level.
"""

import unreal
from typing import List, Type, Iterator, Optional
from functools import lru_cache


class SceneScannerBase:
    """
    Base class for scanning actors in the current Unreal Engine level.
    Handles level access and provides iterator utilities.
    Includes visual selection for actor confirmation.
    """

    def __init__(self):
        """Initialize the scanner with the current world context."""
        self.world = None
        self._init_world()

    def _init_world(self) -> None:
        """Initialize world with multiple fallback methods."""
        # Method 1: EditorSubsystem (most reliable in editor)
        try:
            editor_subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
            if editor_subsystem:
                self.world = editor_subsystem.get_editor_world()
                if self.world:
                    unreal.log("[AIRD] World acquired via EditorSubsystem")
        except Exception as e:
            unreal.log_warning(f"[AIRD] EditorSubsystem failed: {e}")

        # Method 2: EditorLevelLibrary fallback
        if not self.world:
            try:
                self.world = unreal.EditorLevelLibrary.get_editor_world()
                if self.world:
                    unreal.log("[AIRD] World acquired via EditorLevelLibrary")
            except Exception as e:
                unreal.log_warning(f"[AIRD] EditorLevelLibrary failed: {e}")

        # Method 3: Last resort - try to get any valid world
        if not self.world:
            try:
                # Try to get world from any loaded level
                for level in unreal.EditorLevelLibrary.get_editor_world().levels:
                    if level:
                        self.world = level
                        break
            except Exception as e:
                pass

        if not self.world:
            error_msg = (
                "AIRD: لم يتم العثور على عالم المحرر - تأكد من فتح مستوى في المحرر"
            )
            unreal.log_error(error_msg)
            raise RuntimeError(error_msg)

    def get_all_actors(self) -> List[unreal.Actor]:
        """
        Get ALL actors in the current level including:
        - StaticMeshActor (physical 3D models/meshes)
        - Light actors
        - Camera actors
        - All other actors in the level

        Returns:
            List of all Actor objects in the level
        """
        if not self.world:
            error_msg = "AIRD: عالم المحرر غير متوفر للمسح"
            unreal.log_error(error_msg)
            return []

        try:
            # Get ALL actors from the level using get_all_level_actors
            # This returns ALL actors including StaticMeshActor, Lights, Cameras, etc.
            actors = unreal.EditorLevelLibrary.get_all_level_actors(self.world)

            # Log what we found for debugging
            if actors:
                # Count by type for logging
                mesh_count = sum(
                    1
                    for a in actors
                    if a.get_class().is_child_of(unreal.StaticMeshActor)
                )
                light_count = sum(
                    1 for a in actors if a.get_class().is_child_of(unreal.Light)
                )
                camera_count = sum(
                    1 for a in actors if a.get_class().is_child_of(unreal.CameraActor)
                )

                unreal.log(
                    f"[AIRD] Found {len(actors)} total actors: {mesh_count} meshes, {light_count} lights, {camera_count} cameras"
                )

            return actors
        except Exception as e:
            unreal.log_error(f"AIRD: فشل في الحصول على الممثلين: {e}")
            return []

    def get_physical_meshes_only(self) -> List[unreal.Actor]:
        """
        Get ONLY physical 3D models (StaticMeshActor).
        This is useful for specifically highlighting meshes in the editor.

        Returns:
            List of StaticMeshActor objects
        """
        if not self.world:
            return []

        try:
            return unreal.GameplayStatics.get_all_actors_of_class(
                self.world, unreal.StaticMeshActor
            )
        except Exception as e:
            unreal.log_warning(f"[AIRD] Failed to get StaticMeshActors: {e}")
            return []

    def get_lights_only(self) -> List[unreal.Actor]:
        """
        Get ONLY light actors.

        Returns:
            List of Light actors
        """
        if not self.world:
            return []

        try:
            return unreal.GameplayStatics.get_all_actors_of_class(
                self.world, unreal.Light
            )
        except Exception as e:
            unreal.log_warning(f"[AIRD] Failed to get Lights: {e}")
            return []

    def get_cameras_only(self) -> List[unreal.Actor]:
        """
        Get ONLY camera actors.

        Returns:
            List of CameraActor objects
        """
        if not self.world:
            return []

        try:
            return unreal.GameplayStatics.get_all_actors_of_class(
                self.world, unreal.CameraActor
            )
        except Exception as e:
            unreal.log_warning(f"[AIRD] Failed to get Cameras: {e}")
            return []

    def select_actors_for_visual_confirmation(
        self, actors: List[unreal.Actor] = None, clear_first: bool = True
    ) -> int:
        """
        Select actors in the editor to provide visual confirmation (glow).
        Uses set_selected_level_actors to highlight actors.

        Args:
            actors: List of actors to select (if None, gets all from level)
            clear_first: Whether to clear existing selection first

        Returns:
            Number of actors selected
        """
        if not self.world:
            unreal.log_warning("[AIRD] Cannot select actors - no world context")
            return 0

        try:
            if clear_first:
                # Clear current selection
                unreal.EditorLevelLibrary.set_selected_level_actors([])

            # Get actors if not provided
            if actors is None:
                actors = self.get_all_actors()

            if actors:
                # Convert to array for UE
                actor_array = unreal.Array(unreal.Actor)
                for actor in actors[:100]:  # Limit to 100 for performance
                    if actor and not actor.is_pending_kill():
                        actor_array.append(actor)

                # Select all actors - this makes them glow with selection outline
                unreal.EditorLevelLibrary.set_selected_level_actors(actor_array)
                unreal.log(
                    f"[AIRD] Selected {len(actor_array)} actors for visual confirmation (glow)"
                )
                return len(actor_array)
        except Exception as e:
            unreal.log_warning(f"[AIRD] Failed to select actors: {e}")

        return 0

    def scan_and_select(self, limit: int = 100) -> dict:
        """
        Scan the scene and select actors for visual confirmation.

        Args:
            limit: Maximum actors to select

        Returns:
            Dictionary with scan results and selection count
        """
        actors = self.get_all_actors()

        if not actors:
            error_msg = "AIRD: لم يتم العثور على ممثلين في المشهد - تأكد من وجود عناصر في المستوى"
            unreal.log_error(error_msg)
            return {"actors_found": 0, "actors_selected": 0, "error": error_msg}

        # Select first N actors for visual confirmation
        selected_count = self.select_actors_for_visual_confirmation(actors[:limit])

        return {
            "actors_found": len(actors),
            "actors_selected": selected_count,
            "error": None,
        }

    @lru_cache(maxsize=4)
    def get_actors_cached(self, class_name: str = "Actor") -> List[unreal.Actor]:
        """
        Get actors with caching to improve performance for repeated calls.
        Cache is cleared on each scan to ensure freshness.

        Args:
            class_name: Name of the actor class (default: "Actor" for all)

        Returns:
            List of Actor objects
        """
        if class_name == "Actor":
            return self.get_all_actors()
        # For specific classes, would need to look up the class first
        return self.get_all_actors()

    def clear_cache(self) -> None:
        """Clear the actor cache to force refresh on next call."""
        self.get_actors_cached.cache_clear()

    def get_actors_of_class(
        self, actor_class: Type[unreal.Actor]
    ) -> List[unreal.Actor]:
        """
        Get all actors of a specific class in the current level.

        Args:
            actor_class: The class to filter actors by (must be subclass of Actor)

        Returns:
            List of Actor objects matching the specified class
        """
        return unreal.GameplayStatics.get_all_actors_of_class(self.world, actor_class)

    def iterate_actors(
        self, actor_class: Optional[Type[unreal.Actor]] = None
    ) -> Iterator[unreal.Actor]:
        """
        Iterate over actors in the level, optionally filtered by class.

        Args:
            actor_class: Optional class to filter by. If None, iterates all actors.

        Yields:
            Actor objects from the level
        """
        if actor_class:
            actors = self.get_actors_of_class(actor_class)
        else:
            actors = self.get_all_actors()

        for actor in actors:
            yield actor

    def get_actor_count(self, actor_class: Optional[Type[unreal.Actor]] = None) -> int:
        """
        Get the count of actors in the level, optionally filtered by class.

        Args:
            actor_class: Optional class to filter by. If None, counts all actors.

        Returns:
            Number of actors matching the criteria
        """
        if actor_class:
            return len(self.get_actors_of_class(actor_class))
        else:
            return len(self.get_all_actors())

    def is_valid_actor(self, actor: unreal.Actor) -> bool:
        """
        Check if an actor is valid and not pending kill.

        Args:
            actor: Actor to validate

        Returns:
            True if actor is valid, False otherwise
        """
        return actor and not actor.is_pending_kill()


# Example usage (for documentation purposes):
# scanner = SceneScannerBase()
# for actor in scanner.iterate_actors():
#     print(f"Found actor: {actor.get_name()}")
#
# light_count = scanner.get_actor_count(unreal.Light)
# print(f"Found {light_count} lights in the scene")
