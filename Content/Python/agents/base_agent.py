from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseAgent(ABC):
    """Base contract for all AIRD agents."""

    def __init__(self, name: str) -> None:
        self.name = str(name or "unknown").strip().lower()

    @abstractmethod
    def process(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Process a normalized request payload."""
        raise NotImplementedError

