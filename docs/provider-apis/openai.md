# OpenAI

Implemented in [`src/ai_taxman/providers/openai/`](../../src/ai_taxman/providers/openai/).

- **Endpoint:** Responses API, `POST /v1/responses`, via `AsyncOpenAI().responses.create()`.
- **Key env:** whatever the audit's `api_key_env:` names. `OPENAI_API_KEY` is OpenAI's own
  convention and the hint `taxman audits new` writes into a comment; taxman resolves no other name.
- **Index:** <https://developers.openai.com/api/llms.txt>
- **Markdown convention:** `developers.openai.com/api/<path>` + `.md`. Note the host —
  `platform.openai.com/...md` returns HTML with a 200.

The single most useful page is the Responses *create* reference: it is the authoritative list
of every request parameter and every response field, and it is what to read before adding any
key to the `model:` block.

> <https://developers.openai.com/api/reference/resources/responses/methods/create.md>

## What taxman implements today

| Feature | Where in the code | Documentation |
|---|---|---|
| `responses.create` call | `provider.py` `send()` | [Responses — Create](https://developers.openai.com/api/reference/resources/responses/methods/create.md), [Responses resource](https://developers.openai.com/api/reference/resources/responses.md) |
| `input` (the message text) | `provider.py` `build_request()` | [Text generation](https://developers.openai.com/api/docs/guides/text.md) |
| `model:` `name` | `config.py` | [Models catalog](https://developers.openai.com/api/docs/models.md), [All models](https://developers.openai.com/api/docs/models/all.md) |
| `system_prompt` (core's, `Request.system_prompt`) → `instructions` | `provider.py` | [Text generation](https://developers.openai.com/api/docs/guides/text.md) |
| `temperature`, `top_p` | `config.py` | [Responses — Create](https://developers.openai.com/api/reference/resources/responses/methods/create.md) |
| `max_output_tokens` | `config.py` | [Responses — Create](https://developers.openai.com/api/reference/resources/responses/methods/create.md) |
| `reasoning_effort` → `reasoning.effort` | `provider.py` | [Reasoning models](https://developers.openai.com/api/docs/guides/reasoning.md), [Reasoning best practices](https://developers.openai.com/api/docs/guides/reasoning-best-practices.md) |
| `search.web_search` → `tools: [{type: web_search}]` | `provider.py` | [Web search](https://developers.openai.com/api/docs/guides/tools-web-search.md), [Tools overview](https://developers.openai.com/api/docs/guides/tools.md) |
| `store` (always sent, defaults false) | `config.py` | [Data controls](https://developers.openai.com/api/docs/guides/your-data.md), [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state.md) |
| Retry set: `RateLimitError`, `InternalServerError`, `ConflictError`, `APIConnectionError`, `APITimeoutError` | `provider.py` `RETRYABLE_ERRORS` | [Error codes](https://developers.openai.com/api/docs/guides/error-codes.md), [Rate limits](https://developers.openai.com/api/docs/guides/rate-limits.md) |
| Client construction, `max_retries=0` | `provider.py` `_new_client()` | [Python SDK](https://developers.openai.com/api/docs/libraries.md) |

## Known gaps, with the spec to read first

Each of these is reachable only through the `extra:` escape hatch, or not at all. Read the
linked page before promoting one to a first-class `model:` key.

Nothing here is about *reading* a response: taxman writes OpenAI's answer verbatim to `raw`
and derives nothing from it. Fields like `url_citation` and `incomplete_details` are already
in the collected data, waiting for whatever reads it.

| Gap | Documentation |
|---|---|
| **Batch mode** (`supports_batch = False`; the runner rejects `execution.batch`) | [Batch API guide](https://developers.openai.com/api/docs/guides/batch.md), [Batches — Create](https://developers.openai.com/api/reference/resources/batches/methods/create.md), [Batches — Retrieve](https://developers.openai.com/api/reference/resources/batches/methods/retrieve.md), [Files — Create](https://developers.openai.com/api/reference/resources/files/methods/create.md) |
| **Streaming** | [Streaming responses](https://developers.openai.com/api/docs/guides/streaming-responses.md), [Responses streaming events](https://developers.openai.com/api/reference/resources/responses/streaming-events.md) |
| **Structured outputs** (`text.format`) | [Structured model outputs](https://developers.openai.com/api/docs/guides/structured-outputs.md) |
| **Function calling, `tool_choice`, `parallel_tool_calls`** | [Function calling](https://developers.openai.com/api/docs/guides/function-calling.md) |
| **Web-search options** (`search_context_size`, `user_location`, filters) | [Web search](https://developers.openai.com/api/docs/guides/tools-web-search.md) |
| **`url_citation` annotations** (in `raw`; a reader's job, not taxman's) | [Web search](https://developers.openai.com/api/docs/guides/tools-web-search.md), [Citation formatting](https://developers.openai.com/api/docs/guides/citation-formatting.md) |
| **`incomplete_details.reason`** (in `raw`; a reader's job, not taxman's) | [Responses — Create](https://developers.openai.com/api/reference/resources/responses/methods/create.md) |
| **`reasoning.summary`, encrypted reasoning state** | [Reasoning models](https://developers.openai.com/api/docs/guides/reasoning.md) |
| **`service_tier`** — flex, priority, and the latency trade-offs | [Flex processing](https://developers.openai.com/api/docs/guides/flex-processing.md), [Fast mode](https://developers.openai.com/api/docs/guides/fast-mode.md), [Latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization.md) |
| **`prompt_cache_key`, `safety_identifier`** | [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching.md), [Production best practices](https://developers.openai.com/api/docs/guides/production-best-practices.md) |
| **Seed / reproducible outputs, `logprobs`** | [Advanced usage](https://developers.openai.com/api/docs/guides/advanced-usage.md) |
| **`background: true`** for long runs | [Background mode](https://developers.openai.com/api/docs/guides/background.md) |
| **Multi-turn input arrays, `previous_response_id`** | [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state.md) |
| **`Retry-After` / `x-ratelimit-*` headers** (ignored; core uses fixed backoff) | [Rate limits](https://developers.openai.com/api/docs/guides/rate-limits.md) |
| **`base_url`, `organization`, `project`** (not surfaced) | [Admin APIs](https://developers.openai.com/api/docs/guides/admin-apis.md) |

## Keeping the model list current

`models.py` hardcodes `KNOWN_MODELS`. Refresh it against
[All models](https://developers.openai.com/api/docs/models/all.md) and check
[Deprecations](https://developers.openai.com/api/docs/deprecations.md) and the
[API changelog](https://developers.openai.com/api/docs/changelog.md) before removing a name —
an audit YAML in the wild may still reference it, and unknown names are allowed on purpose.
