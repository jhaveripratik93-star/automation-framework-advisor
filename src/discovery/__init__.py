"""Interactive Discovery Module - Adaptive Q&A for requirements gathering."""

from .questions import DISCOVERY_QUESTIONS
from .session import DiscoverySession
from .framework_scanner import FrameworkScanner

__all__ = ["DISCOVERY_QUESTIONS", "DiscoverySession", "FrameworkScanner"]