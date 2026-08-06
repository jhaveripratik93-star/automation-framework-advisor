# Migration from Ollama to Groq API

This document describes the migration from local Ollama to cloud-based Groq API for LLM inference.

## Changes Made

### 1. New Files Created

#### `src/llm/groq_client.py`
- New Groq API client replacing `OllamaClient`
- Supports function calling (tool use) compatible with OpenAI format
- Uses Groq's fast inference API at `https://api.groq.com/openai/v1`
- Supported models:
  - `llama-3.1-70b-versatile` (default, best for complex tasks)
  - `llama-3.1-8b-instant` (faster, good for simple tasks)
  - `mixtral-8x7b-32768` (large context window)
  - `gemma2-9b-it` (efficient alternative)

#### `.env` and `.env.example`
- Secure storage for Groq API key
- `.env` contains your actual key
- `.env.example` is a template for other developers

#### `.gitignore`
- Ensures `.env` file is not committed to version control
- Standard Python ignore patterns

### 2. Modified Files

#### `src/llm/advisor_llm.py`
- Updated imports to support both `GroqClient` and `OllamaClient` (backwards compatible)
- Changed parameter from `ollama_client` to `llm_client` (generic)
- No other logic changes required

#### `src/graph/entity_extractor.py`
- Updated parameter from `ollama_client` to `llm_client`
- Supports both Groq and Ollama clients

#### `streamlit_app.py`
- Replaced `OllamaClient` import with `GroqClient`
- Updated `init_llm_stack()` to initialize Groq client with API key
- Loads API key from environment variable `GROQ_API_KEY` or uses hardcoded fallback
- Updated UI references from "Ollama" to "Groq API"
- All variable names changed from `ollama_client` to `groq_client`

#### `requirements.txt`
- Updated comments to reflect Groq usage
- No new dependencies needed (httpx already included)

#### `README.md`
- Updated Quick Start instructions
- Removed Ollama installation steps
- Added Groq API key setup instructions
- Simplified setup (no local services required)

### 3. API Key Configuration

The Groq API key is configured in the following priority order:

1. **Environment variable** (recommended for production):
   ```bash
   export GROQ_API_KEY="your_key_here"
   ```

2. **.env file** (for local development):
   ```
   GROQ_API_KEY=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
   ```

3. **Hardcoded fallback** in `streamlit_app.py` (for demo purposes):
   ```python
   groq_api_key = os.getenv("GROQ_API_KEY", "gsk_4JLW...")
   ```

## Benefits of Groq Migration

### Performance
- **Much faster inference**: Groq's LPU (Language Processing Unit) provides sub-second responses
- **No local GPU required**: All processing happens in the cloud
- **Consistent performance**: No variability based on local hardware

### Deployment
- **Zero local setup**: No need to install Ollama or download models
- **Cloud-ready**: Easy to deploy to production without infrastructure concerns
- **Scalable**: Groq handles scaling automatically

### Developer Experience
- **Faster iteration**: No waiting for model downloads or local inference
- **Better debugging**: Consistent API responses and error messages
- **Tool calling support**: Native function calling (same as OpenAI format)

## Testing the Migration

1. **Check API connectivity**:
   ```bash
   python -c "from src.llm.groq_client import GroqClient; c = GroqClient(api_key='gsk_4JLW...'); print(c.health_check())"
   ```

2. **Run the Streamlit app**:
   ```bash
   streamlit run streamlit_app.py
   ```

3. **Verify LLM features**:
   - Ask a question in the chat
   - Check that tool calls work (e.g., "Compare Playwright vs Cypress")
   - Verify knowledge graph entity extraction

## Fallback Behavior

The application maintains the same fallback strategy:

```
User Question
    ↓
AdvisorLLM.respond()
    ↓
Groq API (with tools) ──[if error]──→ AdvisorChat (rule-based)
    ↓
Response
```

If Groq API is unavailable or returns an error, the system automatically falls back to the rule-based `AdvisorChat` that uses keyword pattern matching.

## Backwards Compatibility

The code is structured to support both clients:

```python
# In advisor_llm.py TYPE_CHECKING block
try:
    from src.llm.groq_client import GroqClient as LLMClient
except ImportError:
    from src.llm.ollama_client import OllamaClient as LLMClient
```

This means you can switch back to Ollama by:
1. Changing imports in `streamlit_app.py`
2. Updating `init_llm_stack()` to create `OllamaClient()`

## Cost Considerations

Groq API pricing (as of 2024):
- Free tier: 14,400 requests/day per API key
- Paid tiers available for higher volume

For this advisor application with typical usage (~10-20 messages per session), the free tier should be sufficient for development and demo purposes.

## Security Notes

⚠️ **Important**: 
- The API key in `.env` is for development/demo only
- For production deployment:
  - Use environment variables set in your hosting platform
  - Rotate API keys regularly
  - Never commit `.env` to version control (it's in `.gitignore`)
  - Consider using secrets management (AWS Secrets Manager, etc.)

## Troubleshooting

### "Groq API not connected"
- Check your API key is correct in `.env`
- Verify internet connectivity
- Check Groq API status: https://status.groq.com

### "Rate limit exceeded"
- Wait a few minutes (free tier has rate limits)
- Consider upgrading to paid tier for higher limits

### Tool calls not working
- Ensure you're using a supported model (llama-3.1-70b or mixtral-8x7b)
- Check that the model supports function calling

### Slow responses
- Try switching to `llama-3.1-8b-instant` for faster inference
- Check your internet connection speed

## Next Steps

1. Test the application thoroughly with Groq
2. Monitor API usage in Groq console
3. Consider implementing response caching for repeated queries
4. Add retry logic with exponential backoff for production
