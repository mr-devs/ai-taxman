# xAI (Grok)

**Not yet implemented.** xAI ships no first-party Python SDK for this surface; the documented
route is the OpenAI SDK pointed at a different `base_url`, or plain HTTP.

- **Endpoint:** `POST https://api.x.ai/v1/chat/completions` — **OpenAI Chat Completions
  compatible**, so much of the existing adapter's shape transfers. Note it is *Chat
  Completions*, not the Responses API taxman's OpenAI adapter uses.
- **Key env:** `XAI_API_KEY` (or `TAXMAN_XAI_API_KEY`).
- **Index:** <https://docs.x.ai/llms.txt>
- **Markdown convention:** `docs.x.ai/developers/<path>` + `.md`. There is **no `/docs` path
  segment** — `docs.x.ai/docs/anything.md` is a 404.

Start here — the request and response schema:

> <https://docs.x.ai/developers/rest-api-reference/inference/chat.md>

## Mapping the taxman feature set

| Taxman feature | xAI equivalent | Documentation |
|---|---|---|
| Send one message | `messages: [{role: "user", content: ...}]` | [Chat endpoint](https://docs.x.ai/developers/rest-api-reference/inference/chat.md), [Generate text](https://docs.x.ai/developers/model-capabilities/text/generate-text.md) |
| `system_prompt` | a `{role: "system"}` entry in `messages` | [Generate text](https://docs.x.ai/developers/model-capabilities/text/generate-text.md) |
| `max_output_tokens` | `max_tokens` | [Chat endpoint](https://docs.x.ai/developers/rest-api-reference/inference/chat.md) |
| `temperature`, `top_p` | same names | [Chat endpoint](https://docs.x.ai/developers/rest-api-reference/inference/chat.md) |
| `reasoning_effort` | `reasoning_effort` — same key, but only some models accept it | [Reasoning](https://docs.x.ai/developers/model-capabilities/text/reasoning.md) |
| `web_search` | the server-side search tool | [Web search](https://docs.x.ai/developers/tools/web-search.md), [Tools overview](https://docs.x.ai/developers/tools/overview.md), [X search](https://docs.x.ai/developers/tools/x-search.md) |
| `extract_text` | `choices[0].message.content` (plus `reasoning_content`) | [Chat endpoint](https://docs.x.ai/developers/rest-api-reference/inference/chat.md), [Reasoning](https://docs.x.ai/developers/model-capabilities/text/reasoning.md) |
| Refusals / stop reason | `choices[].finish_reason` | [Chat endpoint](https://docs.x.ai/developers/rest-api-reference/inference/chat.md) |
| `extract_usage` | `usage.prompt_tokens` / `completion_tokens` / `total_tokens` | [Chat endpoint](https://docs.x.ai/developers/rest-api-reference/inference/chat.md), [Cost tracking](https://docs.x.ai/developers/cost-tracking.md) |
| Reasoning tokens | `usage.completion_tokens_details.reasoning_tokens` | [Reasoning](https://docs.x.ai/developers/model-capabilities/text/reasoning.md) |
| `cached_tokens` | `usage.prompt_tokens_details.cached_tokens` | [Prompt caching](https://docs.x.ai/developers/advanced-api-usage/prompt-caching.md), [Usage and pricing](https://docs.x.ai/developers/advanced-api-usage/prompt-caching/usage-and-pricing.md) |
| Retry semantics | 429 and 5xx | [Debugging errors](https://docs.x.ai/developers/debugging.md), [Rate limits](https://docs.x.ai/developers/rate-limits.md) |
| `known_models()` | | [Models](https://docs.x.ai/developers/models.md), [Models endpoint](https://docs.x.ai/developers/rest-api-reference/inference/models.md), [Model comparison](https://docs.x.ai/developers/model-capabilities/text/comparison.md) |
| API key setup | `Authorization: Bearer` | [Quickstart](https://docs.x.ai/developers/quickstart.md) |
| SDK client | OpenAI SDK with `base_url="https://api.x.ai/v1"` | [Quickstart](https://docs.x.ai/developers/quickstart.md) |

## Beyond parity

| Capability | Documentation |
|---|---|
| **Batch mode** | [Batch API](https://docs.x.ai/developers/advanced-api-usage/batch-api.md), [Batches endpoint](https://docs.x.ai/developers/rest-api-reference/inference/batches.md) |
| Async / deferred completions | [Async](https://docs.x.ai/developers/advanced-api-usage/async.md), [Deferred chat completions](https://docs.x.ai/developers/advanced-api-usage/deferred-chat-completions.md) |
| Structured outputs | [Structured outputs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs.md) |
| Streaming | [Streaming](https://docs.x.ai/developers/model-capabilities/text/streaming.md) |
| Function calling | [Function calling](https://docs.x.ai/developers/tools/function-calling.md) |
| Citations from search | [Citations](https://docs.x.ai/developers/tools/citations.md) |
| Priority capacity | [Priority processing](https://docs.x.ai/developers/advanced-api-usage/priority-processing.md) |
| Cost estimation for an audit run | [Pricing](https://docs.x.ai/developers/pricing.md) |
| What changed recently | [Release notes](https://docs.x.ai/developers/release-notes.md) |

## Gotchas to check against the docs, not memory

- OpenAI-compatible is not OpenAI-identical. The response is Chat Completions shaped
  (`choices[].message`), so the OpenAI adapter's `extract()` — which walks the Responses API's
  `output[]` — does **not** transfer. Per the provider-isolation rule this belongs in its own
  subpackage anyway; do not try to share the extractor.
- xAI's `llms.txt` is a single ~1.5 MB file with every page inlined between `===/path===`
  markers, and it covers the Grok CLI as well as the API. Fetch a per-page `.md` URL unless
  you specifically need to search the whole corpus.
- Reasoning models reject some sampling parameters. Check
  [Reasoning](https://docs.x.ai/developers/model-capabilities/text/reasoning.md) before
  forwarding `temperature`.
