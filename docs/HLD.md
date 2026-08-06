# High-Level Design (HLD)
# Automation Framework Migration & Coverage Advisor

**Version:** 2.0
**Date:** August 2026
**Status:** Active

---

## 1. Executive Summary

An AI-powered advisor that helps engineering teams select, evaluate, and migrate to the right test automation framework. The system combines a **weighted scoring matrix** with a **GraphRAG knowledge engine** and a **4-agent agentic pipeline** to produce grounded, context-aware recommendations.

Key capabilities:
1. **Weighted Decision Matrix** — scores 17+ frameworks across 7–10 configurable criteria
2. **GraphRAG Context Retrieval** — 2-hop knowledge graph subgraph grounding every LLM response
3. **4-Agent Pipeline** — Decision → Tool Selection → Evaluation → Format agents
4. **Human-in-the-Loop (HITL)** — every agent-generated response is reviewed before publishing to chat
5. **Persistent Knowledge Graph** — self-growing graph seeded from 17 YAML profiles, enriched by user interactions

---

## 2. System Context Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER / QA LEAD                           │
│  (chat queries · uploaded test files · case study documents)    │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                   STREAMLIT FRONTEND (UI)                       │
│                                                                 │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │ Chat Panel   │  │ HITL Review Panel│  │ Sidebar          │  │
│  │ (history)    │  │ approve/edit/    │  │ file upload      │  │
│  │              │  │ discard          │  │ weight sliders   │  │
│  └──────────────┘  └──────────────────┘  └──────────────────┘  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    AGENT ORCHESTRATOR                           │
│                                                                 │
│  ┌──────────────┐  ┌──────────────────┐                        │
│  │ Decision     │  │ Tool Selection   │                        │
│  │ Agent        │→ │ Agent            │                        │
│  └──────────────┘  └────────┬─────────┘                        │
│                             │ tool calls                        │
│                             ▼                                   │
│                    ┌────────────────┐                           │
│                    │ Tool Executor  │                           │
│                    │ (src/tools/)   │                           │
│                    └────────┬───────┘                           │
│                             │ raw results                       │
│                             ▼                                   │
│  ┌──────────────┐  ┌──────────────────┐                        │
│  │ Evaluation   │  │ Format Agent     │→ HITLState (pending)   │
│  │ Agent        │→ │                  │                        │
│  └──────────────┘  └──────────────────┘                        │
└────────────────────────────┬────────────────────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
   │ Ollama LLM   │  │ GraphRAG     │  │ Scoring      │
   │ (local)      │  │ Engine       │  │ Engine       │
   │ llama3/      │  │ (2-hop       │  │ (weighted    │
   │ mistral      │  │  subgraph)   │  │  matrix)     │
   └──────────────┘  └──────┬───────┘  └──────────────┘
                            │
                   ┌────────▼────────┐
                   │ Knowledge Graph │
                   │ (306 entities   │
                   │  604 relations) │
                   └────────┬────────┘
                            │
                   ┌────────▼────────┐
                   │ Knowledge Base  │
                   │ 17 YAML profiles│
                   └─────────────────┘
```

---

## 3. Architecture Components

### 3.1 Streamlit Frontend

| Aspect | Detail |
|--------|--------|
| **File** | `streamlit_app.py` |
| **Layout** | Left sidebar (inputs) · Main chat panel · HITL review panel |
| **Sidebar** | File upload, case study upload, weight preset selector, per-criterion sliders |
| **Chat** | Full message history, welcome message, discovery questionnaire flow |
| **HITL Panel** | Appears below chat when a draft is pending; shows tool metadata, editable text area, Approve / Send Edited / Discard buttons |

### 3.2 Agent Orchestrator (`src/agents/orchestrator.py`)

Central coordinator of the 4-agent pipeline. Manages `HITLState` — a dataclass that holds the draft response and is serialised into Streamlit `session_state`.

**Pipeline steps:**
```
user query
  → GraphRAG.retrieve_context()          # pre-fetch graph context
  → DecisionAgent.decide()               # tool_call | direct
  → ToolSelectionAgent.select()          # picks tool + builds args
  → ToolExecutor.execute()               # runs tool, returns raw string
  → EvaluationAgent.evaluate()           # LLM synthesises results
  → FormatAgent.format()                 # markdown / table structure
  → HITLState(pending=True)              # returned to UI for review
