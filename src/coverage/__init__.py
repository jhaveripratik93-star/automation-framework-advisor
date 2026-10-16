"""Coverage — maps manual/legacy test cases onto framework capabilities.

Feature-owned home for the YAML-derived capability-coverage engine
(``engine.py``, formerly ``src.tools.coverage_engine``), the Excel test
case parser (``excel_parser.py``), and the small dispatcher
(``executor.py``) that used to live inside the monolithic
``src.tools.executor.ToolExecutor``.

Not to be confused with ``src.migration.coverage.CoverageAnalyzer``,
which estimates profile-level coverage % for migration planning — a
different, already-isolated feature.
"""
from src.coverage.executor import CoverageExecutor

__all__ = ["CoverageExecutor"]
