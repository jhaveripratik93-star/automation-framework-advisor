# Automation Framework Migration & Coverage Advisor

AI-powered tool that evaluates automation framework suitability, plans
seamless migrations, and verifies 100% functional coverage parity.

## Features

- **Interactive Discovery** – Adaptive Q&A to gather project requirements
- **Weighted Scoring Matrix** – Personalized scorecard comparing frameworks
- **Migration Roadmap** – Phased plan with effort estimates
- **Coverage Gap Analysis** – Ensures no functional coverage is lost
- **Boilerplate Generator** – Ready-to-run project templates with CI/CD
- **Groq LLM Advisor** – Cloud LLM-powered chat with fast inference (llama-3.1-70b/mixtral)
- **Persistent Knowledge Graph** – Self-growing graph seeded from 17 YAML profiles, grows from user interactions
- **GraphRAG Engine** – 2-hop subgraph retrieval for grounded, citation-backed responses
- **Criteria Sidebar** – Real-time weight sliders, preset selector, custom criteria with proportional redistribution
- **Knowledge Graph Visualization** – Entity-type color coding, confidence-based edge width, dashed user-contributed edges

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Set Groq API key (optional — enables LLM-backed features; the app
# falls back to rule-based logic if this isn't set)
export GROQ_API_KEY="your_api_key_here"
# or create config/.env with GROQ_API_KEY=your_api_key_here

# Run the Streamlit UI (recommended for interactive use)

.\.venv\Scripts\streamlit.exe run streamlit_app.py
# or
streamlit run streamlit_app.py 
# or 
python -m streamlit run streamlit_app.py
# Opens at http://localhost:8501

# Option 2: Run the API server (for programmatic access)
python run.py
# Server at http://localhost:8000
# API docs at http://localhost:8000/docs
```

> **Note:** The app uses a cloud LLM API for inference - no local setup required.
> If no provider is available, the advisor automatically falls back to the rule-based AdvisorChat.
>
> See [docs/QUICK_START.md](docs/QUICK_START.md) for a full first-steps walkthrough.

## LLM providers / configuring an LLM key

The app can use any one of three LLM providers for Advisor chat, Code
Studio, Test Generator, and Framework Discovery. You pick the active one
in the ⚙️ **Settings** dialog, or set keys directly in `config/.env`.

| Provider | Key env var | Model env var | Get a key |
|----------|-------------|---------------|-----------|
| Groq | `GROQ_API_KEY` | `GROQ_MODEL` | https://console.groq.com/keys |
| Gemini | `GEMINI_API_KEY` | `GEMINI_MODEL` | https://aistudio.google.com/apikey |
| OpenRouter | `OPENROUTER_API_KEY` | `OPENROUTER_MODEL` | https://openrouter.ai/keys |

Two ways to add a key for any provider:

1. **In the app UI** — open ⚙️ Settings, pick the provider's tab, paste
   the key (optionally set a model), and click **Save & use …**. It
   takes effect immediately (no restart) and is also written to
   `config/.env`.
2. **By hand** — edit `config/.env` (see the OpenRouter example below).

`config/.env` is **gitignored** — never commit your keys.

### OpenRouter

[OpenRouter](https://openrouter.ai) is an OpenAI-compatible gateway to
many models, including free, large-context, code-capable ones. Its
larger context window avoids the **413 payload too large** error some
other free tiers return on big conversions, and its free models avoid
Gemini's **429** free-tier quota limits.

1. Create a free account at https://openrouter.ai and generate an API
   key on the [Keys page](https://openrouter.ai/keys). The key looks
   like `sk-or-v1-...`.
2. Add it either way:
   - **UI:** ⚙️ Settings → **OpenRouter** tab → paste the key,
     optionally set a model → **Save & use OpenRouter**.
   - **config/.env:**
     ```
     OPENROUTER_API_KEY='sk-or-v1-...'
     OPENROUTER_MODEL='deepseek/deepseek-chat-v3.1:free'
     ```

Free model ids end in `:free`, and their exact ids / availability /
rate limits change over time — if the default stops working, open the
OpenRouter models list and pick another `:free` id, then set it in the
Settings OpenRouter tab or `OPENROUTER_MODEL`. Leaving `OPENROUTER_MODEL`
blank uses the app's built-in default.

## Project Structure

```
├── docs/                    # Design documents (HLD, LLD, metrics)
├── data/
│   ├── frameworks/          # YAML knowledge base (framework profiles)
│   ├── samples/             # Sample input/output for testing
│   ├── codegen/             # Learned code-generation templates
│   └── templates/           # Cookiecutter project templates
├── config/
│   └── conversion_prompts/  # Editable prompts for the code-conversion feature
├── mcp_server/              # MCP server exposing the advisor's tools to Kiro
├── src/
│   ├── discovery/           # Interactive requirements gathering
│   ├── scoring/             # Weighted evaluation engine
│   ├── migration/           # Migration planner + coverage analyzer
│   ├── generator/           # Boilerplate project generator
│   ├── knowledge_base/      # Framework data loader
│   ├── advisor_tools/       # Framework recommendation/comparison tools
│   ├── coverage/            # Test case coverage analysis tools
│   ├── prerequisites/       # Prerequisite/CI-CD automation tools
│   ├── conversion/          # Cross-framework test code conversion
│   ├── codegen/             # Manual-test-case-to-code generation
│   ├── agents/              # LangGraph pipeline + ToolRouter
│   ├── graph/               # Knowledge graph + GraphRAG engine
│   ├── tools/               # Shared cross-feature infra (formatters, repo comparator, etc.)
│   ├── models.py            # Shared data models
│   └── main.py              # FastAPI application
├── streamlit_app.py         # Streamlit frontend (interactive UI)
├── run.py                   # API server entry point
└── requirements.txt         # Python dependencies
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/frameworks` | List all frameworks |
| POST | `/api/v1/evaluate` | Evaluate & rank frameworks |
| POST | `/api/v1/migration-plan` | Generate migration roadmap |
| POST | `/api/v1/coverage-analysis` | Analyze coverage gaps |
| POST | `/api/v1/generate-boilerplate` | Generate project template |
| GET | `/api/v1/weight-presets` | Get scoring weight presets |

## Frameworks in Knowledge Base

- Playwright, Cypress, Selenium, WebdriverIO
- Robot Framework, TestCafe, Puppeteer
- Appium (Mobile), Karate (API+UI)
- K6, Locust (Performance/Load)
- REST Assured (API)
