# Google Gemini

Implemented in [`src/ai_taxman/providers/gemini/`](../../src/ai_taxman/providers/gemini/).

- **Endpoint:** Interactions API, `POST /v1beta/interactions`, via
  `genai.Client().aio.interactions.with_raw_response.create()` (`google-genai`). Google
  recommends this API for new work. `generateContent` is still supported, but Google labels
  its docs legacy.
- **Key env:** whatever the audit's `api_key_env:` names. `GEMINI_API_KEY` is Google's own
  convention, and `taxman audits new` writes it into a comment as a hint; taxman reads no
  other name. An explicit key also wins over `GOOGLE_API_KEY`, which the SDK would otherwise
  prefer.
- **Index:** <https://ai.google.dev/gemini-api/docs/llms.txt>
- **Markdown convention:** append **`.md.txt`**, not `.md`. `ai.google.dev/<path>.md` returns
  a rendered HTML page with a 200 status.

The authoritative request and response schema is the REST reference. It is large, about
200 KB; the guides below are smaller if you only need one parameter:

> <https://ai.google.dev/api/interactions.md.txt> (v1beta, what the SDK calls)
> and <https://ai.google.dev/api/interactions-api-v1.md.txt> (the stable v1)

## What taxman implements today

| Feature | Where in the code | Documentation |
|---|---|---|
| `interactions.create` call, through the SDK's raw-response wrapper | `provider.py` `send()` | [Interactions API](https://ai.google.dev/gemini-api/docs/interactions-overview.md.txt), [Interactions reference](https://ai.google.dev/api/interactions.md.txt) |
| `input` (the message text) | `provider.py` `build_request()` | [Text generation](https://ai.google.dev/gemini-api/docs/text-generation.md.txt) |
| `model:` `name` | `config.py`, `models.py` | [Models](https://ai.google.dev/gemini-api/docs/models.md.txt) |
| `system_prompt` (core's, `Request.system_prompt`) → `system_instruction` | `provider.py` | [Text generation](https://ai.google.dev/gemini-api/docs/text-generation.md.txt) |
| `temperature`, `top_p`, `max_output_tokens` → `generation_config` | `config.py` | [Text generation](https://ai.google.dev/gemini-api/docs/text-generation.md.txt) |
| `thinking_level`, `thinking_summaries` → `generation_config` | `config.py`, `models.py` | [Gemini thinking](https://ai.google.dev/gemini-api/docs/thinking.md.txt) |
| `store` (always sent, defaults false) | `config.py` | [Interactions API](https://ai.google.dev/gemini-api/docs/interactions-overview.md.txt), [Logs and datasets](https://ai.google.dev/gemini-api/docs/logs-datasets.md.txt) |
| `search.web_search` → `tools: [{type: google_search}]` | `provider.py` `build_request()` | [Grounding with Google Search](https://ai.google.dev/gemini-api/docs/google-search.md.txt) |
| `extra:`, merged into the body at any depth (`generation_config.seed` included), never over a key path in `SET_BY_TAXMAN`; `stream` and `background` refused | `config.py`, `provider.py` | [Interactions reference](https://ai.google.dev/api/interactions.md.txt) |
| `raw` = the response's JSON body, not the SDK's parsed object | `provider.py` `send()` | [Gemini API libraries](https://ai.google.dev/gemini-api/docs/libraries.md.txt) |
| Retry set: `RateLimitError`, `InternalServerError` (every 5xx), `ConflictError`, `APIConnectionError`, `APITimeoutError` | `provider.py` `RETRYABLE_ERRORS` | [API errors](https://ai.google.dev/gemini-api/docs/api-errors.md.txt), [Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits.md.txt) |
| Client construction: explicit key, `enterprise=False`, SDK retries off, explicit `timeout=` per call | `provider.py` `_new_client()`, `send()` | [Gemini API libraries](https://ai.google.dev/gemini-api/docs/libraries.md.txt), [Using Gemini API keys](https://ai.google.dev/gemini-api/docs/api-key.md.txt) |

## How the SDK is used, and why

These were all checked against `google-genai` 2.28 on 2026-10-03, by tests that run against a
fake transport. Re-check them on an SDK upgrade; the tests fail if any of them changes.

- **The Interactions client is a separate generated SDK** (`google.genai._gaos`), with its own
  retries and errors. Its errors are Stainless-style classes in
  `google.genai._gaos.lib.compat_errors`.
- **Its retries are on by default** (408, 409, 429 and 5xx), **and no `HttpRetryOptions` value
  turns them off.** The parent client raises `attempts=0` to 1, and the Interactions client
  reads `attempts` as a *retry* count, so every failure would cost two requests.
  `_new_client()` sets the Interactions client's own `retry_config` to `None` instead. That
  is what makes a failure exactly one HTTP request, so a record's `attempts` stays true.
- **Typed keywords silently drop unknown fields.** Passed as keywords to `create()`, an
  unknown key inside `generation_config` is dropped without an error. So `send()` passes
  only `model` and `input` as keywords, and everything else as `extra_body`, which reaches
  the wire unchanged.
- **`raw` comes from `with_raw_response`.** The parsed object adds conveniences Google never
  sent (`output_text`), so `send()` records the response's own JSON instead.
- **`enterprise=False`** keeps the audit on the Gemini Developer API whatever
  `GOOGLE_GENAI_USE_ENTERPRISE` or `GOOGLE_GENAI_USE_VERTEXAI` say.

## Search settings taxman does not offer

Two settings Google documents for search stopped the search when tested live on 2026-10-03,
with both `gemini-3.1-flash-lite` and `gemini-3.8-flash`. With either one, the response was a
client-side `function_call` for `google_search` instead of a search. No search ran and no
answer came back.

| Setting | What happened |
|---|---|
| `search_types: [web_search]` on the `google_search` tool | `status: requires_action` with a `function_call` step, every time. Recorded in `tests/providers/gemini/fixtures/response_search_types_function_call.json`. |
| `generation_config.tool_choice: any` | A `function_call` step, or a 400: "Model generated too many tool calls". |

`tool_choice` can still be sent through `extra:`. `search_types` cannot, because taxman builds
`tools` itself. Re-test both before offering them.

The Interactions `google_search` tool has no domain filters and no location setting at all.

## Known gaps, with the spec to read first

Nothing here is about *reading* a response. taxman writes Google's answer verbatim to `raw`
and derives nothing from it. With `store: false`, the response carries no `id`, and an
`incomplete` one may carry no `steps`.

| Gap | Documentation |
|---|---|
| **Safety settings**: the Interactions overview says custom safety settings are not supported. The beta reference lists `safety_settings`, so it can be tried through `extra:` | [Interactions API](https://ai.google.dev/gemini-api/docs/interactions-overview.md.txt) |
| **Batch mode**: the Batch API serves `generateContent` only | [Batch API](https://ai.google.dev/gemini-api/docs/batch-api.md.txt) |
| **`thinking_budget`** (Gemini 2.5): the Interactions API has only `thinking_level`, so 2.5 models get their default thinking | [Gemini thinking](https://ai.google.dev/gemini-api/docs/thinking.md.txt) |
| **Streaming and background execution**: refused in `extra:`, since neither returns a finished response | [Streaming](https://ai.google.dev/gemini-api/docs/streaming.md.txt), [Background execution](https://ai.google.dev/gemini-api/docs/background-execution.md.txt) |
| **Service tiers**: reachable through `extra: {service_tier: flex}` | [Flex inference](https://ai.google.dev/gemini-api/docs/flex-inference.md.txt) |
| **`Retry-After`** (ignored; core uses a fixed backoff) | [Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits.md.txt) |
| **`GOOGLE_GEMINI_BASE_URL`**: read by the SDK and not pinned, as with the other providers' base URLs | [Gemini API libraries](https://ai.google.dev/gemini-api/docs/libraries.md.txt) |
| **Schema changes**: the May 2026 `steps` schema replaced `outputs`. Responses recorded before the change have the old shape | [Breaking changes, May 2026](https://ai.google.dev/gemini-api/docs/interactions-breaking-changes-may-2026.md.txt), [Migrating to the Interactions API](https://ai.google.dev/gemini-api/docs/migrate-to-interactions.md.txt) |
| **Free-tier logging**: free-tier prompts may be used for product improvement, which bears on an audit's claims about where data went | [Pricing](https://ai.google.dev/gemini-api/docs/pricing.md.txt) |

## Keeping the model list current

`models.py` hardcodes `KNOWN_MODELS`, limited to the models the Interactions overview lists.
Refresh it against [Models](https://ai.google.dev/gemini-api/docs/models.md.txt) and the
[Release notes](https://ai.google.dev/gemini-api/docs/changelog.md.txt) before removing a
name. An audit YAML in the wild may still reference it, and unknown names are allowed on
purpose.
