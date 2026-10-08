"""One-shot setup for the Automation Framework Advisor MCP server.

Run this ONE script after cloning/unzipping this project. It:

  1. Creates a project-local virtual environment (``.venv``) if one
     doesn't already exist.
  2. Installs ``requirements.txt`` into it (skippable with --no-install
     if you've already installed dependencies yourself, e.g. into a
     different environment via --python).
  3. Registers the MCP server in a Kiro ``mcp.json`` — auto-detecting the
     interpreter (the venv it just created/found) and this project's
     absolute root directory — merging the entry in without disturbing
     any other MCP servers already configured there.

No GROQ_API_KEY is required. The code-conversion/generation tools work
without one — see docs/MCP_SERVER_SETUP.md. Pass --groq-key only if you
specifically want those tools to call Groq server-side instead of
handing the work to Kiro's own model.

Usage:
    python scripts/setup_mcp.py                   # full setup, workspace scope
    python scripts/setup_mcp.py --user             # user scope (all workspaces)
    python scripts/setup_mcp.py --no-install       # skip venv/pip, just configure mcp.json
    python scripts/setup_mcp.py --groq-key KEY     # optional — see note above
    python scripts/setup_mcp.py --python PATH      # use an existing interpreter instead of creating a venv
    python scripts/setup_mcp.py --name my-advisor  # register under a different key
    python scripts/setup_mcp.py --dry-run          # print the result, write/install nothing

Workspace scope writes to <this-repo>/.kiro/settings/mcp.json — only
available when this repo is open in Kiro. User scope writes to
~/.kiro/settings/mcp.json — available in every Kiro workspace, which is
what you want if you plan to call these tools while working in *other*
projects/repos (see docs/MCP_SERVER_SETUP.md, "Using these tools from a
different test repo").
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_SCRIPT = REPO_ROOT / "mcp_server" / "server.py"
REQUIREMENTS_FILE = REPO_ROOT / "requirements.txt"
VENV_DIR = REPO_ROOT / ".venv"
DEFAULT_SERVER_NAME = "automation-framework-advisor"


def _venv_python() -> Path:
    """Path to the interpreter inside this project's .venv, OS-dependent."""
    if platform.system() == "Windows":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _ensure_venv_and_deps(dry_run: bool) -> str:
    """Create .venv (if missing) and install requirements.txt into it.

    Returns the path to the venv's Python interpreter. Idempotent — safe
    to run again on an already-set-up project (pip install is a no-op
    when everything is already satisfied).
    """
    venv_python = _venv_python()

    if not venv_python.exists():
        print(f"Creating virtual environment at {VENV_DIR} ...")
        if not dry_run:
            result = subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)])
            if result.returncode != 0:
                raise SystemExit(
                    "Failed to create a virtual environment. Install Python's "
                    "`venv` module, or pass --python to use an existing "
                    "interpreter with --no-install."
                )
    else:
        print(f"Using existing virtual environment at {VENV_DIR}")

    if not REQUIREMENTS_FILE.exists():
        print(f"Warning: {REQUIREMENTS_FILE} not found — skipping dependency install.")
        return str(venv_python)

    print(f"Installing dependencies from {REQUIREMENTS_FILE.name} ...")
    if not dry_run:
        result = subprocess.run(
            [str(venv_python), "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS_FILE)]
        )
        if result.returncode != 0:
            raise SystemExit(
                "`pip install -r requirements.txt` failed — see output above. "
                "Fix the error and re-run this script."
            )
        print("Dependencies installed.")
    else:
        print(f"(dry run — would run: {venv_python} -m pip install -r {REQUIREMENTS_FILE})")

    return str(venv_python)


def _detect_python() -> str:
    """Fall back path when --no-install is passed: use whichever
    interpreter is running this script, or `python`/`python3` on PATH."""
    venv_python = _venv_python()
    if venv_python.exists():
        return str(venv_python)

    if sys.executable:
        return sys.executable

    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return found

    raise SystemExit(
        "Could not find a Python interpreter. Install dependencies with "
        "`pip install -r requirements.txt` in a venv, or pass --python explicitly."
    )


def _default_config_path(user_scope: bool) -> Path:
    if user_scope:
        return Path.home() / ".kiro" / "settings" / "mcp.json"
    return REPO_ROOT / ".kiro" / "settings" / "mcp.json"


def _load_existing(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {"mcpServers": {}}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        backup = config_path.with_suffix(".json.bak")
        shutil.copy2(config_path, backup)
        print(
            f"Warning: existing {config_path} was not valid JSON ({exc}). "
            f"Backed it up to {backup} and starting fresh."
        )
        return {"mcpServers": {}}
    data.setdefault("mcpServers", {})
    return data


def build_server_entry(python_path: str, groq_key: str | None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "command": python_path,
        "args": [str(SERVER_SCRIPT)],
        "cwd": str(REPO_ROOT),
        "disabled": False,
    }
    if groq_key:
        entry["env"] = {"GROQ_API_KEY": groq_key}
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--user", action="store_true",
        help="Write to the user-level ~/.kiro/settings/mcp.json instead of "
             "this repo's workspace-level config. Use this if you want the "
             "tools available while working in OTHER projects/workspaces.",
    )
    parser.add_argument(
        "--name", default=DEFAULT_SERVER_NAME,
        help=f"Key to register the server under (default: {DEFAULT_SERVER_NAME}). "
             "Use a different name if you need multiple instances side by side.",
    )
    parser.add_argument(
        "--python", default=None,
        help="Use this interpreter instead of creating/using a .venv. "
             "Implies --no-install unless requirements.txt is installed "
             "into it separately.",
    )
    parser.add_argument(
        "--no-install", action="store_true",
        help="Skip creating a .venv / installing requirements.txt — just "
             "(re)configure mcp.json. Use this if dependencies are already "
             "installed (e.g. you manage your own environment).",
    )
    parser.add_argument(
        "--groq-key", default=None,
        help="OPTIONAL. GROQ_API_KEY is not required to use this server — "
             "the code-conversion/generation tools work without it by "
             "handing the work to Kiro's own model instead of calling an "
             "external API. Only pass this if you specifically want those "
             "tools to call Groq server-side themselves.",
    )
    parser.add_argument(
        "--config-path", default=None,
        help="Explicit path to the mcp.json to write (overrides --user).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would happen (venv/install/mcp.json) without "
             "actually installing anything or writing any file.",
    )
    args = parser.parse_args()

    if not SERVER_SCRIPT.exists():
        raise SystemExit(f"Could not find {SERVER_SCRIPT} — run this script from the cloned repo.")

    if args.python:
        python_path = args.python
        if not args.no_install:
            print(f"Installing dependencies into {python_path} ...")
            if not args.dry_run:
                result = subprocess.run(
                    [python_path, "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS_FILE)]
                )
                if result.returncode != 0:
                    raise SystemExit("pip install failed — see output above.")
            else:
                print(f"(dry run — would run: {python_path} -m pip install -r {REQUIREMENTS_FILE})")
    elif args.no_install:
        python_path = _detect_python()
    else:
        python_path = _ensure_venv_and_deps(args.dry_run)

    config_path = Path(args.config_path) if args.config_path else _default_config_path(args.user)

    config = _load_existing(config_path)
    config["mcpServers"][args.name] = build_server_entry(python_path, args.groq_key)

    rendered = json.dumps(config, indent=2)

    print()
    print(f"Platform:   {platform.system()}")
    print(f"Python:     {python_path}")
    print(f"Server cwd: {REPO_ROOT}")
    print(f"Config:     {config_path} ({'user' if args.user else 'workspace'} scope)")
    print()
    print(rendered)

    if args.dry_run:
        print("\n(dry run — nothing installed or written)")
        return

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(rendered + "\n", encoding="utf-8")

    print(f"\nWrote '{args.name}' MCP server entry to {config_path}")
    print(
        "Reconnect it from Kiro's MCP Server view (or run the "
        "'MCP: Reconnect' command) — no IDE restart needed."
    )
    if not args.groq_key:
        print(
            "\nNo GROQ_API_KEY configured — that's fine, it's optional. "
            "The code-conversion/generation tools will hand their work to "
            "Kiro's own model instead of calling Groq."
        )


if __name__ == "__main__":
    main()
