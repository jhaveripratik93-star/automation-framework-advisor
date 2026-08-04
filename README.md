# Automation Framework Migration & Coverage Advisor

AI-powered tool that evaluates automation framework suitability, plans
seamless migrations, and verifies 100% functional coverage parity.

## Features

- **Interactive Discovery** – Adaptive Q&A to gather project requirements
- **Weighted Scoring Matrix** – Personalized scorecard comparing frameworks
- **Migration Roadmap** – Phased plan with effort estimates
- **Coverage Gap Analysis** – Ensures no functional coverage is lost
- **Boilerplate Generator** – Ready-to-run project templates with CI/CD
- **Ollama LLM Advisor** – Local LLM-powered chat grounded by knowledge graph (llama3/mistral/codellama)
- **Persistent Knowledge Graph** – Self-growing graph seeded from 17 YAML profiles, grows from user interactions
- **GraphRAG Engine** – 2-hop subgraph retrieval for grounded, citation-backed responses
- **Criteria Sidebar** – Real-time weight sliders, preset selector, custom criteria with proportional redistribution
- **Knowledge Graph Visualization** – Entity-type color coding, confidence-based edge width, dashed user-contributed edges

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Install Ollama (required for LLM features)
# macOS/Linux:
curl -fsSL https://ollama.ai/install.sh | sh
# Windows: download from https://ollama.ai/download

# Add Ollama to PATH permanently (run in PowerShell as normal user):
$env:PATH += ";C:\Users\xjhapra\AppData\Local\Programs\Ollama"
[System.Environment]::SetEnvironmentVariable("PATH", $env:PATH + ";C:\Users\xjhapra\AppData\Local\Programs\Ollama", "User")

# Pull a supported model (choose one)
ollama pull llama3      # recommended
ollama pull mistral     # alternative
ollama pull codellama   # code-focused alternative

# Start Ollama (runs at http://localhost:11434)
ollama serve

# Option 1: Run the Streamlit UI (recommended for interactive use)
streamlit run streamlit_app.py 
or 
python -m streamlit run streamlit_app.py
# Opens at http://localhost:8501

# Option 2: Run the API server (for programmatic access)
python run.py
# Server at http://localhost:8000
# API docs at http://localhost:8000/docs
```

> **Note:** Ollama must be running locally before starting the app.
> If Ollama is unavailable, the advisor automatically falls back to
> the rule-based AdvisorChat.

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
