"""Static-analysis feature — offline Robot Framework dependency analysis.

Public entry points:
  - analyze_path(input_path, source_root="") -> dict   (file OR directory)
  - analyze_file(file_path, source_root="") -> dict     (single .robot file)

Pure static analysis: parses .robot files with the REAL Robot parser
(robot.api.get_model) and resolves imports to files on disk. No LLM, no
network, no MCP sampling.
"""
from src.analysis.robot_dependency_analyzer import (
    analyze_file,
    analyze_path,
    filter_result_by_test_case,
)

__all__ = ["analyze_path", "analyze_file", "filter_result_by_test_case"]
