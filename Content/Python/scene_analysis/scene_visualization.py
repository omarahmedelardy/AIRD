"""
Scene Visualization Module
Provides data for UMG widget visualization and HTML integration.
"""

from typing import Dict, List, Any, Optional
import json
from .scene_processor import SceneProcessor
from .scene_query_api import SceneQueryAPI


class SceneVisualizationData:
    """
    Generates visualization data for scene analysis.
    Used by UMG widgets and HTML frontend.
    """

    def __init__(self):
        """Initialize with scene processor."""
        self.processor = SceneProcessor()
        self.api = SceneQueryAPI() if self.processor.scanner else None

    def get_pie_chart_data(self) -> Dict[str, Any]:
        """
        Get data for actor distribution pie chart.

        Returns:
            Dictionary with labels, values, and colors
        """
        if not self.api:
            return {"labels": [], "values": [], "colors": []}

        summary = self.api.get_quick_summary()
        actor_counts = summary.get("actor_counts", {})

        # Define colors for each category
        category_colors = {
            "Light": "#FFD700",  # Gold
            "StaticMesh": "#4169E1",  # Royal Blue
            "DynamicActor": "#32CD32",  # Lime Green
            "Volume": "#9370DB",  # Medium Purple
            "Player": "#FF4500",  # Orange Red
            "Camera": "#00CED1",  # Dark Turquoise
            "Audio": "#FF69B4",  # Hot Pink
            "Other": "#808080",  # Gray
        }

        labels = []
        values = []
        colors = []

        for category, count in actor_counts.items():
            if count > 0:
                labels.append(category)
                values.append(count)
                colors.append(category_colors.get(category, "#808080"))

        return {
            "labels": labels,
            "values": values,
            "colors": colors,
            "total": sum(values),
        }

    def get_actor_list_by_category(
        self, category: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get actors filtered by category for list view.

        Args:
            category: Category name to filter by
            limit: Maximum number of actors to return

        Returns:
            List of actor data dictionaries
        """
        if not self.api:
            return []

        actors = self.api.get_by_category(category)
        return actors[:limit]

    def get_visualization_summary(self) -> Dict[str, Any]:
        """
        Get complete visualization data for the scene.

        Returns:
            Dictionary with all visualization data
        """
        pie_data = self.get_pie_chart_data()
        quick_summary = self.processor.get_quick_summary()
        bounds = self.api.get_scene_bounds() if self.api else None

        return {
            "pie_chart": pie_data,
            "quick_summary": quick_summary,
            "bounds": bounds,
            "timestamp": self.processor.process_scene().timestamp
            if self.processor.scanner
            else None,
        }

    def get_light_details(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get detailed light information for visualization.

        Args:
            limit: Maximum number of lights to return

        Returns:
            List of light details
        """
        if not self.api:
            return []

        lights = self.api.get_all_lights()
        return lights[:limit]


def get_scene_visualization_html() -> str:
    """
    Get HTML/JavaScript snippet for scene visualization.

    Returns:
        HTML string for visualization widget
    """
    return """
<div id="scene-viz-widget" style="display: none; position: fixed; top: 10px; right: 10px; width: 300px; background: #1a1a2e; border-radius: 8px; padding: 15px; box-shadow: 0 4px 20px rgba(0,0,0,0.5); z-index: 1000;">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
        <h3 style="margin: 0; color: #fff; font-size: 14px;">Scene Analysis</h3>
        <button onclick="document.getElementById('scene-viz-widget').style.display='none'" style="background: none; border: none; color: #888; cursor: pointer; font-size: 18px;">&times;</button>
    </div>
    <canvas id="scene-pie-chart" width="250" height="150"></canvas>
    <div id="scene-actor-list" style="max-height: 200px; overflow-y: auto; margin-top: 10px;"></div>
</div>

<script>
function renderScenePieChart(data) {
    const canvas = document.getElementById('scene-pie-chart');
    if (!canvas || !data || !data.labels) return;
    
    const ctx = canvas.getContext('2d');
    const total = data.values.reduce((a, b) => a + b, 0);
    let startAngle = 0;
    
    data.labels.forEach((label, i) => {
        const sliceAngle = (data.values[i] / total) * 2 * Math.PI;
        ctx.beginPath();
        ctx.moveTo(125, 75);
        ctx.arc(125, 75, 70, startAngle, startAngle + sliceAngle);
        ctx.fillStyle = data.colors[i];
        ctx.fill();
        startAngle += sliceAngle;
    });
    
    // Draw legend
    ctx.font = '10px Arial';
    data.labels.forEach((label, i) => {
        const y = 160 + (i * 12);
        ctx.fillStyle = data.colors[i];
        ctx.fillRect(10, y, 10, 10);
        ctx.fillStyle = '#fff';
        ctx.fillText(label + ': ' + data.values[i], 25, y + 9);
    });
}

function toggleSceneViz() {
    const widget = document.getElementById('scene-viz-widget');
    if (widget.style.display === 'none') {
        widget.style.display = 'block';
        // Fetch data and render
        if (typeof callRpc === 'function') {
            callRpc('get_scene_quick_summary', {}).then(result => {
                if (result && result.result) {
                    renderScenePieChart(result.result);
                }
            });
        }
    } else {
        widget.style.display = 'none';
    }
}
</script>
"""


# Example usage:
# viz = SceneVisualizationData()
# pie_data = viz.get_pie_chart_data()
# list_data = viz.get_actor_list_by_category("Light")
# html_snippet = get_scene_visualization_html()
