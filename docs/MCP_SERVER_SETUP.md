# Automation Framework Advisor — MCP Server Setup

This project ships an MCP (Model Context Protocol) server at
`mcp_server/server.py` that wraps every capability of the Advisor —
framework recommendations, scoring, migration planning, coverage gap
analysis, cross-framework test-code conversion, test-code generation from
manual test cases, cross-repo test parity comparison, and live framework
discovery — as individually callable tools. Any MCP client (Kiro, Claude
Desktop, etc.) can call these tools directly, without the Streamlit UI.

It's a thin wrapper: all 20 tools delegate to the same `ToolExecutor`,
`ScoringEngine`, `MigrationPlanner`, `CoverageAnalyzer`,
`BoilerplateGenerator`, `CodeGenOrchestrator`, `repo_comparator`, and
`FrameworkScanner` modules the Streamlit app already uses — so behaviour
is identical to the UI.

## 1. Prerequisites

- Python 3.11+ — that's it. No API key, no manual `pip install` step —
  the setup script in section 2 handles dependencies for you.

### A note on GROQ_API_KEY — it's optional

This server does **not** require a Groq (or any other LLM) API key.
When no key is configured, the three code-conversion/generation tools
(`convert_test_code`, `convert_test_project`, `generate_test_code`)
don't fail — they return a structured brief (framework-specific
conversion/generation rules + the source material) and ask the calling
assistant to produce the result directly. Since you're using this from
Kiro, that means **Kiro's own model does the conversion** — no external
API call, no key needed.

Setting `GROQ_API_KEY` only changes those three tools to perform the
work server-side via Groq instead — useful for headless/CI use where no
assistant is in the loop, but not needed for interactive Kiro usage.
Every other tool (recommendations, scoring, migration planning,
coverage analysis, repo comparison, boilerplate generation) never used
an API key in the first place.

If you do want to set one anyway: `--groq-key` on the setup script
below injects it directly into the MCP config, or you can add it to
`config/.env`:

```env
# config/.env
GROQ_API_KEY=your_key_here
GROQ_MODEL=openai/gpt-oss-120b
```

## 2. One-shot setup

Run this single script after cloning/unzipping the project:

```powershell
python scripts\setup_mcp.py
```

It does everything in order:

1. Creates a project-local virtual environment at `.venv` (skips this
   if one already exists).
2. Installs `requirements.txt` into it.
3. Registers the server in `.kiro/settings/mcp.json` (workspace scope),
   auto-detecting the venv interpreter it just set up and this repo's
   absolute path — merging the entry in without disturbing any other
   MCP servers already configured there.

Useful variations:

```powershell
# User scope — tools available in every Kiro workspace (any repo), not just this one
python scripts\setup_mcp.py --user

# Already have dependencies installed yourself? Skip venv/pip, just write mcp.json
python scripts\setup_mcp.py --no-install --python "C:\path\to\your\python.exe"

# Preview everything (venv creation, pip install, mcp.json) without doing any of it
python scripts\setup_mcp.py --dry-run

# Optional — only if you want convert_test_code / convert_test_project /
# generate_test_code to call Groq server-side instead of using Kiro's model
python scripts\setup_mcp.py --groq-key gsk_your_key_here
```

Run `python scripts\setup_mcp.py --help` for all options (custom server
name, explicit interpreter, explicit config file path).

The script is idempotent — re-running it after a `git pull` just
re-installs any new dependencies and rewrites the same config entry.

After it finishes, reconnect the server from Kiro's **MCP Server** view
(or run the `MCP: Reconnect` command) — no IDE restart needed.

## 3. Manual fallback / scope reference

Kiro discovers MCP servers from `mcp.json` files. You can register the
server at either scope:

- **Workspace scope**: `.kiro/settings/mcp.json` inside this repo —
  only takes effect when *this* repo is open in Kiro. This is what
  `python scripts\setup_mcp.py` writes to by default.
- **User scope**: `~/.kiro/settings/mcp.json` — takes effect in
  **every** Kiro workspace, regardless of which project you have open.
  Use `--user` if you want to call these tools while working in a
  *different* repo (see section 3b below) — this is the common case
  when migrating/converting test code that lives outside this project.

### 3a. Manual fallback

