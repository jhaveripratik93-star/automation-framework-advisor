"""Tools module for the automation framework migration advisor."""
from src.tools.executor import ToolExecutor
from src.tools.formatters import (
    format_architecture_fit,
    format_cicd,
    format_cloud_grids,
    format_performance,
    format_maintainability,
    format_version_info,
    format_limitations,
    positive_items,
    resolve_framework,
)
from src.tools.prerequisites import (
    PREREQ_PATTERNS,
    match_prereq_pattern,
    generate_script,
)
from src.tools.cicd_generators import (
    generate_cicd_pipeline,
    generate_framework_hooks,
)

__all__ = [
    "ToolExecutor",
    # Formatters
    "format_architecture_fit",
    "format_cicd",
    "format_cloud_grids",
    "format_performance",
    "format_maintainability",
    "format_version_info",
    "format_limitations",
    "positive_items",
    "resolve_framework",
    # Prerequisites
    "PREREQ_PATTERNS",
    "match_prereq_pattern",
    "generate_script",
    # CI/CD
    "generate_cicd_pipeline",
    "generate_framework_hooks",
]
