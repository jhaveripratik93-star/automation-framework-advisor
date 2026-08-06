# 🚀 Quick Start Guide - Groq-Powered Advisor

## 30-Second Setup

```bash
# 1. Install dependencies (if not already done)
pip install -r requirements.txt

# 2. Test Groq connection
python test_groq_connection.py

# 3. Start the app
streamlit run streamlit_app.py
```

That's it! Open http://localhost:8501 in your browser.

## First Steps in the App

1. **Try a simple question**: 
   - "What's the best framework for Python web testing?"
   
2. **Upload test files** (left sidebar):
   - Drag & drop your existing test files
   - App will auto-detect framework and language

3. **Start evaluation**:
   - Answer 8 quick discovery questions, OR
   - Say "evaluate frameworks for my React SPA"

4. **Get recommendations**:
   - See ranked frameworks with scores
   - Ask follow-up questions like "why did Playwright win?"
   - Request "migration plan" or "boilerplate"

## Key Features to Try

### 💬 Chat Questions
```
"Compare Playwright vs Cypress"
"Why is Playwright recommended?"
"What are Terraform's limitations?"
"Show migration plan from Selenium"
"Generate boilerplate for Playwright"
```

### 📊 Tool-Powered Analysis
These automatically use function calling:
- Framework comparisons
- Knowledge graph queries
- Test case coverage analysis
- Uploaded document analysis

### 🎛️ Configuration (right sidebar)
- Adjust criteria weights
- Switch LLM models (llama-3.3-70b-versatile vs llama-3.1-8b-instant)
- Change temperature/max tokens
- Apply weight presets (balanced, cloud_migration, etc.)

## Common Workflows

### Workflow 1: Find Best Framework
```
1. Click chat input
2. Type: "I need to test a React SPA with REST APIs, team uses Python"
3. Answer discovery questions (8 quick prompts)
4. Review evaluation report
5. Ask: "Compare top 3 options"
```

### Workflow 2: Plan Migration
```
1. Upload existing test files (left sidebar)
2. Say: "We have 300 Selenium tests, show migration plan"
3. Review phased roadmap with effort estimates
4. Ask: "What coverage gaps will we have?"
```

### Workflow 3: Generate Project
```
1. Complete evaluation first
2. Say: "Generate boilerplate for Playwright"
3. Review generated files (conftest.py, Dockerfile, CI/CD)
4. Download or copy files to your project
```

## Troubleshooting

### App won't start
```bash
# Check Python version (3.11+ required)
python --version

# Reinstall dependencies
pip install -r requirements.txt --upgrade
```

### "Groq API not connected"
```bash
# Test connection
python test_groq_connection.py

# Check .env file exists
cat .env

# Verify API key (first 20 chars)
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print(os.getenv('GROQ_API_KEY')[:20])"
```

### Slow responses
- Switch to `llama-3.1-8b-instant` (faster model)
- In app: 🤖 LLM Settings → Model dropdown → select instant model

### Rate limit errors
- Free tier: 14,400 requests/day
- Wait a few minutes, or
- Upgrade at https://console.groq.com

## Tips & Tricks

### 🎯 Get Better Answers
- Be specific: "Python framework for API testing" vs "testing framework"
- Upload test files for auto-detection
- Ask follow-up questions to refine

### 📈 Optimize Performance  
- Use llama-3.1-8b-instant for simple questions
- Use llama-3.3-70b-versatile for complex analysis
- Lower temperature (0.3-0.5) for factual answers
- Higher temperature (0.7-0.9) for creative suggestions

### 🔍 Explore Knowledge Graph
- After evaluation, expand "🕸️ Knowledge Graph" section
- Interactive visualization of framework relationships
- Color-coded by entity type
- Edge thickness = confidence score

### 📝 Custom Criteria
- Add your own evaluation criteria (e.g., "Mobile Support")
- Weight redistributes automatically
- Integrated into scoring in real-time

## Example Chat Session

```
You: What's the best Python testing framework?

Advisor: Based on your question, I recommend Playwright for Python because...
[Detailed analysis with scores]

You: Compare Playwright vs Selenium

Advisor: Here's a head-to-head comparison:
[Side-by-side table with 7 criteria]

You: Why is Playwright better for CI/CD?

Advisor: Playwright scores 92/100 on CI/CD because...
[Explanation with knowledge graph context]

You: Show migration plan from Selenium

Advisor: Migration Roadmap → Playwright
- Total scripts: 300
- Estimated effort: 8 weeks
- Phase 1: Foundation (2 weeks)
  - Setup Playwright project...
[Full phased plan]
```

## Next Steps

1. ✅ Complete this quick start
2. 📖 Read detailed docs: `GROQ_MIGRATION.md`
3. 🧪 Run evaluation for your actual project
4. 📊 Check Groq usage: https://console.groq.com
5. 🚀 Deploy to production (set `GROQ_API_KEY` env var)

## Need Help?

- Check logs: `logs/advisor.log`
- Run diagnostics: `python test_groq_connection.py`
- Review docs: `README.md`, `GROQ_MIGRATION.md`
- Test API directly at: https://console.groq.com/playground

---

**You're all set!** Start chatting with the advisor and find the perfect testing framework for your project. 🎉
