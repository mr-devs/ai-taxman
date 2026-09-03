# Anthropic (Claude)

**Not yet implemented.** `anthropic>=0.40` is already declared as an optional extra in
`pyproject.toml`; the subpackage does not exist. Follow the checklist in
[CLAUDE.md](../../CLAUDE.md#adding-a-new-provider).

- **Endpoint:** Messages API, `POST /v1/messages`.
- **Key env:** `ANTHROPIC_API_KEY` (or `TAXMAN_ANTHROPIC_API_KEY`).
- **Index:** <https://platform.claude.com/llms.txt>
- **Markdown convention:** `platform.claude.com/docs/en/<path>` + `.md`.

Start here — the authoritative request and response schema:

> <https://platform.claude.com/docs/en/api/messages/create.md>

## Mapping the taxman feature set

Left column is the taxman feature, so this reads across against
[openai.md](openai.md). Anthropic's shape differs in three ways that matter for the adapter:
`system` is a **top-level parameter**, not part of the message array; `max_tokens` is
**required**; and thinking is configured as a token **budget** or an **effort** level rather
than OpenAI's single `effort` string.

| Taxman feature | Anthropic equivalent | Documentation |
|---|---|---|
| Send one message | `messages: [{role: "user", content: ...}]` | [Create a Message](https://platform.claude.com/docs/en/api/messages/create.md), [Messages](https://platform.claude.com/docs/en/api/messages.md) |
| `system_prompt` | top-level `system` | [Create a Message](https://platform.claude.com/docs/en/api/messages/create.md) |
| `max_output_tokens` | `max_tokens` — **required** | [Create a Message](https://platform.claude.com/docs/en/api/messages/create.md), [Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows.md) |
| `temperature`, `top_p` | same names | [Create a Message](https://platform.claude.com/docs/en/api/messages/create.md) |
| `reasoning_effort` | `thinking` budget, or the newer `effort` control | [Thinking overview](https://platform.claude.com/docs/en/build-with-claude/thinking.md), [Effort](https://platform.claude.com/docs/en/build-with-claude/effort.md), [Steering and cost control](https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost.md) |
| `web_search` | `tools: [{type: "web_search_20250305", ...}]` | [Web search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool.md) |
| `extract_text` | `content[]` blocks of type `text` (also `thinking`, `tool_use`) | [Create a Message](https://platform.claude.com/docs/en/api/messages/create.md) |
| Refusals / stop reason | `stop_reason` (`end_turn`, `max_tokens`, `refusal`, `stop_sequence`) | [Create a Message](https://platform.claude.com/docs/en/api/messages/create.md), [Handle streaming refusals](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/handle-streaming-refusals.md) |
| `extract_usage` | `usage.input_tokens` / `output_tokens` | [Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting.md), [Count tokens in a Message](https://platform.claude.com/docs/en/api/messages/count_tokens.md) |
| `cached_tokens` | `usage.cache_read_input_tokens`, `cache_creation_input_tokens` | [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md) |
| Retry semantics | `overloaded_error` (529) is distinctive and **must** be retried | [Errors](https://platform.claude.com/docs/en/api/errors.md), [Rate limits](https://platform.claude.com/docs/en/api/rate-limits.md) |
| `known_models()` | | [Models overview](https://platform.claude.com/docs/en/models/overview.md), [List Models](https://platform.claude.com/docs/en/api/models/list.md), [Choosing a model](https://platform.claude.com/docs/en/about-claude/models/choosing-a-model.md) |
| API key setup | `x-api-key` header | [Get your API key](https://platform.claude.com/docs/en/get-api-key.md), [Quickstart](https://platform.claude.com/docs/en/get-started.md) |
| SDK client | `anthropic.AsyncAnthropic` | [Python SDK](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python.md), [SDKs overview](https://platform.claude.com/docs/en/cli-sdks-libraries/overview.md) |

## Beyond parity

| Capability | Documentation |
|---|---|
| **Batch mode** — the natural first implementation of the `supports_batch` contract | [Batch processing](https://platform.claude.com/docs/en/build-with-claude/batch-processing.md), [Create a Message Batch](https://platform.claude.com/docs/en/api/messages/batches/create.md), [Retrieve results](https://platform.claude.com/docs/en/api/messages/batches/results.md) |
| Structured outputs | [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs.md) |
| Streaming | [Streaming Messages](https://platform.claude.com/docs/en/build-with-claude/streaming.md) |
| Service tiers / priority capacity | [Service tiers](https://platform.claude.com/docs/en/api/service-tiers.md) |
| Preserving thinking across turns | [Preserved thinking](https://platform.claude.com/docs/en/build-with-claude/preserved-thinking.md), [Thinking in tool workflows](https://platform.claude.com/docs/en/build-with-claude/thinking-tool-workflows.md) |
| Cost estimation for an audit run | [Pricing](https://platform.claude.com/docs/en/about-claude/pricing.md) |
| `anthropic-version` header pinning | [Versions](https://platform.claude.com/docs/en/api/versioning.md) |
| Model retirement dates | [Model deprecations](https://platform.claude.com/docs/en/about-claude/model-deprecations.md) |
| Reusing the OpenAI adapter shape | [OpenAI SDK compatibility](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk.md) — a compatibility layer, not full parity; prefer the native SDK |

## Gotchas to check against the docs, not memory

- `max_tokens` has no default. A missing value is a 400, so `config.py` should require it
  rather than omitting the key the way the OpenAI adapter does.
- Thinking imposes constraints on `temperature` and on the thinking budget relative to
  `max_tokens`. See [Troubleshooting thinking](https://platform.claude.com/docs/en/build-with-claude/thinking-troubleshooting.md).
- Prompt caching is opt-in per content block (`cache_control`), unlike OpenAI's automatic
  caching — so `cache_read_input_tokens` stays zero unless the adapter asks for it.
