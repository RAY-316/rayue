# OpenAI Compatibility

Gemini provides an OpenAI-compatible endpoint for common SDK/client integrations.

Base URL:

```text
https://generativelanguage.googleapis.com/v1beta/openai/
```

TypeScript:

```ts
import OpenAI from "openai";

const client = new OpenAI({
  apiKey: process.env.GEMINI_API_KEY,
  baseURL: "https://generativelanguage.googleapis.com/v1beta/openai/",
});

const response = await client.chat.completions.create({
  model: "gemini-3.5-flash",
  messages: [{ role: "user", content: "Summarize this in one sentence." }],
});

console.log(response.choices[0].message.content);
```

Python:

```python
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["GEMINI_API_KEY"],
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
)

response = client.chat.completions.create(
    model="gemini-3.5-flash",
    messages=[{"role": "user", "content": "Summarize this in one sentence."}],
)

print(response.choices[0].message.content)
```

Use OpenAI compatibility for chat completions, streaming, function calling, image input, audio input, structured output, embeddings, and clients that already speak OpenAI API shape.

Use native Gemini Files API for local video understanding, large media/PDF input, and workflows that need file upload/cached content. Compatibility is useful, but not complete parity with every OpenAI Responses API or tool behavior.
