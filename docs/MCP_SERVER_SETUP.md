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

- Python 3.11+ (the project's existing interpreter is fine).
- Dependencies installed from `requirements.txt` (includes the `mcp`
  package used to run the server):

  ```powershell
  pip install -r requirements.txt
  ```

- A Groq API key in `config/.env` — required for the LLM-backed tools
  (`convert_test_code`, `convert_test_project`, `generate_test_code`,
  `discover_new_frameworks`'s LLM profiling step, `add_framework_to_kb`).
  Tools that only read the YAML knowledge base (`list_frameworks`,
  `get_framework_details`, `compare_frameworks`, `score_frameworks`,
  `find_migration_paths`, `generate_migration_roadmap`,
  `analyze_coverage_gaps`, `analyze_test_case_coverage`,
  `generate_boilerplate_project`, `compare_repos`) work without it.

  ```env
  # config/.env
  GROQ_API_KEY=your_key_here
  GROQ_MODEL=openai/gpt-oss-120b
  ```

## 2. Verify the server runs standalone

Before wiring it into Kiro, confirm the server starts cleanly:

```powershell
python mcp_server\server.py
```

It should sit idle waiting for stdio input (no output on the console —
all logging goes to `logs\mcp_server.log`). Press `Ctrl+C` to stop it.
If it exits immediately with a traceback, read `logs\mcp_server.log` for
the error (missing dependency, bad `.env`, etc.).

## 3. Configure it in Kiro

Kiro discovers MCP servers from `mcp.json` files. You can register the
server at either scope:

- **Workspace scope** (this project only): `.kiro/settings/mcp.json`
  in the repo root — committed with the project so teammates get it
  automatically when they open the workspace.
- **User scope** (all your workspaces): `~/.kiro/settings/mcp.json`.

Create the file (or add to it, if `mcpServers` already has entries) with
an entry like this:

```json
{
  "mcpServers": {
    "automation-framework-advisor": {
      "command": "C:\\Program Files\\Python314\\python.exe",
      "args": [
        "mcp_server/server.py"
      ],
      "cwd": "C:\\Users\\xjhapra\\tcs-poc-automation-framework\\automation-framework-advisor",
      "disabled": false
    }
  }
}
```

Notes:

- `command` must be an **absolute path** to the Python interpreter that
  has the project's dependencies installed (find yours with
  `(Get-Command python).Source` in PowerShell, or point it at a venv's
  `python.exe` if you use one, e.g.
  `...\automation-framework-advisor\.venv\Scripts\python.exe`).
- `cwd` must be the **project root** (`automation-framework-advisor`),
  not `mcp_server/` — the server resolves `data/frameworks`,
  `config/.env`, and `logs/` relative to it. If you omit `cwd` and the
  server isn't started from the project root, it still works because
  `mcp_server/services.py` inserts the project root onto `sys.path` and
  loads `config/.env` by absolute path — but setting `cwd` explicitly is
  the safest option across MCP clients.
- `args` can also be a relative path (`"mcp_server/server.py"`) since
  it's resolved against `cwd`.
- Environment variables: if you'd rather not keep `GROQ_API_KEY` in
  `config/.env`, you can inject it directly in the MCP config instead:

  ```json
  "automation-framework-advisor": {
    "command": "C:\\Program Files\\Python314\\python.exe",
    "args": ["mcp_server/server.py"],
    "cwd": "C:\\...\\automation-framework-advisor",
    "env": { "GROQ_API_KEY": "your_key_here" },
    "disabled": false
  }
  ```

After saving `mcp.json`, Kiro connects automatically. If it was already
running, reconnect the server from the **MCP Server** view in the Kiro
feature panel, or use the command palette (`MCP: Reconnect`) — no need
to restart Kiro.

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

| Tool | Purpose | Needs GROQ_API_KEY |
|---|---|---|
| `list_frameworks` | List every framework in the KB | No |
| `list_frameworks_by_category` | Group frameworks by category (API, UI, mobile, IaC, …) | No |
| `search_knowledge_graph` | Free-text search over the KB/graph | No |
| `get_framework_details` | Full profile of one framework | No |
| `compare_frameworks` | Side-by-side comparison of 2+ frameworks | No |
| `recommend_frameworks` | Ranked recommendation for a described use case | No |
| `score_frameworks` | Weighted scorecard, optional custom weights | No |
| `find_migration_paths` | Capability gaps/overlap moving between frameworks | No |
| `generate_migration_roadmap` | Phased migration plan with effort estimates | No |
| `analyze_coverage_gaps` | Coverage parity % + gaps for a target framework | No |
| `analyze_test_case_coverage` | Map test cases to best-fit framework(s) | No |
| `analyze_prerequisites` | Automate manual setup steps + CI/CD stages | No |
| `generate_boilerplate_project` | Ready-to-run project scaffold | No |
| `convert_test_code` | Convert one test file between frameworks | **Yes** |
| `convert_test_project` | Convert a multi-file test project | **Yes** |
| `generate_test_code` | Generate automated tests from manual test cases | **Yes** |
| `compare_repos` | Cross-repo test parity report (assertion-count diff) | No |
| `compare_repos_csv` | Same as above, returned as CSV | No |
| `discover_new_frameworks` | Scan GitHub for new frameworks not yet in KB | No* |
| `add_framework_to_kb` | Research + add a named framework to the KB | **Yes** |

\* `discover_new_frameworks` works without an API key but produces richer
profiles when one is set.

## 6. Troubleshooting

- **Server shows as disconnected in Kiro**: check `logs/mcp_server.log`
  for a startup traceback (usually a missing dependency or a bad `cwd`).
- **LLM tools fail with "GROQ_API_KEY is not set"**: set it in
  `config/.env` or in the MCP config's `env` block, then reconnect.
- **Changes to `mcp_server/*.py` aren't picked up**: reconnect the server
  from the MCP Server view — it's a long-running process, so edits
  require a restart.
- **"Framework not found" errors**: framework name lookups are
  case-insensitive but not fuzzy for every tool — use the exact name from
  `list_frameworks` (e.g. `"Playwright"`, `"Robot Framework"`, `"K6"`).
