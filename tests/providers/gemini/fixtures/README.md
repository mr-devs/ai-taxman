# Recorded Gemini responses

Real `POST /v1beta/interactions` bodies, recorded on 2026-10-03 through `GeminiProvider.send`, so
each is exactly what taxman writes to `raw`: the JSON Google sent, not the SDK's parsed object.

`taxman` reads nothing out of a response, so no test asserts against these field by field.
They are kept because each one cost a real API call to produce, and because they show what an
Interactions response actually looks like. Any downstream tool that *does* parse this data has
to handle these shapes. Two things such a tool should know:
- With `store: false`, taxman's default, a response carries **no `id`**.
- An `incomplete` response may have **no `steps` key at all**.

| File | Model and settings | What it captures |
|---|---|---|
| `response_basic.json` | `gemini-3.1-flash-lite` | An ordinary answer: a `thought` step with a signature, then `model_output`. |
| `response_max_output_tokens.json` | `gemini-3.1-flash-lite`, `max_output_tokens: 4` | `status: incomplete` with no `steps`; thinking used the whole budget. |
| `response_thought_summary.json` | `gemini-3.8-flash`, `thinking_level: high`, `thinking_summaries: auto` | A `thought` step carrying a readable `summary`. |
| `response_google_search.json` | `gemini-3.1-flash-lite`, `search.web_search: true` | `google_search_call` and `google_search_result` steps, then an answer with citation annotations. |
| `response_search_types_function_call.json` | `gemini-3.1-flash-lite`, `google_search` tool with `search_types: [web_search]`, sent directly | Why taxman does not offer `search_types`: `status: requires_action` and a client-side `function_call` step. No search ran and no answer came back. |

Re-record against the [Interactions API reference](https://ai.google.dev/api/interactions.md.txt)
rather than editing by hand.
