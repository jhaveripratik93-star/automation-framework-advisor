# High-Level Design (HLD)
# Automation Framework Migration & Coverage Advisor

**Version:** 3.0
**Date:** July 2025
**Status:** Active

---

## 1. Executive Summary

An AI-powered advisor that helps engineering teams select, evaluate, and migrate to the right test automation framework. The system combines a **weighted scoring matrix** with a **GraphRAG knowledge engine** and a **7-agent LangGraph pipeline** to produce grounded, context-aware recommendations.

Key capabilities:
1. **Weighted Decision Matrix** — scores 17 frameworks across 7–10 configurable criteria
2. **GraphRAG Context Retrieval** — 2-hop knowledge graph subgraph grounding every LLM response
3. **7-Agent LangGraph Pipeline** — Decide → SelectTools → ExecuteTools → Synthesise → Evaluate → Reflect → Format
4. **LangGraph CodeGen Pipeline** — 5-agent pipeline: Plan → Resolve → Generate → Validate → Assemble
5. **Persistent Knowledge Graph** — self-growing graph seeded from 17 YAML profiles, enriched by user interactions
6. **Case Study Ingestion** — file upload or URL fetch with browser-like headers; persisted to `data/frameworks/case_studies/`
7. **Criteria Sidebar** — real-time weight sliders, preset selector, custom criteria with proportional redistribution

---

## 2. System Context Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER / QA LEAD                           │
│  (chat queries · uploaded test files · case study docs/URLs)    │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                   STREAMLIT FRONTEND (UI)                       │
│                                                                 │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │ Chat Panel   │  │ Criteria Sidebar  │  │ Case Study       │  │
│  │ (history)    │  │ weight sliders   │  │ File / URL tabs  │  │
│  │              │  │ preset selector  │  │                  │  │
│  └──────────────┘  └──────────────────┘  └──────────────────┘  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    AGENT ORCHESTRATOR                           │
│              (src/agents/orchestrator.py)                       │
│                                                                 │
│  decide → select_tools → execute_tools → synthesise             │
│                ↑ (needs_more, max 2 rounds)                     │
│  → evaluate → reflect → format                                  │
└────────────────────────────┬────────────────────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
   │ Groq LLM     │  │ GraphRAG     │  │ Scoring      │
   │ (cloud)      │  │ Engine       │  │ Engine       │
   │ llama-3.3-   │  │ (2-hop       │  │ (weighted    │
   │ 70b-versatile│  │  subgraph)   │  │  matrix)     │
   └──────────────┘  └──────┬───────┘  └──────────────┘
                            │
                   ┌────────▼────────┐
                   │ Knowledge Graph │
                   │ (entities +     │
                   │  relationships) │
                   └────────┬────────┘
                            │
                   ┌────────▼────────┐
                   │ Knowledge Base  │
                   │ 17 YAML profiles│
                   └─────────────────┘
```

---

## 3. Architecture Components

### 3.1 Streamlit Frontend (`streamlit_app.py`)

| Aspect | Detail |
|--------|--------|
| **Layout** | Left sidebar (inputs) · Main chat panel |
| **Sidebar** | File upload, case study (File/URL tabs), weight preset selector, per-criterion sliders, custom criteria |
| **Chat** | Full message history, welcome message, discovery questionnaire flow |
| **Case Study** | Two-tab expander: 📄 File upload or 🔗 URL fetch; multiple URLs with dedup; persisted to `data/frameworks/case_studies/` |
| **Frameworks** | Available Frameworks grouped by category in sidebar |
| **Off-topic filter** | `_TOPIC_KEYWORDS` guards against non-automation queries |

### 3.2 Agent Orchestrator (`src/agents/orchestrator.py`)

Central coordinator of the 7-agent LangGraph pipeline. Maintains `ConversationMemory` across invocations and emits streaming callbacks.

**Pipeline steps:**
```
user query
  → GraphRAG.retrieve_context()          # pre-fetch graph context
  → build_pipeline()                     # compile LangGraph StateGraph
  → decide                               # tool_call | direct | rejected | clarify
  → select_tools                         # picks tool(s) + builds args
  → execute_tools                        # runs tools, collects results
  → synthesise                           # validates results, flags needs_more
  → evaluate                             # LLM synthesises final answer
  → reflect                              # critique draft, approve or revise
  → format                               # markdown / table structure
  → ConversationMemory.add_turn()        # persist to memory
