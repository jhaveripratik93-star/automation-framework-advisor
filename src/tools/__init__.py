"""Shared cross-cutting infrastructure used by 2+ features.

This package is deliberately NOT feature-owned — everything here is used
by multiple feature packages and would otherwise be duplicated:

  formatters.py           — framework-field rendering + case-study-relevance
                            scan, used by src.advisor_tools, src.coverage,
                            src.prerequisites, src.migration.
  assertion_counter.py    — cross-framework assertion counting, used by
                            repo_comparator.py AND src.conversion.
  conversion_validator.py — "no orphan test" structural check, used by
                            src.conversion.service.
  repo_comparator.py      — cross-repo test-case parity engine, used by
                            its own standalone compare_repos*/_pages
                            feature AND auto-chained by
                            src.conversion.parity.

Feature-owned tool implementations have moved out to their own packages:
  src.advisor_tools   — search/details/compare/recommend/score/categories/
                        migration-path-lookup (formerly ToolExecutor)
  src.coverage        — test-case coverage analysis, Excel parsing
  src.prerequisites   — prerequisite automation + CI/CD hook generation
  src.conversion      — framework-to-framework code conversion
"""
