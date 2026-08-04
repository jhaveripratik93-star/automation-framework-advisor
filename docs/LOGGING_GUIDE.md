# Logging Guide - Tool-Based Approach Flow Tracking

This document explains the comprehensive logging system added to track which execution flow is running at any given time.

## Log Levels

- **INFO**: Flow decisions, major state transitions, tool execution
- **DEBUG**: Detailed data (message lengths, arguments, intermediate results)
- **WARNING**: Fallback scenarios, missing data, degraded functionality
- **ERROR**: Failures, exceptions, invalid states

## Execution Flows & Log Markers

### 1. Entry Point (`streamlit_app.py::process_message`)

**Log Pattern:**
```
================================================================================
PROCESS_MESSAGE: New request received
  User input length: XXX chars
  Uploaded docs context: XXX chars
  Case study context: XXX chars
  Gathering mode: True/False
  Profile exists: True/False
================================================================================
```

**What to look for:**
- Confirms request received
- Shows context availability (uploaded docs, case studies)
- Shows current state (gathering mode, profile status)

---

### 2. Flow Branching (`streamlit_app.py::process_message`)

Each flow branch logs its decision:

#### 2.1 Discovery Questionnaire Mode
```
FLOW: Discovery questionnaire mode (gathering answers)
```
or
```
FLOW: All discovery questions answered, running evaluation
```
or
```
FLOW: Asking next discovery question (X/Y)
```

#### 2.2 Auto-Evaluation
```
FLOW: Auto-evaluation or explicit evaluation requested
FLOW: Building profile from auto-detected context
```
or
```
FLOW: Starting discovery flow (explicit evaluation request)
```

#### 2.3 Knowledge Graph Request
```
FLOW: Knowledge graph visualization requested (currently disabled)
```

#### 2.4 Migration Plan Request
```
FLOW: Migration plan requested
FLOW: Generating migration plan for framework: XXX
```
or
```
FLOW: Migration plan requested but no evaluation exists yet
```

#### 2.5 Boilerplate Request
```
FLOW: Boilerplate generation requested
FLOW: Generating boilerplate for framework: XXX
```
or
```
FLOW: Boilerplate requested but no evaluation exists yet
```

#### 2.6 Re-evaluation Request
```
FLOW: Re-evaluation requested
```
or
```
FLOW: Re-evaluation requested but no profile exists
```

#### 2.7 Default LLM-Powered Flow (Tool-Based Approach)
```
FLOW: Default flow - using AdvisorLLM (tool-based/LLM-powered)
  AdvisorLLM will receive:
    - User message: XXX chars
    - Uploaded docs: XXX chars
    - Case study: XXX chars
```

---

### 3. LLM-Powered Response Generation (`src/llm/advisor_llm.py::respond`)

#### 3.1 Entry
```
================================================================================
ADVISOR_LLM.respond: Starting LLM-powered response generation
  Message length: XXX chars | History: X messages
  Uploaded docs context: XXX chars
  Case study context: XXX chars
================================================================================
```

#### 3.2 Tool Executor Initialization
```
TOOL_EXECUTOR: Initializing with knowledge graph and document contexts
TOOL_EXECUTOR: X tools available for LLM
```
or
```
TOOL_EXECUTOR: Knowledge graph not available, tools disabled
```

#### 3.3 Ollama Availability Check
```
================================================================================
FALLBACK: Ollama unavailable — using rule-based AdvisorChat
================================================================================
```

#### 3.4 GraphRAG Context Retrieval
```
GRAPHRAG: Retrieved XXX chars of context from knowledge graph
```

---

### 4. Agentic Loop (`src/llm/advisor_llm.py::respond`)

#### 4.1 Loop Start
```
AGENTIC_LOOP: Starting (max 5 iterations)
```

#### 4.2 Each Iteration
```
--------------------------------------------------------------------------------
AGENTIC_LOOP: Iteration X/5
--------------------------------------------------------------------------------
```

With optional:
```
AGENTIC_LOOP: Including X previous tool results in prompt
```

#### 4.3 LLM API Call
```
LLM_CALL: Sending request to Ollama (tools=enabled/disabled)
```

