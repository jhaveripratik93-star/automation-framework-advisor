"""Code conversion feature — rewritten architecture.

Public entry points:
  - convert(request) -> ConversionResult      (src.conversion.service)
  - CompileResult / compile_check(...)         (src.conversion.compilers)
  - load_prompt_profile(...)                   (src.conversion.prompts)
"""
from src.conversion.compilers import CompileResult, compile_check
from src.conversion.prompts import PromptProfile, load_prompt_profile
from src.conversion.service import ConversionResult, convert

__all__ = [
    "CompileResult",
    "compile_check",
    "PromptProfile",
    "load_prompt_profile",
    "ConversionResult",
    "convert",
]
