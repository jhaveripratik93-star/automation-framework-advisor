from src.llm.groq_client import GroqClient
from src.llm.token_usage import TokenUsage


client = GroqClient()

response = client.chat(
    messages=[
        {
            "role": "user",
            "content": "Explain Playwright versus Selenium in 200 words."
        }
    ]
)

print("Response:")
print(response["content"])

usage = TokenUsage.from_response(response)
usage.print()