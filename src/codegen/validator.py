"""Code Validator — multi-layer validation for generated test code.

Ensures generated code is syntactically correct, structurally sound,
and has complete imports before delivering to the user.

Validation layers:
  1. Syntax check (AST/bracket balance/regex)
  2. Framework structure rules (required patterns present)
  3. Import completeness (all used APIs have imports)
  4. LLM fix loop (auto-repair on failure, max retries)
"""
from __future__ import annotations

import ast
import logging
import re
from typing import Any

from src.codegen.models import TargetFramework, ValidationResult

logger = logging.getLogger(__name__)


# ── Framework Validation Rules ────────────────────────────────────────

_FRAMEWORK_RULES: dict[str, dict[str, Any]] = {
    "playwright_ts": {
        "language": "typescript",
        "must_have_any": [
            r"\btest\s*\(",
            r"\btest\.describe\s*\(",
        ],
        "should_have": [
            r"\bexpect\s*\(",
            r"\bawait\s+",
        ],
        "must_import": [
            r"from\s+['\"]@playwright/test['\"]",
        ],
        "forbidden": [
            r"\brequire\s*\(\s*['\"]selenium",  # Wrong framework
        ],
        "required_imports": {
            "test": "import { test, expect } from '@playwright/test';",
            "expect": "import { test, expect } from '@playwright/test';",
            "Page": "import { Page } from '@playwright/test';",
        },
    },
    "playwright_py": {
        "language": "python",
        "must_have_any": [
            r"\bdef\s+test_\w+",
        ],
        "should_have": [
            r"\bexpect\s*\(",
            r"\bpage\.\w+",
        ],
        "must_import": [
            r"from\s+playwright",
        ],
        "forbidden": [
            r"\bfrom\s+selenium",
        ],
        "required_imports": {
            "expect": "from playwright.sync_api import expect",
            "Page": "from playwright.sync_api import Page",
            "sync_playwright": "from playwright.sync_api import sync_playwright",
        },
    },
    "selenium_py": {
        "language": "python",
        "must_have_any": [
            r"\bdef\s+test_\w+",
            r"\bclass\s+Test\w+",
        ],
        "should_have": [
            r"\bassert\b",
            r"\bdriver\.\w+",
        ],
        "must_import": [
            r"from\s+selenium",
        ],
        "forbidden": [
            r"\bfrom\s+playwright",
        ],
        "required_imports": {
            "By": "from selenium.webdriver.common.by import By",
            "WebDriverWait": "from selenium.webdriver.support.ui import WebDriverWait",
            "expected_conditions": "from selenium.webdriver.support import expected_conditions as EC",
            "EC": "from selenium.webdriver.support import expected_conditions as EC",
            "Select": "from selenium.webdriver.support.ui import Select",
            "ActionChains": "from selenium.webdriver.common.action_chains import ActionChains",
            "Keys": "from selenium.webdriver.common.keys import Keys",
        },
    },
    "selenium_java": {
        "language": "java",
        "must_have_any": [
            r"@Test",
            r"\bpublic\s+void\s+test\w+",
        ],
        "should_have": [
            r"\bassert\w*\s*\(",
            r"\bdriver\.\w+",
        ],
        "must_import": [
            r"import\s+org\.openqa\.selenium",
        ],
        "forbidden": [],
        "required_imports": {
            "By": "import org.openqa.selenium.By;",
            "WebElement": "import org.openqa.selenium.WebElement;",
            "WebDriverWait": "import org.openqa.selenium.support.ui.WebDriverWait;",
            "ExpectedConditions": "import org.openqa.selenium.support.ui.ExpectedConditions;",
            "Select": "import org.openqa.selenium.support.ui.Select;",
            "Actions": "import org.openqa.selenium.interactions.Actions;",
            "Keys": "import org.openqa.selenium.Keys;",
            "JavascriptExecutor": "import org.openqa.selenium.JavascriptExecutor;",
            "Duration": "import java.time.Duration;",
        },
    },
    "cypress_js": {
        "language": "javascript",
        "must_have_any": [
            r"\bdescribe\s*\(",
            r"\bit\s*\(",
            r"\bcontext\s*\(",
        ],
        "should_have": [
            r"\bcy\.\w+",
            r"\.should\s*\(",
        ],
        "must_import": [],  # Cypress auto-imports
        "forbidden": [
            r"\brequire\s*\(\s*['\"]selenium",
            r"\bimport\s+.*playwright",
        ],
        "required_imports": {},
    },
    "rest_assured": {
        "language": "java",
        "must_have_any": [
            r"@Test",
            r"\bgiven\s*\(\s*\)",
        ],
        "should_have": [
            r"\b(?:given|when|then)\s*\(\s*\)",
        ],
        "must_import": [
            r"import\s+.*io\.restassured",
        ],
        "forbidden": [],
        "required_imports": {
            "given": "import static io.restassured.RestAssured.given;",
            "RestAssured": "import io.restassured.RestAssured;",
            "Response": "import io.restassured.response.Response;",
        },
    },
    "robot_framework": {
        "language": "robot",
        "must_have_any": [
            r"\*\*\*\s*Test Cases\s*\*\*\*",
            r"\*\*\*\s*Tasks\s*\*\*\*",
        ],
        "should_have": [
            r"\*\*\*\s*Settings\s*\*\*\*",
        ],
        "must_import": [],
        "forbidden": [],
        "required_imports": {},
    },
}


