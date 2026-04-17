"""
Caching mechanism and incremental scan detection for scene analysis.
Tracks scene changes to optimize scan performance.
"""

from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import unreal
from .scene_scanner import SceneScannerBase


class SceneCacheManager:
    """
    Manages scene scan caching with dirty flag tracking.
    Tracks changes to enable incremental scans.
    """

    def __init__(self, cache_ttl_seconds: int = 30):
        """
        Initialize cache manager.

        Args:
            cache_ttl_seconds: Time-to-live for cache in seconds (default: 30)
        """
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self._cached_summary: Optional[Dict[str, Any]] = None
        self._cache_timestamp: Optional[datetime] = None
        self._last_actor_hash: Optional[str] = None
        self._is_dirty: bool = True
        self._actor_hashes: Dict[str, str] = {}

    def is_cache_valid(self) -> bool:
        """
        Check if cached data is still valid.

        Returns:
            True if cache exists and hasn't expired
        """
        if self._cached_summary is None or self._cache_timestamp is None:
            return False

        age = datetime.now() - self._cache_timestamp
        return age < self.cache_ttl and not self._is_dirty

    def get_cached_summary(self) -> Optional[Dict[str, Any]]:
        """
        Get cached scene summary if valid.

        Returns:
            Cached summary or None
        """
        if self.is_cache_valid():
            return self._cached_summary
        return None

    def set_cached_summary(self, summary: Dict[str, Any]) -> None:
        """
        Store scene summary in cache.

        Args:
            summary: Scene summary data to cache
        """
        self._cached_summary = summary
        self._cache_timestamp = datetime.now()
        self._is_dirty = False
        self._update_actor_hashes(summary)

    def invalidate_cache(self) -> None:
        """Mark cache as invalid (dirty)."""
        self._is_dirty = True
        self._cached_summary = None

    def mark_dirty(self) -> None:
        """Alias for invalidate_cache."""
        self.invalidate_cache()

    def _update_actor_hashes(self, summary: Dict[str, Any]) -> None:
        """
        Update actor hashes for change detection.

        Args:
            summary: Scene summary containing actor data
        """
        self._actor_hashes.clear()

        # Hash each actor for change detection
        categories = summary.get("categories", {})
        for category, actors in categories.items():
            for actor in actors:
                actor_name = actor.get("name", "")
                if actor_name:
                    # Simple hash based on name and location
                    loc = actor.get("location", {})
                    hash_str = (
                        f"{actor_name}_{loc.get('x')}_{loc.get('y')}_{loc.get('z')}"
                    )
                    self._actor_hashes[actor_name] = hash_str

    def detect_changes(self, new_summary: Dict[str, Any]) -> Dict[str, Any]:
        """
        Detect changes between cached and new summary.

        Args:
            new_summary: New scene summary to compare

        Returns:
            Dictionary with change information
        """
        if not self._actor_hashes:
            return {"changed": True, "reason": "no_previous_data"}

        new_actors = set()
        categories = new_summary.get("categories", {})

        for category, actors in categories.items():
            for actor in actors:
                actor_name = actor.get("name", "")
                if actor_name:
                    loc = actor.get("location", {})
                    new_hash = (
                        f"{actor_name}_{loc.get('x')}_{loc.get('y')}_{loc.get('z')}"
                    )
                    new_actors.add(actor_name)

                    # Check if actor changed
                    if actor_name not in self._actor_hashes:
                        return {
                            "changed": True,
                            "reason": "new_actor",
                            "actor": actor_name,
                        }
                    elif self._actor_hashes[actor_name] != new_hash:
                        return {
                            "changed": True,
                            "reason": "actor_modified",
                            "actor": actor_name,
                        }

        # Check for deleted actors
        for old_actor in self._actor_hashes:
            if old_actor not in new_actors:
                return {"changed": True, "reason": "deleted_actor", "actor": old_actor}

        return {"changed": False}

    def get_incremental_summary(self, full_summary: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get incremental summary showing only changes.

        Args:
            full_summary: Complete scene summary

        Returns:
            Summary with incremental flag
        """
        changes = self.detect_changes(full_summary)

        return {
            "is_incremental": not changes.get("changed", True),
            "change_reason": changes.get("reason"),
            "changed_actor": changes.get("actor"),
            "timestamp": datetime.now().isoformat(),
            "full_summary": full_summary,
        }


class IncrementalSceneScanner:
    """
    Performs incremental scene scans based on change detection.
    """

    def __init__(self, scanner: SceneScannerBase, cache_ttl: int = 30):
        """
        Initialize incremental scanner.

        Args:
            scanner: Base scene scanner
            cache_ttl_seconds: Cache time-to-live in seconds
        """
        self.scanner = scanner
        self.cache_manager = SceneCacheManager(cache_ttl)

    def should_full_scan(self) -> bool:
        """
        Determine if a full scan is needed.

        Returns:
            True if full scan should be performed
        """
        return not self.cache_manager.is_cache_valid()

    def get_scan_mode(self) -> str:
        """
        Get the recommended scan mode.

        Returns:
            "full" or "incremental"
        """
        return "full" if self.should_full_scan() else "incremental"

    def after_scene_change(self) -> None:
        """
        Call this after making changes to the scene.
        Marks cache as dirty to force refresh on next scan.
        """
        self.cache_manager.mark_dirty()

    def get_or_compute_summary(self, compute_fn: callable) -> Dict[str, Any]:
        """
        Get summary from cache or compute new one.

        Args:
            compute_fn: Function to compute scene summary

        Returns:
            Scene summary (cached or new)
        """
        cached = self.cache_manager.get_cached_summary()
        if cached is not None:
            return cached

        # Compute new summary
        new_summary = compute_fn()

        # Detect changes
        incremental = self.cache_manager.get_incremental_summary(new_summary)

        # Cache the result
        self.cache_manager.set_cached_summary(new_summary)

        return incremental if incremental.get("is_incremental") else new_summary


# Global cache instance for module-level caching
_global_cache: Optional[SceneCacheManager] = None


def get_global_cache() -> SceneCacheManager:
    """
    Get global cache manager instance.

    Returns:
        Global SceneCacheManager instance
    """
    global _global_cache
    if _global_cache is None:
        _global_cache = SceneCacheManager()
    return _global_cache


def invalidate_global_cache() -> None:
    """Invalidate the global cache."""
    global _global_cache
    if _global_cache:
        _global_cache.invalidate_cache()
    _global_cache = None


# Example usage:
# cache = SceneCacheManager(cache_ttl_seconds=30)
# scanner = SceneScannerBase()
# incremental = IncrementalSceneScanner(scanner, cache_ttl=30)
#
# # Check scan mode
# mode = incremental.get_scan_mode()  # "full" or "incremental"
#
# # After scene changes
# incremental.after_scene_change()
#
# # Get summary with caching
# result = incremental.get_or_compute_summary(lambda: processor.process_scene().to_dict())
