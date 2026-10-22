"""Prerequisites — maps manual test setup/teardown steps to automation
patterns, CI/CD pipeline stages, and framework-specific setup hooks.

Feature-owned home for the automation-pattern catalog (``patterns.py``,
formerly ``src.tools.prerequisites``), the pipeline/hook generators
(``cicd_generators.py``), and the small dispatcher (``executor.py``)
that used to live inside the monolithic ``src.tools.executor.ToolExecutor``.
"""
from src.prerequisites.executor import PrerequisitesExecutor

__all__ = ["PrerequisitesExecutor"]
