# Google Gemini

**Not yet implemented.** No SDK extra declared yet; the package is `google-genai`.

- **Endpoint:** `generateContent` (`POST /v1beta/models/{model}:generateContent`). Google is
  mid-migration to an Interactions API; pages under `docs/generate-content/` are labelled
  "legacy Gemini Generate Content API" but remain the documented surface for this call.
- **Key env:** `GEMINI_API_KEY` (or `TAXMAN_GEMINI_API_KEY`).
- **Index:** <https://ai.google.dev/gemini-api/docs/llms.txt>
- **Markdown convention:** append **`.md.txt`**, not `.md`. `ai.google.dev/<path>.md` returns
  a rendered HTML page with a 200 status.

Start here — the full REST reference (large, ~220 KB; fetch a guide first if you only need
one parameter):

> <https://ai.google.dev/api/generate-content.md.txt>
> and the smaller entry point <https://ai.google.dev/api.md.txt>

## Mapping the taxman feature set

Gemini's shape diverges more than the others: request content is `contents[].parts[]`, the
tuning knobs live nested under `generationConfig`, and the system prompt is a separate
`systemInstruction` object rather than a string.

| Taxman feature | Gemini equivalent | Documentation |
|---|---|---|
| Send one message | `contents: [{parts: [{text: ...}]}]` | [Text generation](https://ai.google.dev/gemini-api/docs/generate-content/text-generation.md.txt), [Getting started](https://ai.google.dev/gemini-api/docs/generate-content/get-started.md.txt) |
| `system_prompt` | `systemInstruction` | [Text generation](https://ai.google.dev/gemini-api/docs/generate-content/text-generation.md.txt) |
| `max_output_tokens` | `generationConfig.maxOutputTokens` | [generateContent reference](https://ai.google.dev/api/generate-content.md.txt) |
| `temperature`, `top_p` | `generationConfig.temperature` / `topP` | [generateContent reference](https://ai.google.dev/api/generate-content.md.txt) |
| `reasoning_effort` | `generationConfig.thinkingConfig` — a token budget plus `includeThoughts` | [Gemini thinking](https://ai.google.dev/gemini-api/docs/generate-content/thinking.md.txt), [Thought signatures](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures.md.txt) |
| `web_search` | `tools: [{googleSearch: {}}]` | [Grounding with Google Search](https://ai.google.dev/gemini-api/docs/generate-content/google-search.md.txt) |
| `extract_text` | `candidates[0].content.parts[].text` | [generateContent reference](https://ai.google.dev/api/generate-content.md.txt) |
| Refusals / stop reason | `candidates[].finishReason`, plus `promptFeedback.blockReason` and `safetyRatings` | [generateContent reference](https://ai.google.dev/api/generate-content.md.txt) |
| `extract_usage` | `usageMetadata.promptTokenCount` / `candidatesTokenCount` / `totalTokenCount` | [Understand and count tokens](https://ai.google.dev/gemini-api/docs/generate-content/tokens.md.txt) |
| Reasoning tokens | `usageMetadata.thoughtsTokenCount` | [Gemini thinking](https://ai.google.dev/gemini-api/docs/generate-content/thinking.md.txt) |
| `cached_tokens` | `usageMetadata.cachedContentTokenCount` | [Context caching](https://ai.google.dev/gemini-api/docs/generate-content/caching.md.txt) |
| Retry semantics | 429 `RESOURCE_EXHAUSTED`, 503 `UNAVAILABLE` | [API errors](https://ai.google.dev/gemini-api/docs/api-errors.md.txt), [Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits.md.txt), [Troubleshooting](https://ai.google.dev/gemini-api/docs/troubleshooting.md.txt) |
| `known_models()` | | [Models](https://ai.google.dev/gemini-api/docs/models.md.txt) |
| API key setup | `x-goog-api-key` header | [Using Gemini API keys](https://ai.google.dev/gemini-api/docs/api-key.md.txt) |
| SDK client | `google.genai.Client(...).aio` | [Gemini API libraries](https://ai.google.dev/gemini-api/docs/libraries.md.txt) |

## Beyond parity

| Capability | Documentation |
|---|---|
| **Batch mode** — 50% cheaper, 24 h window | [Batch API](https://ai.google.dev/gemini-api/docs/batch-api.md.txt) |
| Structured outputs (`responseSchema`) | [Structured outputs](https://ai.google.dev/gemini-api/docs/generate-content/structured-output.md.txt) |
| Function calling | [Function calling](https://ai.google.dev/gemini-api/docs/generate-content/function-calling.md.txt) |
| Grounding with URLs or Maps | [URL context](https://ai.google.dev/gemini-api/docs/generate-content/url-context.md.txt), [Maps grounding](https://ai.google.dev/gemini-api/docs/generate-content/maps-grounding.md.txt) |
| Service tiers | [Flex inference](https://ai.google.dev/gemini-api/docs/generate-content/flex-inference.md.txt), [Priority inference](https://ai.google.dev/gemini-api/docs/generate-content/priority-inference.md.txt) |
| Cost estimation for an audit run | [Pricing](https://ai.google.dev/gemini-api/docs/pricing.md.txt) |
| Reusing the OpenAI adapter shape | [OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai.md.txt) — a partial shim; prefer the native SDK |
| What changed recently | [Release notes](https://ai.google.dev/gemini-api/docs/changelog.md.txt) |

## Gotchas to check against the docs, not memory

- **Safety blocking is a normal outcome, not an error.** A blocked prompt returns 200 with an
  empty `candidates` list and a `promptFeedback.blockReason`. An adapter that only reads
  `candidates[0]` will raise on a perfectly valid audit response — and for an auditing tool,
  a refusal is data worth recording, not a failure. Check `finishReason` and `blockReason`
  explicitly against the [generateContent reference](https://ai.google.dev/api/generate-content.md.txt).
- Thinking is **on by default** on recent models and its budget interacts with
  `maxOutputTokens`. See [Gemini thinking](https://ai.google.dev/gemini-api/docs/generate-content/thinking.md.txt).
- Free-tier keys have prompts logged for product improvement; paid do not. Relevant to an
  audit's reproducibility claims — see [Pricing](https://ai.google.dev/gemini-api/docs/pricing.md.txt).