```

### 3.3 Decision Agent (`src/agents/decision_agent.py`)

Decides whether the query needs a tool call or can be answered directly from graph context.

| Signal | Action |
|--------|--------|
| Keywords: compare, recommend, migrate, coverage, evaluate | `tool_call` |
| Keywords: hello, what is, explain + short message | `direct` |
| Graph context > 200 chars already retrieved | `direct` |
| Default (no strong signal) | `tool_call` |

### 3.4 Tool Selection Agent (`src/agents/tool_selection_agent.py`)

Maps user intent to the correct tool and builds typed arguments.

| Intent | Tool |
|--------|------|
| compare / vs / versus | `run_framework_comparison` |
| migrate / migration / move from | `find_migration_paths` |
| coverage / test case | `analyze_test_case_coverage` |
| uploaded / file / case study | `analyze_uploaded_content` |
| details / capabilities / about | `get_framework_details` |
| default | `search_knowledge_graph` |

### 3.5 Tool Executor (`src/tools/executor.py`)

Implements 6 tools that query the knowledge graph, knowledge base, and uploaded documents:

| Tool | Description |
|------|-------------|
| `search_knowledge_graph` | 2-hop GraphRAG subgraph retrieval |
| `get_framework_details` | Full YAML profile for a named framework |
| `run_framework_comparison` | Side-by-side capability comparison |
| `find_migration_paths` | MIGRATES_TO relationships from knowledge graph |
| `analyze_test_case_coverage` | Coverage matrix across frameworks |
| `analyze_uploaded_content` | Keyword search in uploaded files / case study |

### 3.6 Evaluation Agent (`src/agents/evaluation_agent.py`)

Sends tool results + graph context to the LLM via `/api/chat` (no tools, pure synthesis). Produces a coherent draft answer grounded in retrieved data.

### 3.7 Format Agent (`src/agents/format_agent.py`)

Applies consistent markdown structure based on query type:

| Query type | Format applied |
|------------|---------------|
| compare / vs | Ensures markdown table |
| migrate / roadmap | Ensures phase headings |
| coverage | Ensures summary table |
| default | Adds `### Response` heading if missing |

Appends HITL footer notice to all drafts.

### 3.8 Scoring Engine (`src/scoring/`)

Standalone weighted matrix evaluation triggered by the discovery questionnaire or explicit `evaluate` command. Completely independent of the agent pipeline.

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

### 3.9 GraphRAG Engine (`src/graph/graphrag_engine.py`)

Retrieves structured knowledge graph context to ground every LLM call.

**Algorithm:**
1. Tokenise query (min 5 chars per token to skip stop-words)
2. Exact-match tokens against entity name index
3. Fuzzy-match remaining tokens (threshold 0.75, cap 20 entities)
4. Retrieve 2-hop subgraph from matched entity IDs
5. Format as `[source] --relationship--> [target] (confidence)` triples
6. YAML fallback if no graph entities matched

### 3.10 Knowledge Graph (`src/graph/`)

Persistent entity-relationship graph seeded from 17 YAML framework profiles and enriched by user interactions.

| Component | File | Purpose |
|-----------|------|---------|
| `KnowledgeGraph` | `knowledge_graph.py` | In-memory graph with name index |
| `GraphStore` | `graph_store.py` | Atomic JSON persistence (`data/knowledge_graph.json`) |
| `EntityExtractor` | `entity_extractor.py` | Extracts entities/relationships from YAML and user messages |
| `GraphRAGEngine` | `graphrag_engine.py` | Context retrieval (see §3.9) |

Current graph size: **306 entities, 604 relationships** across entity types: `framework`, `language`, `capability`, `limitation`, `ci_cd_tool`, `cloud_provider`, `migration_path`.

