"""
Analyzes light actors and extracts their properties.
"""

from typing import List, Dict, Any
import unreal
from .scene_scanner import SceneScannerBase
from .actor_categorizer import ActorCategory, ActorCategorizer


class LightInfo:
    """
    Data class representing properties of a light actor.
    Does NOT store actor references to prevent memory leaks.
    """

    def __init__(self, actor: unreal.Actor):
        """
        Initialize light info from a light actor.

        Args:
            actor: Actor that must be a light (validated by caller)
        """
        # Extract data immediately - do NOT store actor reference
        self.name = actor.get_name()
        self.location = actor.get_actor_location()
        self.rotation = actor.get_actor_rotation()
        self.light_component = self._get_light_component(actor)

        # Extract light properties
        self.light_type = self._get_light_type()
        self.intensity = self._get_intensity()
        self.color = self._get_color()
        self.attenuation_radius = self._get_attenuation_radius()
        self.mobility = self._get_mobility(actor)
        self.temperature = self._get_temperature()

    def _get_light_component(self, actor: unreal.Actor) -> unreal.LightComponent:
        """
        Get the light component from the actor.

        Returns:
            LightComponent of the actor
        """
        # For Light actors, the light component is typically the first component
        # or we can find it by class
        for component in actor.get_components_by_class(unreal.LightComponent):
            if component:
                return component
        # Fallback - should not happen for valid light actors
        return actor.get_component_by_class(unreal.LightComponent)

    def _get_light_type(self) -> str:
        """
        Get the type of light.

        Returns:
            String representation of light type (Point, Spot, Directional, Rect, Sky)
        """
        if not self.light_component:
            return "Unknown"

        light_type = self.light_component.light_type
        # Map enum to string
        type_map = {
            unreal.LightType.Point: "Point",
            unreal.LightType.Spot: "Spot",
            unreal.LightType.Directional: "Directional",
            unreal.LightType.Rect: "Rectangle",
            unreal.LightType.Sky: "Sky",
        }
        return type_map.get(light_type, "Unknown")

    def _get_intensity(self) -> float:
        """
        Get the intensity of the light.

        Returns:
            Intensity value (lumens for point/spot, cd/m^2 for directional)
        """
        if not self.light_component:
            return 0.0
        return self.light_component.intensity

    def _get_color(self) -> Dict[str, float]:
        """
        Get the color of the light.

        Returns:
            Dictionary with r, g, b values (0-1 range)
        """
        if not self.light_component:
            return {"r": 1.0, "g": 1.0, "b": 1.0}

        color = self.light_component.light_color
        return {"r": color.r, "g": color.g, "b": color.b}

    def _get_attenuation_radius(self) -> float:
        """
        Get the attenuation radius of the light.

        Returns:
            Attenuation radius (0 for directional lights)
        """
        if not self.light_component:
            return 0.0
        return self.light_component.attenuation_radius

    def _get_mobility(self, actor: unreal.Actor) -> str:
        """
        Get the mobility of the light.

        Args:
            actor: The light actor

        Returns:
            String representation (Static, Stationary, Movable)
        """
        if not actor:
            return "Unknown"

        mobility = actor.mobility
        mobility_map = {
            unreal.ComponentMobility.Static: "Static",
            unreal.ComponentMobility.Stationary: "Stationary",
            unreal.ComponentMobility.Movable: "Movable",
        }
        return mobility_map.get(mobility, "Unknown")

    def _get_temperature(self) -> float:
        """
        Get the temperature of the light (if applicable).

        Returns:
            Temperature in Kelvin (0 if not applicable)
        """
        # Temperature is primarily for sky lights
        if self.light_type == "Sky" and self.light_component:
            return self.light_component.temperature
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert light info to dictionary for JSON serialization.

        Returns:
            Dictionary representation of the light
        """
        return {
            "name": self.name,
            "type": self.light_type,
            "intensity": self.intensity,
            "color": self.color,
            "attenuation_radius": self.attenuation_radius,
            "mobility": self.mobility,
            "temperature": self.temperature,
            "location": {
                "x": self.location.x,
                "y": self.location.y,
                "z": self.location.z,
            },
            "rotation": {
                "pitch": self.rotation.pitch,
                "yaw": self.rotation.yaw,
                "roll": self.rotation.roll,
            },
        }


class LightAnalyzer:
    """
    Analyzes all lights in the current scene and extracts their properties.
    """

    def __init__(self):
        """Initialize the light analyzer."""
        self.scanner = SceneScannerBase()
        self.categorizer = ActorCategorizer()

    def analyze_lights(self) -> List[Dict[str, Any]]:
        """
        Analyze all lights in the current level.

        Returns:
            List of dictionaries containing light properties
        """
        lights = self.categorizer.get_actors_by_category(ActorCategory.LIGHT)
        light_infos = []

        for light_actor in lights:
            if self.scanner.is_valid_actor(light_actor):
                try:
                    light_info = LightInfo(light_actor)
                    light_infos.append(light_info.to_dict())
                except Exception as e:
                    # Log error but continue processing other lights
                    unreal.log_warning(
                        f"Failed to analyze light {light_actor.get_name()}: {str(e)}"
                    )

        return light_infos

    def get_light_summary(self) -> Dict[str, Any]:
        """
        Get a summary of all lights in the scene.

        Returns:
            Dictionary with light counts and aggregated properties
        """
        lights = self.analyze_lights()

        # Count by type
        type_counts = {}
        total_intensity = 0.0
        colors = []

        for light in lights:
            light_type = light["type"]
            type_counts[light_type] = type_counts.get(light_type, 0) + 1
            total_intensity += light["intensity"]
            colors.append(light["color"])

        # Calculate average color
        avg_color = {"r": 0.0, "g": 0.0, "b": 0.0}
        if colors:
            for color in colors:
                avg_color["r"] += color["r"]
                avg_color["g"] += color["g"]
                avg_color["b"] += color["b"]
            avg_color["r"] /= len(colors)
            avg_color["g"] /= len(colors)
            avg_color["b"] /= len(colors)

        return {
            "total_lights": len(lights),
            "by_type": type_counts,
            "total_intensity": total_intensity,
            "average_color": avg_color,
            "lights": lights,
        }


# Example usage (for documentation purposes):
# analyzer = LightAnalyzer()
# summary = analyzer.get_light_summary()
# print(f"Found {summary['total_lights']} lights:")
# for light_type, count in summary['by_type'].items():
#     print(f"  {light_type}: {count}")
#
# for light in summary['lights']:
#     print(f"Light '{light['name']}': {light['type']} at {light['location']}")