If you'd rather not run the script, install dependencies yourself
(`pip install -r requirements.txt`, ideally into a venv) then add this
to `mcpServers` in the relevant `mcp.json`, replacing both placeholders:

```json
{
  "mcpServers": {
    "automation-framework-advisor": {
      "command": "<ABSOLUTE_PATH_TO_PYTHON_WITH_DEPS_INSTALLED>",
      "args": ["<ABSOLUTE_PATH_TO_THIS_REPO>/mcp_server/server.py"],
      "cwd": "<ABSOLUTE_PATH_TO_THIS_REPO>",
      "disabled": false
    }
  }
}
```

Notes:

- `command` must point at a Python interpreter that has
  `requirements.txt` installed — this repo's `.venv\Scripts\python.exe`
  (Windows) / `.venv/bin/python` (macOS/Linux) if you created one, or
  your global interpreter otherwise.
- `cwd` must be this repo's root (not `mcp_server/`) — the server
  resolves `data/frameworks`, `config/.env`, and `logs/` relative to it.
- Environment variables: if you'd rather not keep `GROQ_API_KEY` in
  `config/.env`, inject it directly instead:

  ```json
  "automation-framework-advisor": {
    "command": "<...>",
    "args": ["<...>/mcp_server/server.py"],
    "cwd": "<...>",
    "env": { "GROQ_API_KEY": "your_key_here" },
    "disabled": false
  }
  ```

After saving `mcp.json`, Kiro connects automatically. If it was already
running, reconnect the server from the **MCP Server** view in the Kiro
feature panel, or use the command palette (`MCP: Reconnect`) — no need
to restart Kiro.

### 3b. Using these tools from a different test repo

The MCP server has no dependency on being opened as the active
workspace — it's a standalone process launched by Kiro according to
`mcp.json`, so **its tools are available in whatever repo you have open
in Kiro**, as long as the server is registered at **user scope**
(`~/.kiro/settings/mcp.json`) rather than workspace scope.

This is exactly the shape of your "two product repos" scenario:

```
test-folder-1/   ← Robot Framework test suite (source)
test-folder-2/   ← K6 target repo, open in Kiro right now
automation-framework-advisor/   ← this repo, provides the MCP tools
```

Setup:

1. Clone/unzip this project once, anywhere on the machine, and run the
   one-shot setup with `--user` so it registers at user scope right
   away:

   ```powershell
   python scripts\setup_mcp.py --user
   ```

2. Open `test-folder-2` (the K6 target repo) in Kiro. The
   `automation-framework-advisor` server shows up in the MCP Server
   view even though its own folder isn't open — because it's a
   separate process, not something scoped to the active workspace.