```

### 3.3 LangGraph Pipeline (`src/agents/langgraph_pipeline.py`)

`PipelineState` TypedDict with all keys declared (undeclared keys are silently dropped by LangGraph):

| Key | Type | Purpose |
|-----|------|---------|
| `user_message` | str | Original query |
| `graph_context` | str | GraphRAG pre-fetched context |
| `profile_context` | str | Weight/profile summary |
| `uploaded_docs` | str | Uploaded test file content |
| `case_study` | str | Case study content |
| `action` | str | `tool_call` / `direct` / `rejected` / `clarify` |
| `tool_results` | list | Accumulated tool outputs |
| `synthesis_verdict` | str | SynthesisAgent feedback |
| `needs_more` | bool | Whether more tool calls needed |
| `round_num` | int | Current tool-call round (max 2) |
| `reflection_critique` | str | ReflectionAgent critique |
| `reflection_count` | int | Number of reflection passes |
| `final_response` | str | Final formatted response |
| `conversation_history` | list | Last 10 turns from memory |
| `_pending_tool_calls` | list | Tool calls queued by SelectTools |
| `_last_round_results` | list | Results from last execute round |

### 3.4 Agents (`src/agents/`)

| Agent | File | Role |
|-------|------|------|
| `DecisionAgent` | `decision_agent.py` | Routes: `tool_call` / `direct` / `rejected` / `clarify` |
| `ToolSelectionAgent` | `tool_selection_agent.py` | Maps intent to tool + builds typed args |
| `SynthesisAgent` | `synthesis_agent.py` | Validates tool results, flags `needs_more` |
| `EvaluationAgent` | `evaluation_agent.py` | LLM synthesis with `uploaded_docs` + `case_study` injected |
| `ReflectionAgent` | `reflection_agent.py` | Critiques draft, triggers re-evaluate if needed |
| `FormatAgent` | `format_agent.py` | Applies markdown structure by query type |

### 3.5 Tool Executor (`src/tools/executor.py`)

11 tools registered:

| Tool | Description |
|------|-------------|
| `search_knowledge_graph` | Token-scored KB search, top 10 results |
| `get_framework_details` | Full YAML profile for a named framework |
| `run_framework_comparison` | Side-by-side capability comparison |
| `recommend_frameworks` | ScoringEngine-ranked recommendations with weight priority header |
| `find_migration_paths` | MIGRATES_TO relationships with gap analysis |
| `analyze_test_case_coverage` | Coverage matrix via `coverage_engine` |
| `analyze_uploaded_content` | Keyword search in uploaded files / case study |
| `analyze_prerequisites` | Prerequisite automation scripts + CI/CD YAML |
| `score_frameworks` | Weighted scorecard with active WeightProfile |
| `convert_test_cases` | Single-file LLM-based framework conversion |
| `list_frameworks_by_category` | Frameworks grouped by `architecture_fit` classification |

### 3.6 Scoring Engine (`src/scoring/`)

Standalone weighted matrix evaluation. Completely independent of the agent pipeline.

| Component | File | Purpose |
|-----------|------|---------|
| `ScoringEngine` | `engine.py` | Orchestrates evaluation, ranking, pros/cons |
| `WeightProfile` | `weights.py` | 8 presets + dynamic adjustment per profile |
| Criterion scorers | `criteria.py` | 10 individual scoring functions (C1–C10) |
| Penalty/bonus logic | `penalties.py` | Hard penalties for missing requirements |
| Output models | `models.py` | `DecisionMatrix`, `FrameworkScore`, `CriteriaScores` |

**Evaluation criteria:**

| ID | Criterion | Default Weight |
|----|-----------|---------------|
| C1 | Language Compatibility | 20% |
| C2 | API Validation | 20% |
| C3 | Performance & Load | 15% |
| C4 | CI/CD Integration | 20% |
| C5 | Maintainability | 15% |
| C6 | Cloud Readiness | 5% |
| C7 | License & Cost | 5% |
| C8 | Cloud Provider Support | cloud only |
| C9 | IaC Capabilities | cloud only |
| C10 | Cloud Migration Readiness | cloud only |

### 3.7 GraphRAG Engine (`src/graph/graphrag_engine.py`)

Retrieves structured knowledge graph context to ground every LLM call.

**Algorithm:**
1. Tokenise query (min 5 chars per token to skip stop-words)
2. Exact-match tokens against entity name index
3. Fuzzy-match remaining tokens (threshold 0.75, cap 20 entities)
4. Retrieve 2-hop subgraph from matched entity IDs
5. Format as `[source] --relationship--> [target] (confidence)` triples
6. YAML fallback if no graph entities matched

### 3.8 Knowledge Graph (`src/graph/`)

Persistent entity-relationship graph seeded from 17 YAML framework profiles and enriched by user interactions.

| Component | File | Purpose |
|-----------|------|---------|
| `KnowledgeGraph` | `knowledge_graph.py` | In-memory graph with name index |
| `GraphStore` | `graph_store.py` | Atomic JSON persistence (`data/knowledge_graph.json`) |
| `EntityExtractor` | `entity_extractor.py` | Extracts entities/relationships from YAML and user messages |
| `GraphRAGEngine` | `graphrag_engine.py` | Context retrieval (see §3.7) |

Entity types: `framework`, `language`, `capability`, `limitation`, `ci_cd_tool`, `cloud_provider`, `migration_path`.

### 3.9 Knowledge Base (`src/knowledge_base/`)

17 YAML framework profiles loaded at startup. All 17 files have consistent top-level keys (23 keys audited as OK): `category`, `cloud_providers`, `cloud_migration_metrics`, `testing_capabilities`, `architecture_fit` (with `performance_testing`, `load_testing`, `cloud_infrastructure`, `infrastructure_as_code`, `multi_cloud`, `hybrid_cloud`), `capabilities`, `cicd_integration`.

Framework classification uses `classify_framework_data()` from `schema.py` which reads `architecture_fit` flags — not the raw `category` string.

**Frameworks:** Playwright, Cypress, Selenium WebDriver, WebdriverIO, Robot Framework, TestCafe, Puppeteer, Appium, Karate, K6, Locust, REST Assured, Terraform, Ansible, Chef, Pulumi, AWS CloudFormation.

### 3.10 Groq LLM Client (`src/llm/groq_client.py`)

Cloud LLM inference via Groq REST API.

| Feature | Detail |
|---------|--------|
| Default model | `llama-3.3-70b-versatile` (configurable via `GROQ_MODEL` env var) |
| API base | `https://api.groq.com/openai/v1` |
| Timeout | 60s |
| API key | `_get_api_key()` reads fresh on every call with `load_dotenv(override=True)` |
| Key location | `config/.env` → `GROQ_API_KEY` |
| Fallback | If Groq unavailable, advisor falls back to rule-based `AdvisorChat` |
| Permanent error detection | `retry.py` fails immediately on 400/401/403, `model_decommissioned`, auth errors |

