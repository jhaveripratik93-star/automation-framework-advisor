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

# Set Groq API key (required for LLM features)
# Option 1: Environment variable (recommended)
export GROQ_API_KEY="your_api_key_here"

# Option 2: The key is already hardcoded in the app as fallback

# Option 1: Run the Streamlit UI (recommended for interactive use)
streamlit run streamlit_app.py 
# or 
python -m streamlit run streamlit_app.py
# Opens at http://localhost:8501

# Option 2: Run the API server (for programmatic access)
python run.py
# Server at http://localhost:8000
# API docs at http://localhost:8000/docs
```

> **Note:** The app uses Groq's cloud API for LLM inference - no local setup required.
> If Groq is unavailable, the advisor automatically falls back to the rule-based AdvisorChat.

## Project Structure

```
├── docs/                    # Design documents (HLD, LLD, metrics)
├── data/
│   ├── frameworks/          # YAML knowledge base (framework profiles)
│   ├── samples/             # Sample input/output for testing
│   └── templates/           # Cookiecutter project templates
├── src/
│   ├── discovery/           # Interactive requirements gathering
│   ├── scoring/             # Weighted evaluation engine
│   ├── migration/           # Migration planner + coverage analyzer
│   ├── generator/           # Boilerplate project generator
│   ├── knowledge_base/      # Framework data loader
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
