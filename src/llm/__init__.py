"""LLM integration module for Ollama-powered advisory responses.

Provides the OllamaClient for communicating with a local Ollama instance,
the HealthStatus dataclass for connection status reporting, and AdvisorLLM
for graph-grounded chat responses.
"""

from .ollama_client import HealthStatus, OllamaClient

__all__ = ["OllamaClient", "HealthStatus"]

try:
    from .advisor_llm import AdvisorLLM
    __all__.append("AdvisorLLM")
except ImportError:
    pass
