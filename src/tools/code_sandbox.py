"""Venv-based Python sandbox for the Code Studio tab.

Flow per run_code() call:
  1. Pre-check available disk space.
  2. Parse imports from user code via AST.
  3. Map import names to pip package names.
  4. Create (or reuse) a persistent venv with --system-site-packages so
     packages already in the main Python env (requests, pandas, pytest, etc.)
     are inherited without copying, keeping the venv lean (~5 MB overhead).
  5. pip-install only packages not already present (cached across runs).
  6. Write code to a temp .py file and execute with the venv python.
  7. Return SandboxResult(install_log, stdout, stderr, exit_code, timed_out).

NOTE on Playwright browsers:
  playwright (pip package) is installed automatically when imported.
  Browser binaries (chromium etc.) are NOT auto-installed because they are
  ~150 MB each. The user must run:
      playwright install chromium
  once manually, or the test will fail with a browser-not-found error.
  The install log will show this hint when playwright is detected.
"""
from __future__ import annotations

import ast
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────
_EXEC_TIMEOUT    = 60    # seconds for user code execution
_INSTALL_TIMEOUT = 120   # seconds per pip install call
_MIN_FREE_MB     = 100   # warn if less than this is free before installing

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_VENV_DIR     = _PROJECT_ROOT / ".sandbox_venv"

# Allow override via env var for machines with limited C: space.
# Set SANDBOX_VENV_PATH=D:\sandbox_venv in config/.env
_override = os.environ.get("SANDBOX_VENV_PATH", "").strip()
if _override:
    _VENV_DIR = Path(_override)

# ── Import -> pip package mapping ─────────────────────────────────────
_IMPORT_TO_PIP: dict[str, str | None] = {
    "playwright":    "playwright",
    "pytest":        "pytest",
    "selenium":      "selenium",
    "robot":         "robotframework",
    "appium":        "Appium-Python-Client",
    "cypress":       None,
    "requests":      "requests",
    "httpx":         "httpx",
    "aiohttp":       "aiohttp",
    "urllib3":       "urllib3",
    "pandas":        "pandas",
    "numpy":         "numpy",
    "openpyxl":      "openpyxl",
    "xlrd":          "xlrd",
    "faker":         "Faker",
    "assertpy":      "assertpy",
    "behave":        "behave",
    "allure":        "allure-pytest",
    "locust":        "locust",
    "k6":            None,
    "allure_pytest": "allure-pytest",
    "pytest_html":   "pytest-html",
    "dotenv":        "python-dotenv",
    "yaml":          "pyyaml",
    "toml":          "toml",
    "jsonschema":    "jsonschema",
    "parameterized": "parameterized",
    "mock":          "mock",
    "responses":     "responses",
    "freezegun":     "freezegun",
    "PIL":           "Pillow",
    "cv2":           "opencv-python",
    "bs4":           "beautifulsoup4",
    "lxml":          "lxml",
    "sqlalchemy":    "SQLAlchemy",
    "psycopg2":      "psycopg2-binary",
    "pymongo":       "pymongo",
    "redis":         "redis",
    "boto3":         "boto3",
    "paramiko":      "paramiko",
    "cryptography":  "cryptography",
    "jwt":           "PyJWT",
    "pydantic":      "pydantic",
    "fastapi":       "fastapi",
    "uvicorn":       "uvicorn",
}

