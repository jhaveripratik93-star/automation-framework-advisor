"""Interactive Discovery Module - Adaptive Q&A for requirements gathering."""

from .questions import DISCOVERY_QUESTIONS
from .session import DiscoverySession

__all__ = ["DISCOVERY_QUESTIONS", "DiscoverySession"]
