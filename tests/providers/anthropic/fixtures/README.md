# Recorded Anthropic responses

Real `POST /v1/messages` payloads, recorded on 2026-10-03 through `AnthropicProvider.send`, so
each is exactly what taxman writes to `raw`.

`taxman` reads nothing out of a response, so no test asserts against these field by field.
They are kept because each one cost a real API call to produce, and because they show what a
Claude response actually looks like. Any downstream tool that *does* parse this data has to
handle these shapes.

| File | Model and settings | What it captures |
|---|---|---|
| `response_basic.json` | `claude-haiku-4-5-20251001` | An ordinary text answer. |
| `response_max_tokens.json` | `claude-haiku-4-5-20251001`, `max_tokens: 4` | `stop_reason: max_tokens`, with the answer cut off. |
| `response_adaptive_skipped_thinking.json` | `claude-sonnet-5-5`, `effort: high`, `thinking: {type: adaptive, display: summarized}` | Adaptive thinking that chose not to think: no `thinking` block, and `thinking_tokens: 0`. |
| `response_web_search.json` | `claude-haiku-4-5-20251001`, web search with `allowed_callers: [direct]`, `max_uses: 1` | `server_tool_use` and `web_search_tool_result` blocks, then text carrying citations. |

Re-record against the [Create a Message
reference](https://platform.claude.com/docs/en/api/messages/create.md) rather than editing by
hand.