_STDLIB = frozenset(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else frozenset({
    "os", "sys", "re", "io", "abc", "ast", "copy", "csv", "json", "math",
    "time", "datetime", "random", "string", "struct", "typing", "pathlib",
    "functools", "itertools", "collections", "contextlib", "dataclasses",
    "enum", "logging", "unittest", "traceback", "inspect", "importlib",
    "subprocess", "threading", "multiprocessing", "socket", "http", "urllib",
    "email", "html", "xml", "hashlib", "hmac", "base64", "binascii",
    "tempfile", "shutil", "glob", "fnmatch", "stat", "platform", "signal",
    "textwrap", "pprint", "warnings", "weakref", "gc", "builtins",
})

_BLOCKED_PATTERNS: list[str] = [
    "os.system(",
    "__import__('os').system",
    "shutil.rmtree(",
]


# ── Data classes ──────────────────────────────────────────────────────

@dataclass
class SandboxResult:
    stdout:             str
    stderr:             str
    exit_code:          int
    install_log:        str = ""
    timed_out:          bool = False
    packages_installed: list[str] = field(default_factory=list)
    packages_required:  list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def output(self) -> str:
        parts: list[str] = []
        if self.install_log:
            parts.append(self.install_log)
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append("[stderr]\n" + self.stderr)
        if self.timed_out:
            parts.append(f"\n[error] Execution timed out after {_EXEC_TIMEOUT}s")
        return "\n".join(parts) or "(no output)"


# ── Internal helpers ──────────────────────────────────────────────────

def _venv_python() -> Path:
    if sys.platform == "win32":
        return _VENV_DIR / "Scripts" / "python.exe"
    return _VENV_DIR / "bin" / "python"


def _free_mb() -> int:
    try:
        drive = str(_VENV_DIR.anchor)
        return shutil.disk_usage(drive).free // (1024 * 1024)
    except Exception:
        return 9999


def _extract_imports(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module.split(".")[0])
    return list(dict.fromkeys(names))


def _imports_to_packages(imports: list[str]) -> list[str]:
    pkgs: list[str] = []
    for imp in imports:
        if imp in _STDLIB:
            continue
        if imp in _IMPORT_TO_PIP:
            pip_name = _IMPORT_TO_PIP[imp]
            if pip_name is not None:
                pkgs.append(pip_name)
        else:
            pkgs.append(imp)
    return list(dict.fromkeys(pkgs))


def _ensure_venv() -> tuple[bool, str]:
    if _venv_python().exists():
        return True, ""
    free = _free_mb()
    if free < 50:
        return False, (
            f"[error] Only {free}MB free on {_VENV_DIR.anchor} -- cannot create venv. "
            "Free up disk space or set SANDBOX_VENV_PATH in config/.env to a drive with more room.\n"
        )
    logger.info("Sandbox: creating venv at %s", _VENV_DIR)
    try:
        # --system-site-packages inherits packages from the main Python env
        # (requests, pandas, numpy, pytest, etc.) so the venv stays lean.
        subprocess.run(
            [sys.executable, "-m", "venv", "--system-site-packages", str(_VENV_DIR)],
            check=True, capture_output=True, text=True, timeout=60,
        )
        subprocess.run(
            [str(_venv_python()), "-m", "pip", "install", "--upgrade", "pip", "-q"],
            check=True, capture_output=True, text=True, timeout=60,
        )
        return True, f"[setup] Created sandbox venv at {_VENV_DIR}\n"
    except Exception as exc:
        logger.error("Sandbox: venv creation failed: %s", exc)
        return False, f"[error] Failed to create venv: {exc}\n"


def _installed_packages() -> set[str]:
    try:
        result = subprocess.run(
            [str(_venv_python()), "-m", "pip", "list", "--format=freeze"],
            capture_output=True, text=True, timeout=30,
        )
        return {
            line.split("==")[0].lower()
            for line in result.stdout.splitlines()
            if "==" in line
        }
    except Exception:
        return set()


def _install_packages(packages: list[str]) -> tuple[list[str], str]:
    if not packages:
        return [], ""

    already = _installed_packages()
    to_install = [p for p in packages if p.lower() not in already]

    if not to_install:
        return [], f"[cached] Already installed: {', '.join(packages)}\n"

    # Disk space check before installing
    free = _free_mb()
    if free < _MIN_FREE_MB:
        msg = (
            f"[warn] Only {free}MB free -- skipping install of: {', '.join(to_install)}. "
            "Free up disk space or set SANDBOX_VENV_PATH in config/.env.\n"
        )
        return [], msg

    log_lines: list[str] = [f"[install] Installing: {', '.join(to_install)}"]
    installed: list[str] = []

    for pkg in to_install:
        try:
            proc = subprocess.run(
                [str(_venv_python()), "-m", "pip", "install", pkg, "-q",
                 "--disable-pip-version-check"],
                capture_output=True, text=True, timeout=_INSTALL_TIMEOUT,
            )
            if proc.returncode == 0:
                log_lines.append(f"  [ok] {pkg}")
                installed.append(pkg)
            else:
                all_lines = (proc.stdout + proc.stderr).strip().splitlines()
                last_err = all_lines[-1] if all_lines else "unknown error"
                log_lines.append(f"  [FAILED] {pkg}: {last_err}")
                if "no space left" in last_err.lower() or "errno 28" in last_err.lower():
                    log_lines.append(
                        "  [hint] Disk full -- set SANDBOX_VENV_PATH=D:\\sandbox_venv "
                        "in config/.env and restart Streamlit"
                    )
        except subprocess.TimeoutExpired:
            log_lines.append(f"  [timeout] {pkg}: install timed out")
        except Exception as exc:
            log_lines.append(f"  [error] {pkg}: {exc}")

    # Playwright pip package installed -- remind user about browsers
    if "playwright" in [p.lower() for p in installed]:
        log_lines.append(
            "  [note] Playwright pip package installed. "
            "Browser binaries are NOT auto-installed (they are ~150MB each). "
            "To run browser tests, open a terminal and run: "
            ".sandbox_venv\\Scripts\\playwright install chromium"
        )

    return installed, "\n".join(log_lines) + "\n"


def _check_blocked(code: str) -> str | None:
    for pattern in _BLOCKED_PATTERNS:
        if pattern in code:
            return f"Blocked pattern: '{pattern}'"
    return None


# ── Public API ────────────────────────────────────────────────────────

def detect_required_packages(code: str) -> list[str]:
    """Return pip packages required by the code without installing anything."""
    return _imports_to_packages(_extract_imports(code))


def uninstall_packages(packages: list[str]) -> str:
    """Uninstall the given packages from the sandbox venv. Returns a log string."""
    if not packages or not _venv_python().exists():
        return ""
    log_lines: list[str] = [f"[uninstall] Removing: {', '.join(packages)}"]
    for pkg in packages:
        try:
            proc = subprocess.run(
                [str(_venv_python()), "-m", "pip", "uninstall", pkg, "-y", "-q",
                 "--disable-pip-version-check"],
                capture_output=True, text=True, timeout=60,
            )
            if proc.returncode == 0:
                log_lines.append(f"  [ok] {pkg} removed")
            else:
                log_lines.append(f"  [warn] {pkg}: {(proc.stdout + proc.stderr).strip().splitlines()[-1:] or 'unknown'}")
        except Exception as exc:
            log_lines.append(f"  [error] {pkg}: {exc}")
    return "\n".join(log_lines)


def run_code(code: str, timeout: int = _EXEC_TIMEOUT) -> SandboxResult:
    """Execute Python code in a managed venv sandbox.

    Packages are NOT installed automatically. Call install_and_run() instead
    if you want auto-install behaviour, or install packages explicitly before
    calling this function.
    """
    block_err = _check_blocked(code)
    if block_err:
        return SandboxResult(stdout="", stderr=block_err, exit_code=1)

    log_parts: list[str] = []

    venv_ok, venv_log = _ensure_venv()
    if venv_log:
        log_parts.append(venv_log)
    if not venv_ok:
        return SandboxResult(
            stdout="", stderr="Venv creation failed -- see install log.",
            exit_code=1, install_log="".join(log_parts),
        )

    required = detect_required_packages(code)
    install_log = "".join(log_parts).strip()

    wrapped = textwrap.dedent(f"""\
import traceback, sys
try:
{textwrap.indent(code, '    ')}
except SystemExit:
    pass
except Exception:
    traceback.print_exc()
    sys.exit(1)
""")

    tmp_file: Path | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".py", prefix="sandbox_")
        tmp_file = Path(tmp_path)
        os.close(fd)
        tmp_file.write_text(wrapped, encoding="utf-8")

        proc = subprocess.run(
            [str(_venv_python()), str(tmp_file)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(_PROJECT_ROOT),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        return SandboxResult(
            stdout=proc.stdout.strip(),
            stderr=proc.stderr.strip(),
            exit_code=proc.returncode,
            install_log=install_log,
            packages_required=required,
        )

    except subprocess.TimeoutExpired:
        logger.warning("Sandbox: execution timed out after %ds", timeout)
        return SandboxResult(
            stdout="", stderr="", exit_code=1,
            install_log=install_log, timed_out=True,
            packages_required=required,
        )
    except Exception as exc:
        logger.error("Sandbox: unexpected error: %s", exc)
        return SandboxResult(
            stdout="", stderr=str(exc), exit_code=1,
            install_log=install_log,
            packages_required=required,
        )
    finally:
        if tmp_file and tmp_file.exists():
            try:
                tmp_file.unlink()
            except Exception:
                pass


def install_and_run(code: str, packages: list[str], timeout: int = _EXEC_TIMEOUT) -> SandboxResult:
    """Install the given packages then execute code. Returns combined result."""
    log_parts: list[str] = []
    installed: list[str] = []

    venv_ok, venv_log = _ensure_venv()
    if venv_log:
        log_parts.append(venv_log)
    if not venv_ok:
        return SandboxResult(
            stdout="", stderr="Venv creation failed.",
            exit_code=1, install_log="".join(log_parts),
        )

    if packages:
        installed, pkg_log = _install_packages(packages)
        log_parts.append(pkg_log)

    result = run_code(code, timeout=timeout)
    combined_log = ("\n".join(log_parts).strip() + "\n" + result.install_log).strip()
    result.install_log = combined_log
    result.packages_installed = installed
    return result


def get_venv_info() -> dict:
    """Return venv status dict for display in the UI."""
    exists = _venv_python().exists()
    pkgs   = sorted(_installed_packages()) if exists else []
    return {
        "venv_path": str(_VENV_DIR),
        "exists":    exists,
        "python":    str(_venv_python()),
        "packages":  pkgs,
        "count":     len(pkgs),
        "free_mb":   _free_mb(),
    }