### 3.11 CodeGen Pipeline (`src/codegen/`)

Two generation paths:

**Path 1 — LangGraph Agent Pipeline (default):**
```
plan → resolve → generate → validate → (retry or assemble) → END
```

| Agent | File | Role |
|-------|------|------|
| `ScenarioPlanner` | `agents/scenario_planner.py` | Analyses full test case, classifies steps |
| `SelectorResolver` | `agents/selector_resolver.py` | Resolves element names to CSS selectors |
| `StepGenerator` | `agents/step_generator.py` | Generates complete test code in one LLM call |
| `ValidatorAgent` | `agents/validator_agent.py` | Reviews code, applies fixes; `should_retry_validation()` edge |
| `AssemblerAgent` | `agents/assembler_agent.py` | Combines into final project-ready file |

`CodeGenState` TypedDict in `pipeline.py` declares all state keys. `agent_config.py` is the single user-editable file for all prompts, framework context, action keywords, and pipeline settings.

**Path 2 — Legacy Template+LLM Pipeline (fallback):**
```
TemplateEngine → LLMGenerator → CodeValidator → TestOptimizer → CodeRenderer
```

`CodeGenOrchestrator.generate()` tries agent pipeline first, falls back to legacy on any exception.

---

## 4. Data Flow

```
┌─────────────┐     ┌──────────────────────────────────────────┐
│   INPUTS    │     │              PROCESSING                  │
│             │     │                                          │
│ • Chat      │────▶│  1. GraphRAG context retrieval           │
│   query     │     │     (knowledge graph 2-hop subgraph)     │
│             │     │                                          │
│ • Uploaded  │────▶│  2. DecisionAgent                        │
│   test files│     │     (tool_call | direct | rejected)      │
│             │     │                                          │
│ • Case      │────▶│  3. ToolSelectionAgent                   │
│   study     │     │     (tool name + typed arguments)        │
│             │     │                                          │
│ • Discovery │────▶│  4. ToolExecutor (11 tools)              │
│   answers   │     │     (KB lookup / graph search / scoring) │
│             │     │                                          │
│             │     │  5. SynthesisAgent                       │
│             │     │     (validate results, needs_more?)      │
│             │     │                                          │
│             │     │  6. EvaluationAgent                      │
│             │     │     (LLM synthesis with full context)    │
│             │     │                                          │
│             │     │  7. ReflectionAgent                      │
│             │     │     (critique + optional re-evaluate)    │
│             │     │                                          │
│             │     │  8. FormatAgent                          │
│             │     │     (markdown / table structure)         │
└─────────────┘     └──────────────────┬───────────────────────┘
                                       │
                    ┌──────────────────▼───────────────────────┐
                    │                OUTPUTS                    │
                    │                                          │
                    │  • Weighted scorecard (evaluation flow)  │
                    │  • Framework comparison table            │
                    │  • Migration path analysis               │
                    │  • Test coverage matrix                  │
                    │  • Framework detail cards                │
                    │  • Generated test code (CodeGen)         │
                    └──────────────────────────────────────────┘
```

