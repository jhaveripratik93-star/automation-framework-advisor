# Quick Start Guide

## 30-Second Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your Groq API key
echo "GROQ_API_KEY=your_key_here" > config/.env

# 3. Start the app
streamlit run streamlit_app.py
```

Open http://localhost:8501 in your browser.

> **No local LLM required.** The app uses Groq's cloud API. If Groq is unavailable, it automatically falls back to the rule-based AdvisorChat.

---

## First Steps in the App

1. **Try a simple question:**
   - "What's the best framework for Python web testing?"

2. **Upload test files** (left sidebar):
   - Drag & drop your existing test files
   - App auto-detects framework and language

3. **Add a case study** (left sidebar → Case Study expander):
   - 📄 **File tab** — upload a PDF/text document
   - 🔗 **URL tab** — paste one or more URLs (fetched with browser-like headers)

4. **Start evaluation:**
   - Answer 8 quick discovery questions, OR
   - Say "evaluate frameworks for my React SPA"

5. **Get recommendations:**
   - See ranked frameworks with scores
   - Ask follow-up questions like "why did Playwright win?"
   - Request "migration plan" or "generate boilerplate"

---

## Key Features to Try

### 💬 Chat Questions
```
"Compare Playwright vs Cypress"
"Which frameworks support API testing?"
"What are Terraform's limitations?"
"Show migration plan from Selenium"
"Generate boilerplate for Playwright"
"Which frameworks are best for mobile testing?"
```

### 📊 Tool-Powered Analysis
These automatically trigger tool calls:
- Framework comparisons
- Knowledge graph queries
- Test case coverage analysis
- Uploaded document analysis
- Category-based framework listing

### 🎛️ Criteria Sidebar
- Adjust per-criterion weights with sliders
- Switch between presets (balanced, cloud_migration, api_heavy, etc.)
- Add custom criteria — weights redistribute proportionally
- Apply weights → sets preset to "custom"

### 🤖 Test Code Generator
- Upload manual test cases (Excel/JSON)
- Select target framework
- LangGraph 5-agent pipeline: Plan → Resolve → Generate → Validate → Assemble
- Download generated code

---

## Common Workflows

### Workflow 1: Find Best Framework
```
1. Type: "I need to test a React SPA with REST APIs, team uses Python"
2. Answer discovery questions (8 quick prompts)
3. Review evaluation report
4. Ask: "Compare top 3 options"
```

### Workflow 2: Plan Migration
```
1. Upload existing test files (left sidebar)
2. Say: "We have 300 Selenium tests, show migration plan"
3. Review phased roadmap with effort estimates
4. Ask: "What coverage gaps will we have?"
```

### Workflow 3: Generate Test Code
```
1. Go to Test Generator tab
2. Upload manual test cases (JSON or Excel)
3. Select target framework (e.g. Playwright TypeScript)
4. Click Generate — agent pipeline runs automatically
5. Review and download generated files
```

### Workflow 4: Case Study Analysis
```
1. Expand "Case Study" in left sidebar
2. Paste a URL or upload a document
3. Ask: "Based on the case study, which framework fits best?"
4. Advisor cites relevant excerpts from your document
```

---

## Troubleshooting

### App won't start
```bash
# Check Python version (3.11+ required)
python --version

# Reinstall dependencies
pip install -r requirements.txt --upgrade
```

### "GROQ_API_KEY is not set"
```bash
# Check config/.env exists and has the key
type config\.env

# Or set as environment variable
set GROQ_API_KEY=your_key_here   # Windows
export GROQ_API_KEY=your_key_here  # Linux/Mac
```

### Slow responses
- Switch to `llama-3.1-8b-instant` in the LLM Settings sidebar
- Lower `max_tokens` for faster responses

### Rate limit errors
- Free tier: 14,400 requests/day
- Wait a few minutes, or upgrade at https://console.groq.com

### 403 on URL fetch (case study)
- Some sites block automated requests
- Copy-paste the page content manually into the File tab instead

### Off-topic filter blocking your question
- The advisor filters non-automation queries
- Rephrase to include automation/testing context

---

## Tips & Tricks

### 🎯 Get Better Answers
- Be specific: "Python framework for API testing" vs "testing framework"
- Upload test files for auto-detection
- Add a case study for context-aware recommendations

### 📈 Optimize Performance
- Use `llama-3.1-8b-instant` for simple questions
- Use `llama-3.3-70b-versatile` for complex analysis
- Lower temperature (0.3–0.5) for factual answers

### 🔍 Explore the Knowledge Graph
- After evaluation, expand "🕸️ Knowledge Graph" section
- Color-coded by entity type
- Edge thickness = confidence score
- Dashed edges = user-contributed

### 📝 Custom Criteria
- Add your own evaluation criteria (e.g. "Mobile Support")
- Weight redistributes automatically across all criteria
- Integrated into scoring in real-time

---

## Example Chat Session

```
You: What's the best Python testing framework?

Advisor: Based on your question, I recommend Playwright for Python because...
[Detailed analysis with scores]

You: Compare Playwright vs Selenium

Advisor: Here's a head-to-head comparison:
[Side-by-side table with 7 criteria]

You: Show migration plan from Selenium

Advisor: Migration Roadmap → Playwright
- Phase 1: Foundation (2 weeks)
- Phase 2: Core migration (4 weeks)
- Phase 3: Optimization (2 weeks)
[Full phased plan with effort estimates]
```

---

## API Server (Optional)

```bash
python run.py
# Server at http://localhost:8000
# API docs at http://localhost:8000/docs
```

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/frameworks` | List all frameworks |
| POST | `/api/v1/evaluate` | Evaluate & rank frameworks |
| POST | `/api/v1/migration-plan` | Generate migration roadmap |
| POST | `/api/v1/coverage-analysis` | Analyze coverage gaps |
| POST | `/api/v1/generate-boilerplate` | Generate project template |
| GET | `/api/v1/weight-presets` | Get scoring weight presets |

---

## Need Help?

- Check logs: `logs/advisor.log`
- Review docs: `docs/HLD.md`, `docs/LLD_test_codegen.md`
- Groq console: https://console.groq.com/playground
- Groq usage: https://console.groq.com