3. In chat, point Kiro at the source files directly, e.g.:

   > Read all `.robot` files under `../test-folder-1/tests/` and use the
   > `convert_test_project` tool to convert them from Robot Framework to K6.
   > Write the converted files into `./tests/` here.

   Kiro reads the Robot Framework source using its normal file tools
   (from wherever `test-folder-1` lives — a relative path, a second
   workspace folder, or an absolute path you give it), passes the file
   contents to `convert_test_project` (which runs inside
   `automation-framework-advisor`'s process), and writes the K6 output
   back into `test-folder-2` using its normal file-write tools. The two
   product repos never need to know the advisor tool exists on disk —
   only `mcp.json` does.

A few things worth knowing about this pattern:

- **Multi-root workspace alternative**: if you'll do this repeatedly,
  it's often easier to open `test-folder-1` and `test-folder-2` as
  additional folders in the same Kiro workspace (rather than referencing
  one by relative path from the other). Kiro can then read/write both
  directly. The MCP tool registration is unaffected either way.
- **Workspace-scope `mcp.json` files still merge**: per the config
  precedence rules, user config < workspace1 < workspace2. If
  `test-folder-2` also happens to define a server under the same name in
  its own workspace-level `mcp.json`, that workspace entry wins over your
  user-level one. Use `--name` with the setup script to register under a
  distinct key if you want to avoid any ambiguity.
- **No GROQ_API_KEY needed for this either**: `convert_test_project`
  works the same way here as anywhere else — if no key is configured,
  it hands the conversion rules + source back to Kiro and Kiro writes
  the K6 output itself.
- **No file-system access needed by the server itself**: tools like
  `convert_test_project` take file contents as arguments and return
  converted content as output — they don't reach into `test-folder-1` or
  `test-folder-2` on disk. Reading the source and writing the result is
  entirely Kiro's job (via its own file tools), which is why this works
  regardless of where the two product repos happen to live.

## 4. Confirm it's working

Open the MCP Server view in Kiro and check
`automation-framework-advisor` shows as connected with 20 tools. Then,
in chat, try something like:

> List the automation frameworks you know about.

or

> Recommend a framework for API testing on a Python microservices backend.

Kiro should route these to the `list_frameworks` / `recommend_frameworks`
tools automatically.

## 5. Tool reference

No tool in this list requires `GROQ_API_KEY`. The three marked
**manual-mode capable** work either way: with a key configured they
call Groq server-side and return finished code directly; without one
(the default) they return a `mode: "manual"` brief for Kiro to act on
instead.

| Tool | Purpose | Behavior without GROQ_API_KEY |
|---|---|---|
| `list_frameworks` | List every framework in the KB | n/a — doesn't use an LLM either way |
| `list_frameworks_by_category` | Group frameworks by category (API, UI, mobile, IaC, …) | n/a |
| `search_knowledge_graph` | Free-text search over the KB/graph | n/a |
| `get_framework_details` | Full profile of one framework | n/a |
| `compare_frameworks` | Side-by-side comparison of 2+ frameworks | n/a |
| `recommend_frameworks` | Ranked recommendation for a described use case | n/a |
| `score_frameworks` | Weighted scorecard, optional custom weights | n/a |
| `find_migration_paths` | Capability gaps/overlap moving between frameworks | n/a |
| `generate_migration_roadmap` | Phased migration plan with effort estimates | n/a |
| `analyze_coverage_gaps` | Coverage parity % + gaps for a target framework | n/a |
| `analyze_test_case_coverage` | Map test cases to best-fit framework(s) | n/a |
| `analyze_prerequisites` | Automate manual setup steps + CI/CD stages | n/a |
| `generate_boilerplate_project` | Ready-to-run project scaffold | n/a |
| `convert_test_code` | Convert one test file between frameworks | **Manual-mode capable** — returns conversion rules + source for Kiro to convert |
| `convert_test_project` | Convert a multi-file test project | **Manual-mode capable** — same, plus deterministic conftest/requirements |
| `generate_test_code` | Generate automated tests from manual test cases | **Manual-mode capable** — returns generation rules + test cases for Kiro to write |
| `compare_repos` | Cross-repo test parity report (assertion-count diff) | n/a |
| `compare_repos_csv` | Same as above, returned as CSV | n/a |
| `discover_new_frameworks` | Scan GitHub for new frameworks not yet in KB | Works fully — profiles are just less rich without an LLM |
| `add_framework_to_kb` | Research + add a named framework to the KB | Works fully — falls back to a template-based profile; review the returned `completeness` field |

## 6. Troubleshooting

- **Server shows as disconnected in Kiro**: check `logs/mcp_server.log`
  for a startup traceback (usually a missing dependency or a bad `cwd`).
- **`setup_mcp.py` fails on venv creation**: your Python install may be
  missing the `venv` module (uncommon, but happens on some minimal
  Linux distros) — install it, or pass `--no-install --python
  <existing-interpreter>` to skip venv creation entirely.
- **`setup_mcp.py` fails on `pip install`**: re-run the script after
  fixing the underlying pip error shown in the output — it's just a
  normal `pip install -r requirements.txt` under the hood.
- **A `convert_test_code` / `convert_test_project` / `generate_test_code`
  response looks like `{"mode": "manual", ...}` instead of finished
  code**: that's expected without `GROQ_API_KEY` — it means Kiro should
  read the `instructions` field and produce the code itself using the
  attached rules, not that the tool call failed.
- **Changes to `mcp_server/*.py` aren't picked up**: reconnect the server
  from the MCP Server view — it's a long-running process, so edits
  require a restart.
- **"Framework not found" errors**: framework name lookups are
  case-insensitive but not fuzzy for every tool — use the exact name from
  `list_frameworks` (e.g. `"Playwright"`, `"Robot Framework"`, `"K6"`).