---

## 5. Weighted Matrix Evaluation Flow

Triggered by typing `evaluate` or completing the discovery questionnaire.

```
User answers 8 discovery questions
              │
              ▼
   _build_profile() → UserProfile
              │
              ▼
   WeightProfile.from_preset()
   (auto-selects cloud_migration preset if cloud=True)
              │
              ▼
   ScoringEngine.evaluate(profile)
   ├── filter_by_architecture()     → candidate frameworks
   ├── weights.adjust(profile)      → dynamic weight tuning
   ├── _score_framework() × N       → CriteriaScores per framework
   │   ├── score_language_compatibility()
   │   ├── score_api_validation()
   │   ├── score_performance_load()
   │   ├── score_cicd_integration()
   │   ├── score_maintainability()
   │   ├── score_cloud_readiness()
   │   ├── score_license_cost()
   │   └── [C8–C10 if cloud_migration]
   ├── calculate_penalties()
   ├── calculate_bonuses()
   └── rank + derive pros/cons
              │
              ▼
   DecisionMatrix → formatted markdown report → chat
```

---

## 6. Directory Structure

```
automation-framework-advisor/
├── config/
│   └── .env                     # GROQ_API_KEY (template provided)
├── data/
│   ├── frameworks/              # 17 YAML framework profiles
│   │   └── case_studies/        # ingested case study files
│   ├── samples/                 # sample input/output JSON
│   └── knowledge_graph.json     # persistent graph store
├── docs/
│   ├── HLD.md                   # this document
│   ├── LLD_scoring_engine.md
│   ├── LLD_test_codegen.md
│   ├── evaluation_metrics.md
│   ├── LOGGING_GUIDE.md
│   ├── QUICK_START.md
│   └── TEST_CASE_COVERAGE_TOOL.md
├── logs/
│   └── advisor.log              # rotating log (5MB × 3)
├── src/
│   ├── agents/                  # 7-agent LangGraph pipeline
│   │   ├── decision_agent.py
│   │   ├── tool_selection_agent.py
│   │   ├── synthesis_agent.py
│   │   ├── evaluation_agent.py
│   │   ├── reflection_agent.py
│   │   ├── format_agent.py
│   │   ├── orchestrator.py
│   │   ├── langgraph_pipeline.py
│   │   ├── memory.py
│   │   ├── callbacks.py
│   │   ├── retry.py
│   │   └── state.py
│   ├── codegen/                 # Test code generation
│   │   ├── agents/              # 5-agent LangGraph codegen pipeline
│   │   │   ├── scenario_planner.py
│   │   │   ├── selector_resolver.py
│   │   │   ├── step_generator.py
│   │   │   ├── validator_agent.py
│   │   │   └── assembler_agent.py
│   │   ├── agent_config.py      # Single user-editable config (prompts + settings)
│   │   ├── pipeline.py          # LangGraph CodeGenState + build_codegen_pipeline()
│   │   ├── orchestrator.py      # CodeGenOrchestrator (agent + legacy paths)
│   │   ├── template_engine.py
│   │   ├── llm_generator.py
│   │   ├── validator.py
│   │   ├── optimizer.py
│   │   ├── renderer.py
│   │   ├── template_store.py
│   │   └── models.py
│   ├── graph/                   # Knowledge graph + GraphRAG
│   │   ├── knowledge_graph.py
│   │   ├── graph_store.py
│   │   ├── entity_extractor.py
│   │   └── graphrag_engine.py
│   ├── knowledge_base/          # YAML loader + schema
│   │   ├── loader.py
│   │   └── schema.py
│   ├── scoring/                 # Weighted matrix evaluation
│   │   ├── engine.py
│   │   ├── criteria.py
│   │   ├── weights.py
│   │   ├── penalties.py
│   │   └── models.py
│   ├── tools/                   # Tool implementations
│   │   ├── executor.py          # 11 tools
│   │   ├── coverage_engine.py
│   │   ├── cicd_generators.py
│   │   ├── prerequisites.py
│   │   ├── formatters.py
│   │   ├── excel_parser.py
│   │   └── code_sandbox.py
│   ├── llm/                     # LLM clients
│   │   ├── groq_client.py       # Primary (Groq cloud)
│   │   ├── ollama_client.py     # Legacy (local)
│   │   └── advisor_llm.py
│   ├── chat/                    # Rule-based fallback chat
│   ├── discovery/               # Requirements gathering
│   ├── migration/               # Migration planner + coverage
│   ├── generator/               # Boilerplate generator
│   ├── visualization/           # Knowledge graph visualization
│   ├── models.py                # UserProfile, enums
│   └── logging_config.py
├── streamlit_app.py             # Streamlit UI entry point
├── run.py                       # FastAPI server entry point
└── requirements.txt
```