### 3.11 Knowledge Base (`src/knowledge_base/`)

17 YAML framework profiles loaded at startup. Each profile contains: languages, architecture fit, capabilities, CI/CD integration, cloud grids, limitations, performance, maintainability.

**Frameworks:** Playwright, Cypress, Selenium WebDriver, WebdriverIO, Robot Framework, TestCafe, Puppeteer, Appium, Karate, K6, Locust, REST Assured, Terraform, Ansible, Chef, Pulumi, AWS CloudFormation.

### 3.12 Ollama LLM Client (`src/llm/ollama_client.py`)

Local LLM inference via Ollama HTTP API.

| Feature | Detail |
|---------|--------|
| Chat endpoint | `/api/chat` (tool-calling support) |
| Generate endpoint | `/api/generate` (entity extraction) |
| Tool support probe | Sends minimal tools payload at startup; sets `supports_tools` flag |
| Models | `llama3:latest` (8B), `mistral:latest` (7B) |
| Timeout | 180s |
| Max tokens | 256 |

---

## 4. Data Flow

```
┌─────────────┐     ┌──────────────────────────────────────────┐
│   INPUTS    │     │              PROCESSING                  │
│             │     │                                          │
│ • Chat      │────▶│  1. GraphRAG context retrieval           │
│   query     │     │     (knowledge graph 2-hop subgraph)     │
│             │     │                                          │
│ • Uploaded  │────▶│  2. Decision Agent                       │
│   test files│     │     (tool_call | direct)                 │
│             │     │                                          │
│ • Case      │────▶│  3. Tool Selection Agent                 │
│   study     │     │     (tool name + typed arguments)        │
│             │     │                                          │
│ • Discovery │────▶│  4. Tool Executor                        │
│   answers   │     │     (KB lookup / graph search)           │
│             │     │                                          │
│             │     │  5. Evaluation Agent                     │
│             │     │     (LLM synthesis via /api/chat)        │
│             │     │                                          │
│             │     │  6. Format Agent                         │
│             │     │     (markdown / table structure)         │
│             │     │                                          │
│             │     │  7. HITL Review                          │
│             │     │     (approve / edit / discard)           │
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
                    │  • Knowledge graph context citations     │
                    └──────────────────────────────────────────┘
```

---

## 5. Human-in-the-Loop (HITL) Flow

```
Agent pipeline produces HITLState(pending=True, draft=...)
              │
              ▼
   ┌──────────────────────────────────┐
   │     HITL Review Panel (UI)       │
   │                                  │
   │  Draft shown in editable area    │
   │  Tool names + format type shown  │
   │                                  │
   │  [✅ Approve]  [✏️ Edit]  [🗑️ Discard] │
   └──────────┬───────────┬───────────┘
              │           │
     approved │           │ edited
              ▼           ▼
   Strip HITL footer   Replace draft
   Return final text   with edited text
              │           │
              └─────┬─────┘
                    ▼
         Appended to chat history
         HITLState cleared
```

---

## 6. Weighted Matrix Evaluation Flow

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

## 7. Directory Structure

```
Automation_framework_migration/
├── data/
│   ├── frameworks/          # 17 YAML framework profiles
│   ├── samples/             # sample input/output JSON
│   └── knowledge_graph.json # persistent graph store
├── docs/
│   ├── HLD.md               # this document
│   ├── LLD_scoring_engine.md
│   ├── evaluation_metrics.md
│   └── LOGGING_GUIDE.md
├── logs/
│   └── advisor.log          # rotating log (5MB × 3)
├── src/
│   ├── agents/              # 4-agent pipeline
│   │   ├── decision_agent.py
│   │   ├── tool_selection_agent.py
│   │   ├── evaluation_agent.py
│   │   ├── format_agent.py
│   │   └── orchestrator.py
│   ├── graph/               # knowledge graph + GraphRAG
│   │   ├── knowledge_graph.py
│   │   ├── graph_store.py
│   │   ├── entity_extractor.py
│   │   └── graphrag_engine.py
│   ├── knowledge_base/      # YAML loader + schema
│   │   ├── loader.py
│   │   └── schema.py
│   ├── scoring/             # weighted matrix evaluation
│   │   ├── engine.py
│   │   ├── criteria.py
│   │   ├── weights.py
│   │   ├── penalties.py
│   │   └── models.py
│   ├── tools/               # tool implementations
│   │   └── executor.py
│   ├── llm/                 # LLM clients
│   │   ├── ollama_client.py
│   │   └── advisor_llm.py
│   ├── models.py            # UserProfile, enums
│   └── logging_config.py
├── streamlit_app.py         # Streamlit UI entry point
└── requirements.txt
```

