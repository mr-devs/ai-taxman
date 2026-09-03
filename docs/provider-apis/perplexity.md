# Perplexity

**Not yet implemented.**

Perplexity is the odd one out, and worth understanding before writing any code: its models are
**web-grounded by default**. A response carries search results and citations whether or not
you asked for a search tool, which makes it a genuinely different object to audit than a
closed-book completion — and makes `search_results` / citations something the adapter's
`extract()` should probably surface rather than leave buried in `raw`.

- **Endpoints:** the **Agent API** (`POST https://api.perplexity.ai/agent`) is the current
  primary surface. A **Router** endpoint additionally speaks OpenAI Chat Completions, OpenAI
  Responses, and Anthropic Messages schemas against the same key. The older **Sonar** chat
  completions endpoint still exists and is in migration.
- **Key env:** `PPLX_API_KEY` (or `TAXMAN_PPLX_API_KEY`).
- **Index:** <https://docs.perplexity.ai/llms.txt>
- **Markdown convention:** `docs.perplexity.ai/docs/<path>` + `.md`; API reference pages live
  at `docs.perplexity.ai/api-reference/<slug>.md`.

Start here — the request and response schema:

> <https://docs.perplexity.ai/api-reference/agent-post.md>

## Which surface to build against

| Surface | Reference | Why you would pick it |
|---|---|---|
| Agent API | [Create Agent Response](https://docs.perplexity.ai/api-reference/agent-post.md), [Quickstart](https://docs.perplexity.ai/docs/agent-api/quickstart.md) | Current, richest, native search configuration |
| Router — OpenAI Chat Completions | [Create Chat Completion](https://docs.perplexity.ai/api-reference/gateway-chat-completions-post.md) | Reuses OpenAI-shaped code |
| Router — OpenAI Responses | [Create Response](https://docs.perplexity.ai/api-reference/gateway-responses-post.md) | Closest to taxman's existing OpenAI adapter |
| Sonar (legacy) | [Create Chat Completion](https://docs.perplexity.ai/api-reference/sonar-post.md), [Migration guide](https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/how-to.md) | Existing integrations only |

## Mapping the taxman feature set

| Taxman feature | Perplexity equivalent | Documentation |
|---|---|---|
| Send one message | `input` (Agent API) or `messages[]` (Router) | [Create Agent Response](https://docs.perplexity.ai/api-reference/agent-post.md), [Quickstart](https://docs.perplexity.ai/docs/agent-api/quickstart.md) |
| `system_prompt` | `instructions`, kept separate from the question | [Prompt the agent](https://docs.perplexity.ai/docs/agent-api/building-agents/prompt-the-agent.md) |
| `max_output_tokens` | token budgets | [Create Agent Response](https://docs.perplexity.ai/api-reference/agent-post.md) |
| `temperature`, `top_p` | Router surfaces only | [Create Chat Completion](https://docs.perplexity.ai/api-reference/gateway-chat-completions-post.md) |
| `reasoning_effort` | reasoning control / preset | [Presets](https://docs.perplexity.ai/docs/agent-api/presets.md), [Agent API Models](https://docs.perplexity.ai/docs/agent-api/models.md) |
| `web_search` | **on by default**; configured, not enabled | [Web Search](https://docs.perplexity.ai/docs/agent-api/tools/web-search.md), [Tools overview](https://docs.perplexity.ai/docs/agent-api/tools/overview.md) |
| `extract_text` | the output content blocks | [Create Agent Response](https://docs.perplexity.ai/api-reference/agent-post.md) |
| **Citations / search results** — no OpenAI analogue | `search_results`, citations | [Web Search](https://docs.perplexity.ai/docs/agent-api/tools/web-search.md), [Domain filtering](https://docs.perplexity.ai/docs/search/filters/domain-filter.md), [Date filters](https://docs.perplexity.ai/docs/search/filters/date-time-filters.md) |
| `extract_usage` | `usage`, incl. per-request search fees | [Pricing](https://docs.perplexity.ai/docs/getting-started/pricing.md) |
| Retry semantics | 429 and 5xx | [Error Handling](https://docs.perplexity.ai/docs/sdk/error-handling.md), [Rate Limits & Usage Tiers](https://docs.perplexity.ai/docs/admin/rate-limits-usage-tiers.md) |
| `known_models()` | | [Agent API Models](https://docs.perplexity.ai/docs/agent-api/models.md), [Router Models & Pricing](https://docs.perplexity.ai/docs/router/models.md), [List Models](https://docs.perplexity.ai/api-reference/models-get.md) |
| API key setup | `Authorization: Bearer` | [Quickstart](https://docs.perplexity.ai/docs/getting-started/quickstart.md), [API Key Management](https://docs.perplexity.ai/docs/admin/api-key-management.md) |
| SDK client | official Python SDK | [SDK overview](https://docs.perplexity.ai/docs/sdk/overview.md), [Performance](https://docs.perplexity.ai/docs/sdk/performance.md) |

## Beyond parity

| Capability | Documentation |
|---|---|
| Async / background runs — the closest thing to a batch mode | [Background Mode](https://docs.perplexity.ai/docs/agent-api/background-mode.md), [Create Async Chat Completion](https://docs.perplexity.ai/api-reference/async-sonar-post.md) |
| Structured outputs and streaming | [Output Control](https://docs.perplexity.ai/docs/agent-api/output-control.md) |
| Search API on its own, without a model | [Search quickstart](https://docs.perplexity.ai/docs/search/quickstart.md), [Search the Web](https://docs.perplexity.ai/api-reference/search-post.md) |
| Cost estimation for an audit run | [Pricing](https://docs.perplexity.ai/docs/getting-started/pricing.md), [Projects & Billing](https://docs.perplexity.ai/docs/getting-started/projects.md) |
| What changed recently | [Changelog](https://docs.perplexity.ai/docs/resources/changelog.md) |

## Gotchas to check against the docs, not memory

- **Reproducibility is weaker here than anywhere else.** Responses depend on live web state,
  so two identical audit runs will differ for reasons that have nothing to do with the model.
  The `manifest.json` should record the search configuration, and `extract()` should keep the
  search results, or the audit is not interpretable after the fact.
- Billing is per-request plus per-search, not purely per-token. See
  [Pricing](https://docs.perplexity.ai/docs/getting-started/pricing.md) before estimating the
  cost of a run with `execution.repeats`.
- The Agent API and Sonar have different parameter names for the same idea. The
  [migration guide](https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/how-to.md)
  has the mapping table — read it rather than assuming a name carried over.