---

## 7. Technology Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------| 
| Language | Python 3.11+ | Team expertise, rich ML ecosystem |
| UI | Streamlit | Rapid interactive prototyping |
| LLM inference | Groq cloud API | Fast inference, no local setup required |
| LLM model | `llama-3.3-70b-versatile` | Current working model; configurable via `GROQ_MODEL` |
| Agent orchestration | LangGraph | StateGraph with conditional edges, streaming |
| HTTP client | httpx | Async-capable, timeout control, browser-like headers for URL fetch |
| Data validation | Pydantic v2 | Type-safe models throughout |
| Knowledge graph | Custom (dict + JSON) | Lightweight, no external DB dependency |
| YAML parsing | PyYAML | Framework profile loading |
| Logging | Python logging + RotatingFileHandler | Structured logs, 5MB × 3 backups |
| Env management | python-dotenv | `load_dotenv(override=True)` for lazy API key loading |

---

## 8. Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------| 
| Cloud LLM | Groq API | Fast inference, no local setup, `llama-3.3-70b-versatile` |
| LangGraph TypedDict | All keys declared | Undeclared keys are silently dropped by LangGraph |
| API key loading | `_get_api_key()` reads fresh every call | Supports runtime `.env` updates without restart |
| Permanent error retry | Fail immediately on 400/401/403 | Avoids wasting retry budget on non-transient errors |
| Framework classification | `classify_framework_data()` via `architecture_fit` | Raw `category` YAML field was inconsistent across 12/17 files |
| CodeGen config | `agent_config.py` single file | Users only edit one file to customise all prompts and settings |
| CodeGen fallback | Legacy pipeline on agent exception | No regression risk when agent pipeline fails |
| Graph context size | Capped at 3000 chars | Prevents oversized prompts |
| Token min length | 5 chars | Stops stop-words matching hundreds of entities |
| Scoring + agents | Independent paths | Evaluation uses deterministic scoring; agents use LLM synthesis |

---

## 9. Deployment

```
┌─────────────────────────────────────────────┐
│              Local Machine                  │
│                                             │
│  ┌─────────────────┐   ┌─────────────────┐  │
│  │  Streamlit UI   │   │  Groq Cloud API │  │
│  │  Port: 8501     │──▶│  (external)     │  │
│  └─────────────────┘   │  llama-3.3-70b  │  │
│                        └─────────────────┘  │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │  data/                              │    │
│  │  ├── frameworks/*.yaml  (17 files)  │    │
│  │  ├── frameworks/case_studies/       │    │
│  │  └── knowledge_graph.json           │    │
│  └─────────────────────────────────────┘    │
└─────────────────────────────────────────────┘
```

**Start:**
```bash
# Set API key
echo "GROQ_API_KEY=your_key_here" > config/.env

# Run UI
streamlit run streamlit_app.py      # opens http://localhost:8501

# Or run API server
python run.py                       # opens http://localhost:8000
```

---

## 10. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Groq API rate limit | Slow/failed responses | Free tier: 14,400 req/day; retry with backoff; fallback to AdvisorChat |
| Model decommissioned | All LLM calls fail | `retry.py` detects `model_decommissioned` and fails fast; update `_DEFAULT_MODEL` |
| LangGraph state key missing | Silent data loss | All keys declared in `PipelineState` TypedDict |
| 403 on URL fetch | Case study not loaded | Browser-like headers; specific error message directing to manual copy-paste |
| Knowledge graph grows unbounded | Slow fuzzy matching | Entity cap (20) per query; min token length 5 |
| LLM hallucination | Wrong recommendation | All responses grounded via GraphRAG; reflection agent critiques draft |
| Stale framework data | Outdated advice | YAML profiles version-dated; graph enriched from user interactions |

---

*End of HLD Document — Version 3.0*