---

## 8. Technology Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Language | Python 3.11+ | Team expertise, rich ML ecosystem |
| UI | Streamlit | Rapid interactive prototyping |
| LLM inference | Ollama (local) | No API cost, privacy, offline capable |
| LLM models | llama3:latest, mistral:latest | Available locally; mistral faster on CPU |
| HTTP client | httpx | Async-capable, timeout control |
| Data validation | Pydantic v2 | Type-safe models throughout |
| Knowledge graph | Custom (dict + JSON) | Lightweight, no external DB dependency |
| YAML parsing | PyYAML | Framework profile loading |
| Logging | Python logging + RotatingFileHandler | Structured logs, 5MB × 3 backups |

---

## 9. Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Local LLM vs cloud API | Ollama (local) | No cost, no data privacy concerns, works offline |
| Tool support detection | Probe at startup | `llama3:latest` returns 400 for tools; probe sets `supports_tools` flag once |
| Graph context size | Capped at 3000 chars | Prevents 31KB prompts that cause 180s timeouts on CPU |
| Token min length | 5 chars | Stops stop-words (`test`, `for`, `are`) matching hundreds of entities |
| HITL placement | After format agent | Human reviews formatted output, not raw LLM text |
| Scoring + agents | Independent paths | Evaluation uses deterministic scoring; agents use LLM synthesis |
| Weighted matrix trigger | Explicit (`evaluate`) | Prevents accidental re-scoring on every chat message |

---

## 10. Deployment

```
┌─────────────────────────────────────────────┐
│              Local Machine                  │
│                                             │
│  ┌─────────────────┐   ┌─────────────────┐  │
│  │  Streamlit UI   │   │  Ollama Server  │  │
│  │  Port: 8501     │──▶│  Port: 11434    │  │
│  └─────────────────┘   │  llama3:latest  │  │
│                        │  mistral:latest │  │
│                        └─────────────────┘  │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │  data/                              │    │
│  │  ├── frameworks/*.yaml  (17 files)  │    │
│  │  └── knowledge_graph.json           │    │
│  └─────────────────────────────────────┘    │
└─────────────────────────────────────────────┘
```

**Start:**
```bash
ollama serve                        # ensure Ollama is running
streamlit run streamlit_app.py      # opens http://localhost:8501
```

---

## 11. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| LLM timeout on CPU (llama3 8B) | Fallback to direct respond | 180s timeout; graph context capped at 3000 chars; `max_tokens=256` |
| Tool calling not supported by model | 400 error | Startup probe sets `supports_tools`; tools silently dropped if unsupported |
| Knowledge graph grows unbounded | Slow fuzzy matching | Entity cap (20) per query; min token length 5 |
| LLM hallucination | Wrong recommendation | All responses grounded via GraphRAG; HITL review before publishing |
| Stale framework data | Outdated advice | YAML profiles version-dated; graph enriched from user interactions |

---

## 12. Future Enhancements

- **Groq cloud LLM** — faster inference fallback when Ollama is slow
- **Vector embeddings** — semantic search over framework docs (replace fuzzy matching)
- **Automated script conversion** — LLM-based Selenium → Playwright code translation
- **CI/CD pipeline analyser** — scan existing pipelines and suggest optimisations
- **Multi-account cloud evaluation** — Terraform/Ansible scoring for enterprise setups
- **Knowledge graph visualisation** — interactive entity-relationship explorer

---

*End of HLD Document — Version 2.0*
