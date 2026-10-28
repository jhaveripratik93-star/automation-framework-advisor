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
        # (filename, error message) for every YAML file that failed to
        # load on the last load() call. A single bad file intentionally
        # does NOT stop the rest of the knowledge base from loading, but
        # that resilience used to mean failures were only visible in log
        # output — now callers (UI pages, MCP tools, the discovery
        # pipeline) can check this list to find out something is missing
        # instead of silently serving an incomplete knowledge base.
        self.load_errors: list[tuple[str, str]] = []

    def load(self) -> None:
        """Load all YAML framework profiles from the data directory."""
        self.load_errors = []

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
                logger.error(
                    "Failed to load %s — this framework will NOT be "
                    "available until the file is fixed: %s",
                    yaml_file.name, e,
                )
                self.load_errors.append((yaml_file.name, str(e)))

        if self.load_errors:
            logger.error(
                "Knowledge base loaded with %d file(s) failing validation: %s",
                len(self.load_errors),
                [name for name, _ in self.load_errors],
            )
        logger.info(
            f"Knowledge base loaded: {len(self.frameworks)} frameworks"
        )

    @property
    def has_load_errors(self) -> bool:
        """True if any YAML file failed to load on the last load() call."""
        return bool(self.load_errors)

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