---

### 5. Tool Call Execution

#### 5.1 Tool Request Detected
```
================================================================================
TOOL_CALL: LLM requested X tool(s) - ENTERING TOOL EXECUTION
================================================================================
```

#### 5.2 Individual Tool Execution
```
TOOL_CALL [1/X]: Executing 'tool_name'
```

Then in `tools.py`:
```
================================================================================
TOOL_EXECUTOR.execute: Tool='tool_name'
  Arguments: {...}
================================================================================
TOOL_EXECUTOR: Calling handler for 'tool_name'...
```

#### 5.3 Tool-Specific Execution Logs

**search_knowledge_graph:**
```
TOOL: search_knowledge_graph (query='...', max_hops=2)
TOOL: search_knowledge_graph - Found XXX chars of context
```
or
```
TOOL: search_knowledge_graph - No results found
```

**get_framework_details:**
```
TOOL: get_framework_details (framework='XXX')
TOOL: get_framework_details - Retrieved details for 'XXX'
```
or
```
TOOL: get_framework_details - Framework 'XXX' not found
```

**analyze_uploaded_content:**
```
TOOL: analyze_uploaded_content (search='XXX', doc_type='all')
TOOL: analyze_uploaded_content - Found X matches in test files
TOOL: analyze_uploaded_content - Found X matches in case study
```
or
```
TOOL: analyze_uploaded_content - No matches found
```

**run_framework_comparison:**
```
TOOL: run_framework_comparison (frameworks=['X', 'Y'], criteria=None)
TOOL: run_framework_comparison - Processing 'X'
TOOL: run_framework_comparison - Completed comparison of X frameworks
```

**find_migration_paths:**
```
TOOL: find_migration_paths (from='X', to=None)
TOOL: find_migration_paths - Found X migration path(s)
```
or
```
TOOL: find_migration_paths - Source framework 'X' not found in graph
```

#### 5.4 Tool Result
```
TOOL_EXECUTOR: SUCCESS - 'tool_name' returned XXX chars
```
or
```
================================================================================
TOOL_EXECUTOR: FAILED - 'tool_name' raised exception
  Error: ExceptionType: error message
================================================================================
```

#### 5.5 After All Tools Execute
```
TOOL_RESULT [1/X]: 'tool_name' returned XXX chars
================================================================================
TOOL_CALL: All tools executed, feeding results back to LLM
================================================================================
```

---

### 6. Final Response

#### 6.1 Normal Completion
```
================================================================================
AGENTIC_LOOP: FINAL RESPONSE received (length: XXX chars, iterations: X)
================================================================================
```

#### 6.2 Max Iterations Reached
```
================================================================================
AGENTIC_LOOP: MAX ITERATIONS REACHED (5)
  LLM continued requesting tools beyond limit
================================================================================
```

#### 6.3 Exception Fallback
```
================================================================================
FALLBACK: Exception in AdvisorLLM, falling back to AdvisorChat
  Error: ExceptionType: error message
================================================================================
```

---

## Example Full Flow Trace

Here's what a complete tool-based interaction looks like in the logs:

