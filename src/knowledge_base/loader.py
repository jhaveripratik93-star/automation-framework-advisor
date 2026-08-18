"""Knowledge Base loader - reads YAML framework profiles."""

import logging
from pathlib import Path

import yaml

from .schema import FrameworkData

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """Loads and manages framework data from YAML files."""

    # Aliases for common framework name variations
    ALIASES: dict[str, str] = {
        "selenium": "selenium webdriver",
        "webdriver": "selenium webdriver",
    }

    def __init__(self, data_dir: str = "data/frameworks"):
        self.data_dir = Path(data_dir)
        self.frameworks: dict[str, FrameworkData] = {}

    def load(self) -> None:
        """Load all YAML framework profiles from the data directory."""
        if not self.data_dir.exists():
            logger.warning(f"Data directory not found: {self.data_dir}")
            return

        for yaml_file in sorted(self.data_dir.glob("*.yaml")):
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                fw = FrameworkData(**data)
                self.frameworks[fw.framework_name.lower()] = fw
                logger.info(f"Loaded framework: {fw.framework_name}")
            except Exception as e:
                logger.warning(
                    f"Failed to load {yaml_file.name}: {e}"
                )

        logger.info(
            f"Knowledge base loaded: {len(self.frameworks)} frameworks"
        )

    def get(self, name: str) -> FrameworkData | None:
        """Get a framework by name (case-insensitive, with alias support)."""
        key = name.lower()
        # Check alias first
        key = self.ALIASES.get(key, key)
        return self.frameworks.get(key)

    def list_all(self) -> list[FrameworkData]:
        """Return all loaded frameworks."""
        return list(self.frameworks.values())

    def list_names(self) -> list[str]:
        """Return all framework names."""
        return [fw.framework_name for fw in self.frameworks.values()]

    def filter_by_architecture(
        self, arch_types: list[str]
    ) -> list[FrameworkData]:
        """Return frameworks that support at least one architecture type."""
        results = []
        for fw in self.frameworks.values():
            for arch in arch_types:
                fit = fw.architecture_fit.get(arch, False)
                if fit is True or (
                    isinstance(fit, str) and fit.lower() not in ("false", "")
                ):
                    results.append(fw)
                    break
        return results
