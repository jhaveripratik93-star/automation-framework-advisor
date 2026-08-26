# Logging Guide — Agent Pipeline Flow Tracking

This document explains the logging system used to track which execution flow is running at any given time.

## Log Levels

- **INFO**: Flow decisions, major state transitions, tool execution
- **DEBUG**: Detailed data (message lengths, arguments, intermediate results)
- **WARNING**: Fallback scenarios, missing data, degraded functionality
- **ERROR**: Failures, exceptions, invalid states

---

## Execution Flows & Log Markers

### 1. Entry Point (`streamlit_app.py`)

```
PROCESS_MESSAGE: New request received
  User input length: XXX chars
  Uploaded docs context: XXX chars
  Case study context: XXX chars
  Gathering mode: True/False
  Profile exists: True/False
```

---

### 2. Agent Orchestrator (`src/agents/orchestrator.py`)

```
AgentOrchestrator.run: START query='...'
AgentOrchestrator.run: DONE response_len=XXX
```

For streaming:
```
AgentOrchestrator.run_stream: START query='...'
AgentOrchestrator.run_stream: DONE response_len=XXX
```

---

### 3. LangGraph Pipeline Nodes (`src/agents/langgraph_pipeline.py`)

Each node logs on entry and exit:

```
LangGraph[decide]: action=tool_call
LangGraph[select_tools]: 2 tool(s) selected
LangGraph[execute_tools]: tool='search_knowledge_graph' result_len=650
LangGraph[synthesise]: needs_more=False verdict='Results are sufficient...'
LangGraph[evaluate]: response_len=1250
LangGraph[reflect]: approved=True
LangGraph[format]: fmt_type=comparison
```

Conditional routing:
```
# After synthesise — needs_more=True → back to select_tools
# After reflect — critique present → back to evaluate
```

---

### 4. Tool Executor (`src/tools/executor.py`)

```
ToolExecutor: 'search_knowledge_graph' → 650 chars
ToolExecutor: 'get_framework_details' → 450 chars
ToolExecutor: 'run_framework_comparison' → 1200 chars
ToolExecutor: 'recommend_frameworks' → 900 chars
ToolExecutor: 'find_migration_paths' → 800 chars
ToolExecutor: 'analyze_test_case_coverage' → 600 chars
ToolExecutor: 'list_frameworks_by_category' → 400 chars
```

On failure:
```
ToolExecutor: 'tool_name' failed — ExceptionType: error message
```

---

### 5. Groq LLM Client (`src/llm/groq_client.py`)

```
GroqClient initialized: model=llama-3.3-70b-versatile
Groq request: model=llama-3.3-70b-versatile messages=3
Groq response: model=llama-3.3-70b-versatile response_len=1250
```

On HTTP error:
```
GroqClient.chat: HTTP 429 — rate limit exceeded
GroqClient.chat: HTTP 401 — invalid API key
```

---

### 6. Retry Logic (`src/agents/retry.py`)

Permanent errors (no retry):
```
# 400, 401, 403, model_decommissioned, auth errors → fail immediately
```

Transient errors (with backoff):
```
# 429, 500, 503 → exponential backoff retry
```

---

### 7. CodeGen Pipeline (`src/codegen/pipeline.py`)

```
CodeGenPipeline[plan]: analysing test case 'Login with valid credentials'
CodeGenPipeline[resolve]: resolving selectors
CodeGenPipeline[generate]: generating code (attempt 1)
CodeGenPipeline[validate]: reviewing generated code
CodeGenPipeline[assemble]: assembling final file
CodeGenPipeline: DONE — assembled_code=1450 chars, valid=True
```

On retry:
```
CodeGenPipeline[generate]: generating code (attempt 2)
```

CodeGenOrchestrator:
```
CodeGenOrchestrator (agents): 3 files in 4200ms
CodeGenOrchestrator: agent pipeline failed (ExceptionType) — falling back to legacy
```

---

## Example Full Flow Trace

```
AgentOrchestrator.run: START query='Compare Playwright vs Cypress'

LangGraph[decide]: action=tool_call
LangGraph[select_tools]: 1 tool(s) selected
LangGraph[execute_tools]: tool='run_framework_comparison' result_len=1200
LangGraph[synthesise]: needs_more=False verdict='Sufficient comparison data'
LangGraph[evaluate]: response_len=1450
LangGraph[reflect]: approved=True
LangGraph[format]: fmt_type=comparison

AgentOrchestrator.run: DONE response_len=1450
```

---

## Log File Location

Logs are written to: `logs/advisor.log`

## Viewing Logs

**Windows (PowerShell — watch mode):**
```powershell
Get-Content logs\advisor.log -Wait -Tail 50
```

**Windows (cmd — search):**
```cmd
findstr /C:"LangGraph" logs\advisor.log
findstr /C:"ToolExecutor" logs\advisor.log
findstr /C:"WARNING" logs\advisor.log
findstr /C:"ERROR" logs\advisor.log
```

---

## Log Analysis Tips

1. **Track pipeline flow:**
   ```
   findstr /C:"LangGraph[" logs\advisor.log
   ```

2. **Track tool usage:**
   ```
   findstr /C:"ToolExecutor:" logs\advisor.log
   ```

3. **Find errors and fallbacks:**
   ```
   findstr /C:"WARNING" /C:"ERROR" /C:"failed" logs\advisor.log
   ```

4. **Monitor CodeGen pipeline:**
   ```
   findstr /C:"CodeGenPipeline" logs\advisor.log
   ```

5. **Check Groq API calls:**
   ```
   findstr /C:"GroqClient" logs\advisor.log
   ```

---

## Configuration

To adjust log verbosity, edit `src/logging_config.py`:

- `DEBUG` — maximum detail (all arguments, intermediate values)
- `INFO` — flow tracking (default, recommended)
- `WARNING` — errors and fallbacks only

---

## Troubleshooting

**Issue: Not seeing logs**
- Check `logs/advisor.log` exists
- Verify `src/logging_config.py` is imported at startup
- Ensure log level is INFO or DEBUG

**Issue: Too many logs**
- Set log level to WARNING in `src/logging_config.py`

**Issue: Groq API errors**
- Search for `GroqClient.chat: HTTP` in logs
- 401 → check `config/.env` has correct `GROQ_API_KEY`
- 429 → rate limit; wait or upgrade plan
- 400 with `model_decommissioned` → update `_DEFAULT_MODEL` in `groq_client.py`

**Issue: Agent pipeline falling back to legacy**
- Search for `agent pipeline failed` in logs
- Check the exception type and message on the next line
- Common cause: LLM returning unexpected format in ScenarioPlanner