```
================================================================================
PROCESS_MESSAGE: New request received
  User input length: 52 chars
  Uploaded docs context: 1200 chars
  Case study context: 3500 chars
  Gathering mode: False
  Profile exists: True
================================================================================

FLOW: Default flow - using AdvisorLLM (tool-based/LLM-powered)
  AdvisorLLM will receive:
    - User message: 52 chars
    - Uploaded docs: 1200 chars
    - Case study: 3500 chars

================================================================================
ADVISOR_LLM.respond: Starting LLM-powered response generation
  Message length: 52 chars | History: 4 messages
  Uploaded docs context: 1200 chars
  Case study context: 3500 chars
================================================================================

TOOL_EXECUTOR: Initializing with knowledge graph and document contexts
TOOL_EXECUTOR: 5 tools available for LLM

GRAPHRAG: Retrieved 850 chars of context from knowledge graph

AGENTIC_LOOP: Starting (max 5 iterations)

--------------------------------------------------------------------------------
AGENTIC_LOOP: Iteration 1/5
--------------------------------------------------------------------------------

LLM_CALL: Sending request to Ollama (tools=enabled)

================================================================================
TOOL_CALL: LLM requested 2 tool(s) - ENTERING TOOL EXECUTION
================================================================================

TOOL_CALL [1/2]: Executing 'search_knowledge_graph'

================================================================================
TOOL_EXECUTOR.execute: Tool='search_knowledge_graph'
  Arguments: {'query': 'Python testing frameworks', 'max_hops': 2}
================================================================================
TOOL_EXECUTOR: Calling handler for 'search_knowledge_graph'...
TOOL: search_knowledge_graph (query='Python testing frameworks', max_hops=2)
TOOL: search_knowledge_graph - Found 650 chars of context
TOOL_EXECUTOR: SUCCESS - 'search_knowledge_graph' returned 650 chars

TOOL_RESULT [1/2]: 'search_knowledge_graph' returned 650 chars

TOOL_CALL [2/2]: Executing 'get_framework_details'

================================================================================
TOOL_EXECUTOR.execute: Tool='get_framework_details'
  Arguments: {'framework_name': 'Playwright'}
================================================================================
TOOL_EXECUTOR: Calling handler for 'get_framework_details'...
TOOL: get_framework_details (framework='Playwright')
TOOL: get_framework_details - Retrieved details for 'Playwright'
TOOL_EXECUTOR: SUCCESS - 'get_framework_details' returned 450 chars

TOOL_RESULT [2/2]: 'get_framework_details' returned 450 chars

================================================================================
TOOL_CALL: All tools executed, feeding results back to LLM
================================================================================

--------------------------------------------------------------------------------
AGENTIC_LOOP: Iteration 2/5
--------------------------------------------------------------------------------

AGENTIC_LOOP: Including 2 previous tool results in prompt

LLM_CALL: Sending request to Ollama (tools=enabled)

================================================================================
AGENTIC_LOOP: FINAL RESPONSE received (length: 1250 chars, iterations: 2)
================================================================================
```

---

## Log File Location

Logs are written to: `logs/advisor.log`

## Viewing Logs in Real-Time

**Windows:**
```cmd
type logs\advisor.log
```

**Watch mode (PowerShell):**
```powershell
Get-Content logs\advisor.log -Wait -Tail 50
```

## Log Analysis Tips

1. **Search for flow markers** to understand the path taken:
   ```
   findstr /C:"FLOW:" logs\advisor.log
   ```

2. **Track tool usage**:
   ```
   findstr /C:"TOOL_CALL" logs\advisor.log
   ```

3. **Find errors/fallbacks**:
   ```
   findstr /C:"FALLBACK" /C:"ERROR" logs\advisor.log
   ```

4. **Count tool executions**:
   ```
   findstr /C:"TOOL_EXECUTOR.execute: Tool=" logs\advisor.log
   ```

5. **Monitor iteration counts**:
   ```
   findstr /C:"AGENTIC_LOOP: Iteration" logs\advisor.log
   ```

---

## Configuration

To adjust log verbosity, edit `src/logging_config.py`:

- Set `DEBUG` for maximum detail (includes all arguments, intermediate values)
- Set `INFO` for flow tracking (default, recommended)
- Set `WARNING` for errors and fallbacks only

---

## Troubleshooting

**Issue: Not seeing logs**
- Check `logs/advisor.log` exists
- Verify logging is configured (check `src/logging_config.py`)
- Ensure log level is INFO or DEBUG

**Issue: Too many logs**
- Set log level to WARNING in `src/logging_config.py`
- Or filter output: `findstr /V "DEBUG" logs\advisor.log`

**Issue: Need to understand why fallback was triggered**
- Search for `FALLBACK:` in logs
- Check the line immediately after for error details

**Issue: Tool not being called**
- Check if `TOOL_EXECUTOR:` initialization succeeded
- Verify Ollama model supports function calling (llama3.1+, mistral-nemo)
- Look for `LLM_CALL: ... (tools=disabled)` which means tools weren't sent to LLM
