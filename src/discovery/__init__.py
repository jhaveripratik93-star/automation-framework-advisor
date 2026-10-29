"""Interactive Discovery Module - Adaptive Q&A for requirements gathering."""

from .framework_scanner import FrameworkScanner
from .questions import DISCOVERY_QUESTIONS
from .session import DiscoverySession

__all__ = ["DISCOVERY_QUESTIONS", "DiscoverySession", "FrameworkScanner"]