# Anthropic (Claude)

Implemented in [`src/ai_taxman/providers/anthropic/`](../../src/ai_taxman/providers/anthropic/).

- **Endpoint:** Messages API, `POST /v1/messages`, via `AsyncAnthropic().messages.create()`.
- **Key env:** whatever the audit's `api_key_env:` names. `ANTHROPIC_API_KEY` is Anthropic's
  own convention, and `taxman audits new` writes it into a comment as a hint; taxman reads no
  other name. Passing the key explicitly also stops the SDK reading `ANTHROPIC_AUTH_TOKEN`.
- **Index:** <https://platform.claude.com/llms.txt>
- **Markdown convention:** `platform.claude.com/docs/en/<path>` + `.md`.

The authoritative list of every request parameter and response field is the *create*
reference. Read it before adding any key to the `model:` block:

> <https://platform.claude.com/docs/en/api/messages/create.md>

Anthropic's request differs from OpenAI's in four ways that matter to the adapter:
- `system` is a **top-level parameter**, not a turn in `messages`.
- `max_tokens` is **required**.
- Reasoning is steered by `output_config.effort` and a separate `thinking` object.
- Current models **reject** non-default sampling parameters.

## What taxman implements today

| Feature | Where in the code | Documentation |
|---|---|---|
| `messages.create` call | `provider.py` `send()` | [Create a Message](https://platform.claude.com/docs/en/api/messages/create.md) |
| The message, as one user turn in `messages` | `provider.py` `build_request()` | [Create a Message](https://platform.claude.com/docs/en/api/messages/create.md) |
| `model:` `name` | `config.py`, `models.py` | [Models overview](https://platform.claude.com/docs/en/models/overview.md) |
| `system_prompt` (core's, `Request.system_prompt`) → top-level `system` | `provider.py` | [Create a Message](https://platform.claude.com/docs/en/api/messages/create.md) |
| `max_tokens`, required; the template writes 16000 | `config.py` | [Create a Message](https://platform.claude.com/docs/en/api/messages/create.md) |
| `effort` → `output_config.effort` | `config.py`, `provider.py` | [Effort](https://platform.claude.com/docs/en/build-with-claude/effort.md) |
| `thinking:` `type`, `budget_tokens`, `display` → `thinking`; `display: updates` (beta) also sends `anthropic-beta: thinking-display-updates-2026-08-18` | `config.py`, `provider.py` `request_headers()` | [Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking.md) |
| `temperature`, `top_p`, `top_k` (models after Claude Opus 4.6 refuse non-default values) | `config.py` | [Create a Message](https://platform.claude.com/docs/en/api/messages/create.md) |
| `search.web_search` → `tools: [{type: <tool_version>, name: web_search}]`, newest version by default | `provider.py` `_web_search_tool()` | [Web search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool.md) |
| `search.max_uses`, `allowed_domains`, `blocked_domains`, `allowed_callers`, `response_inclusion` → the same names on the tool | `config.py`, `provider.py` | [Web search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool.md), [Server tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/server-tools.md) |
| `search.user_location` → the tool's `user_location`, with `type: approximate` | `provider.py` `_web_search_tool()` | [Web search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool.md) |
| `search.tool_choice` → request-level `tool_choice: {type: auto \| any}` | `provider.py` `build_request()` | [Create a Message](https://platform.claude.com/docs/en/api/messages/create.md) |
| `extra:`, merged into the request at any depth, never over a key path in `SET_BY_TAXMAN`; keys the SDK does not name go in `extra_body` | `config.py`, `provider.py` | [Python SDK](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python.md) |
| `raw` = `Message.to_dict(mode="json")`: only the fields Anthropic sent, under the API's names | `provider.py` `send()` | [Python SDK](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python.md) |
| Retry set: `RateLimitError`, `OverloadedError` (529), `InternalServerError`, `ServiceUnavailableError`, `DeadlineExceededError`, `ConflictError`, `APIConnectionError`, `APITimeoutError` | `provider.py` `RETRYABLE_ERRORS` | [Errors](https://platform.claude.com/docs/en/api/errors.md) |
| Client construction, `max_retries=0`, explicit `timeout=` per call | `provider.py` `_new_client()`, `send()` | [Python SDK](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python.md) |

`OverloadedError`, `ServiceUnavailableError` and `DeadlineExceededError` are siblings of
`InternalServerError` in SDK v1, not subclasses, so each is listed. A test fails if the SDK
grows another 429 or 5xx class that the set misses.

The explicit timeout matters for another reason. Without one, the SDK refuses a
non-streaming request whose `max_tokens` it estimates would take more than 10 minutes. With
one, the audit's `execution.timeout_s` is the only limit, so raise it alongside a large
`max_tokens`.

Validation checks names and the rules Anthropic documents for every model. It does not
check which model accepts which `effort` or `thinking.type`, because that differs from model
to model. The API answers with a 400, and the row records it.

## Known gaps, with the spec to read first

Nothing here is about *reading* a response. taxman writes Anthropic's answer verbatim to
`raw` and derives nothing from it.

| Gap | Documentation |
|---|---|
| **Batch mode** (`supports_batch = False`) | [Batch processing](https://platform.claude.com/docs/en/build-with-claude/batch-processing.md), [Create a Message Batch](https://platform.claude.com/docs/en/api/messages/batches/create.md) |
| **`pause_turn`**: a long server-tool turn can pause. The paused message is recorded as-is; taxman does not continue it | [Web search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool.md), [Server tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/server-tools.md) |
| **Streaming** (`stream` is refused in `extra:`: a stream is not a response) | [Streaming Messages](https://platform.claude.com/docs/en/build-with-claude/streaming.md) |
| **Structured outputs**: reachable through `extra: {output_config: {format: ...}}` | [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs.md) |
| **Service tiers, `inference_geo`**: reachable through `extra:` | [Service tiers](https://platform.claude.com/docs/en/api/service-tiers.md) |
| **Prompt caching**: opt-in per content block, so `cache_read_input_tokens` stays zero | [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md) |
| **`retry-after` header** (ignored; core uses a fixed backoff) | [Rate limits](https://platform.claude.com/docs/en/api/rate-limits.md) |
| **`ANTHROPIC_BASE_URL`**: read by the SDK and not pinned, as with OpenAI's base URL | [Python SDK](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python.md) |
| **Thinking troubleshooting**: per-model `type` and `effort` rules | [Troubleshooting thinking](https://platform.claude.com/docs/en/build-with-claude/thinking-troubleshooting.md) |

## Keeping the model list current

`models.py` hardcodes `KNOWN_MODELS`, as pinned snapshot IDs. Refresh it against the
[Models overview](https://platform.claude.com/docs/en/models/overview.md), and check
[Model deprecations](https://platform.claude.com/docs/en/about-claude/model-deprecations.md)
before removing a name. An audit YAML in the wild may still reference it, and unknown names
are allowed on purpose.