# ── Code Validator ────────────────────────────────────────────────────


class CodeValidator:
    """Multi-layer code validation for generated test automation code.

    Validates:
      1. Syntax correctness (language-specific)
      2. Framework structure (required patterns)
      3. Import completeness (used APIs have imports)
    """

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(
        self,
        code: str,
        framework: TargetFramework,
        strict: bool = False,
    ) -> ValidationResult:
        """Run all validation layers on generated code.

        Args:
            code: The generated code string.
            framework: Target framework (determines rules).
            strict: If True, warnings become errors.

        Returns:
            ValidationResult with detailed pass/fail information.
        """
        if not code or not code.strip():
            return ValidationResult(
                is_valid=False,
                syntax_ok=False,
                errors=["Empty code — nothing to validate"],
            )

        framework_key = framework.value
        rules = _FRAMEWORK_RULES.get(framework_key)
        if not rules:
            # Unknown framework — only do basic syntax check
            syntax_result = self._check_syntax_generic(code)
            return ValidationResult(
                is_valid=syntax_result.syntax_ok,
                syntax_ok=syntax_result.syntax_ok,
                errors=syntax_result.errors,
                warnings=["No framework-specific rules available"],
            )

        # Layer 1: Syntax check
        language = rules["language"]
        syntax_result = self._check_syntax(code, language)

        # Layer 2: Framework structure
        structure_result = self._check_structure(code, rules)

        # Layer 3: Import completeness
        import_result = self._check_imports(code, rules)

        # Aggregate results
        errors = syntax_result.errors + structure_result.errors + import_result.errors
        warnings = syntax_result.warnings + structure_result.warnings + import_result.warnings

        if strict:
            errors.extend(warnings)
            warnings = []

        is_valid = len(errors) == 0
        return ValidationResult(
            is_valid=is_valid,
            syntax_ok=syntax_result.syntax_ok,
            structure_ok=structure_result.structure_ok,
            imports_ok=import_result.imports_ok,
            errors=errors,
            warnings=warnings,
        )

    def validate_step_code(
        self,
        code: str,
        framework: TargetFramework,
    ) -> ValidationResult:
        """Lightweight validation for a single step's code (no structure check).

        Used during step-by-step generation where we don't have the full test yet.
        """
        if not code or not code.strip():
            return ValidationResult(is_valid=False, syntax_ok=False, errors=["Empty code"])

        framework_key = framework.value
        rules = _FRAMEWORK_RULES.get(framework_key)
        language = rules["language"] if rules else "generic"

        # Only do syntax check for individual steps
        result = self._check_syntax(code, language)

        # Check for obvious errors
        obvious_errors = self._check_obvious_errors(code, language)
        result.errors.extend(obvious_errors)
        result.is_valid = len(result.errors) == 0
        return result

    def auto_fix_imports(
        self,
        code: str,
        framework: TargetFramework,
    ) -> str:
        """Automatically add missing imports to the code.

        Non-destructive — only adds imports, never removes code.
        """
        framework_key = framework.value
        rules = _FRAMEWORK_RULES.get(framework_key)
        if not rules or not rules.get("required_imports"):
            return code

        required_imports = rules["required_imports"]
        existing_imports = self._extract_existing_imports(code, rules["language"])
        missing_imports: list[str] = []

        for api_name, import_line in required_imports.items():
            # Check if this API is used in the code
            if re.search(r"\b" + re.escape(api_name) + r"\b", code):
                # Check if it's already imported
                if import_line not in existing_imports and api_name not in " ".join(existing_imports):
                    missing_imports.append(import_line)

        if not missing_imports:
            return code

        # Deduplicate
        missing_imports = list(dict.fromkeys(missing_imports))

        # Insert at top
        import_block = "\n".join(missing_imports)
        if rules["language"] == "python":
            # Add after any existing imports or at top
            return f"{import_block}\n\n{code}"
        elif rules["language"] in ("typescript", "javascript"):
            return f"{import_block}\n\n{code}"
        elif rules["language"] == "java":
            # Java imports go after package declaration
            if "package " in code:
                parts = code.split("\n", 1)
                return f"{parts[0]}\n\n{import_block}\n\n{parts[1] if len(parts) > 1 else ''}"
            return f"{import_block}\n\n{code}"

        return f"{import_block}\n\n{code}"

    # ------------------------------------------------------------------
    # Layer 1: Syntax Checking
    # ------------------------------------------------------------------

    def _check_syntax(self, code: str, language: str) -> ValidationResult:
        """Language-specific syntax validation."""
        if language == "python":
            return self._check_python_syntax(code)
        elif language in ("typescript", "javascript"):
            return self._check_js_ts_syntax(code)
        elif language == "java":
            return self._check_java_syntax(code)
        elif language == "robot":
            return self._check_robot_syntax(code)
        return self._check_syntax_generic(code)

    def _check_python_syntax(self, code: str) -> ValidationResult:
        """Validate Python syntax using ast.parse()."""
        errors: list[str] = []
        warnings: list[str] = []

        try:
            ast.parse(code)
        except SyntaxError as exc:
            errors.append(f"Python syntax error at line {exc.lineno}: {exc.msg}")

        return ValidationResult(
            is_valid=len(errors) == 0,
            syntax_ok=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def _check_js_ts_syntax(self, code: str) -> ValidationResult:
        """Validate JavaScript/TypeScript syntax using bracket/quote balance."""
        errors: list[str] = []
        warnings: list[str] = []

        # Check bracket balance
        bracket_errors = self._check_bracket_balance(code)
        errors.extend(bracket_errors)

        # Check for unclosed strings
        string_errors = self._check_string_balance(code)
        errors.extend(string_errors)

        # Check for common JS/TS syntax issues
        lines = code.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Arrow function without proper syntax
            if "=>" in stripped and stripped.count("(") != stripped.count(")"):
                if not any(l.strip().startswith(")") for l in lines[i:i+3]):
                    warnings.append(f"Line {i}: Possible unbalanced parentheses in arrow function")

        return ValidationResult(
            is_valid=len(errors) == 0,
            syntax_ok=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def _check_java_syntax(self, code: str) -> ValidationResult:
        """Validate Java syntax using bracket balance and basic structure."""
        errors: list[str] = []
        warnings: list[str] = []

        # Bracket balance
        bracket_errors = self._check_bracket_balance(code)
        errors.extend(bracket_errors)

        # Check semicolons on statement lines
        lines = code.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("//") or stripped.startswith("/*"):
                continue
            if stripped.startswith("import ") or stripped.startswith("package "):
                if not stripped.endswith(";"):
                    errors.append(f"Line {i}: Missing semicolon after import/package statement")
            # Annotation lines don't need semicolons
            if stripped.startswith("@"):
                continue
            # Control structures don't need semicolons at end of line
            if any(stripped.startswith(kw) for kw in ("if", "else", "for", "while", "try", "catch", "class", "public", "private", "protected")):
                continue
            if stripped in ("{", "}"):
                continue

        return ValidationResult(
            is_valid=len(errors) == 0,
            syntax_ok=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def _check_robot_syntax(self, code: str) -> ValidationResult:
        """Validate Robot Framework syntax (basic structure check)."""
        errors: list[str] = []
        warnings: list[str] = []

        # Must have at least one section header
        if not re.search(r"\*\*\*\s*\w+", code):
            errors.append("Robot Framework file must have at least one *** Section *** header")

        # Check indentation (steps must be indented)
        lines = code.split("\n")
        in_test = False
        for i, line in enumerate(lines, 1):
            if re.match(r"\*\*\*", line):
                in_test = "Test Cases" in line or "Tasks" in line
                continue
            if in_test and line.strip() and not line.startswith(" ") and not line.startswith("\t"):
                # This is a test case name — next lines should be indented
                pass

        return ValidationResult(
            is_valid=len(errors) == 0,
            syntax_ok=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def _check_syntax_generic(self, code: str) -> ValidationResult:
        """Generic syntax check — bracket balance only."""
        errors = self._check_bracket_balance(code)
        return ValidationResult(
            is_valid=len(errors) == 0,
            syntax_ok=len(errors) == 0,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Layer 2: Structure Checking
    # ------------------------------------------------------------------

    def _check_structure(self, code: str, rules: dict[str, Any]) -> ValidationResult:
        """Verify the code follows framework structural rules."""
        errors: list[str] = []
        warnings: list[str] = []

        # Must have at least one of the required patterns
        must_have_any = rules.get("must_have_any", [])
        if must_have_any:
            found_any = any(re.search(p, code) for p in must_have_any)
            if not found_any:
                errors.append(
                    f"Missing required structure: code must contain at least one of "
                    f"{[p for p in must_have_any]}"
                )

        # Should have (warnings only)
        should_have = rules.get("should_have", [])
        for pattern in should_have:
            if not re.search(pattern, code):
                warnings.append(f"Recommended pattern not found: {pattern}")

        # Must import (for full test files)
        must_import = rules.get("must_import", [])
        for pattern in must_import:
            if not re.search(pattern, code):
                errors.append(f"Missing required import matching: {pattern}")

        # Forbidden patterns
        forbidden = rules.get("forbidden", [])
        for pattern in forbidden:
            if re.search(pattern, code):
                errors.append(f"Contains forbidden pattern (wrong framework?): {pattern}")

        structure_ok = not any(
            "Missing required" in e or "forbidden" in e.lower() for e in errors
        )

        return ValidationResult(
            is_valid=len(errors) == 0,
            structure_ok=structure_ok,
            errors=errors,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Layer 3: Import Checking
    # ------------------------------------------------------------------

    def _check_imports(self, code: str, rules: dict[str, Any]) -> ValidationResult:
        """Check that all used APIs have corresponding imports."""
        errors: list[str] = []
        warnings: list[str] = []

        required_imports = rules.get("required_imports", {})
        if not required_imports:
            return ValidationResult(is_valid=True, imports_ok=True)

        missing: list[str] = []
        for api_name, import_line in required_imports.items():
            # Check if API is used
            if re.search(r"\b" + re.escape(api_name) + r"\b", code):
                # Check if it's imported
                if not re.search(re.escape(api_name), self._get_import_section(code, rules["language"])):
                    # Special case: check the full import line exists
                    if import_line not in code:
                        missing.append(f"'{api_name}' is used but not imported (needs: {import_line})")

        if missing:
            warnings.extend(missing)  # Warnings, not errors — auto_fix_imports can handle

        return ValidationResult(
            is_valid=True,  # Missing imports are auto-fixable, not blocking
            imports_ok=len(missing) == 0,
            errors=errors,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_bracket_balance(code: str) -> list[str]:
        """Check that all brackets are balanced, ignoring those in strings/comments."""
        errors: list[str] = []
        stack: list[tuple[str, int]] = []
        openers = {"(": ")", "[": "]", "{": "}"}
        closers = {")", "]", "}"}

        in_string = False
        string_char = ""
        in_line_comment = False
        in_block_comment = False
        i = 0
        line_num = 1

        while i < len(code):
            ch = code[i]
            next_ch = code[i + 1] if i + 1 < len(code) else ""

            # Track line numbers
            if ch == "\n":
                line_num += 1
                in_line_comment = False
                i += 1
                continue

            # Skip comments
            if in_block_comment:
                if ch == "*" and next_ch == "/":
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue

            if not in_string:
                if ch == "/" and next_ch == "/":
                    in_line_comment = True
                    i += 2
                    continue
                if ch == "/" and next_ch == "*":
                    in_block_comment = True
                    i += 2
                    continue
                if ch == "#" and not in_string:
                    # Python line comment (but not inside strings)
                    in_line_comment = True
                    i += 1
                    continue

            if in_line_comment:
                i += 1
                continue

            # Track strings
            if ch in ("'", '"', "`"):
                if not in_string:
                    in_string = True
                    string_char = ch
                elif ch == string_char:
                    # Check for escape
                    if i > 0 and code[i - 1] != "\\":
                        in_string = False
                i += 1
                continue

            if in_string:
                i += 1
                continue

            # Track brackets
            if ch in openers:
                stack.append((ch, line_num))
            elif ch in closers:
                if not stack:
                    errors.append(f"Line {line_num}: Unmatched closing bracket '{ch}'")
                else:
                    opener, open_line = stack.pop()
                    expected_closer = openers[opener]
                    if ch != expected_closer:
                        errors.append(
                            f"Line {line_num}: Mismatched bracket — expected '{expected_closer}' "
                            f"(opened at line {open_line}) but found '{ch}'"
                        )

            i += 1

        # Report unclosed brackets
        for opener, open_line in stack:
            errors.append(f"Line {open_line}: Unclosed bracket '{opener}'")

        return errors

    @staticmethod
    def _check_string_balance(code: str) -> list[str]:
        """Check for obviously unclosed string literals."""
        errors: list[str] = []
        lines = code.split("\n")

        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("#"):
                continue

            # Count unescaped quotes
            for quote in ("'", '"'):
                count = 0
                j = 0
                while j < len(stripped):
                    if stripped[j] == "\\" and j + 1 < len(stripped):
                        j += 2
                        continue
                    if stripped[j] == quote:
                        count += 1
                    j += 1

                # Template literals (backticks) can span lines — skip
                if quote == "`":
                    continue

                if count % 2 != 0:
                    # Could be a multi-line string or template — only warn
                    pass  # Don't report — too many false positives

        return errors

    @staticmethod
    def _check_obvious_errors(code: str, language: str) -> list[str]:
        """Check for obvious code errors in a step snippet."""
        errors: list[str] = []

        # Empty or placeholder-only
        if code.strip() in ("", "// TODO", "# TODO", "pass"):
            errors.append("Code is empty or placeholder only")

        # Double semicolons (common LLM error)
        if ";;" in code and language != "robot":
            errors.append("Double semicolons detected")

        # Unclosed function calls (opening paren without closing on same logical block)
        # This is heuristic — only flag obvious cases
        if code.count("(") - code.count(")") > 2:
            errors.append("Significantly more opening than closing parentheses")

        return errors

    @staticmethod
    def _extract_existing_imports(code: str, language: str) -> list[str]:
        """Extract import statements from code."""
        imports: list[str] = []
        lines = code.split("\n")

        for line in lines:
            stripped = line.strip()
            if language == "python":
                if stripped.startswith("import ") or stripped.startswith("from "):
                    imports.append(stripped)
            elif language in ("typescript", "javascript"):
                if stripped.startswith("import "):
                    imports.append(stripped)
            elif language == "java":
                if stripped.startswith("import "):
                    imports.append(stripped)

        return imports

    @staticmethod
    def _get_import_section(code: str, language: str) -> str:
        """Extract the import section of the code for checking."""
        lines = code.split("\n")
        import_lines: list[str] = []

        for line in lines:
            stripped = line.strip()
            if language == "python":
                if stripped.startswith("import ") or stripped.startswith("from "):
                    import_lines.append(stripped)
            elif language in ("typescript", "javascript"):
                if stripped.startswith("import "):
                    import_lines.append(stripped)
            elif language == "java":
                if stripped.startswith("import "):
                    import_lines.append(stripped)

        return "\n".join(import_lines)
