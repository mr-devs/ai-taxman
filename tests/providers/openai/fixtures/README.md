# Recorded OpenAI responses

Real `POST /v1/responses` payloads, saved so the pure half of the provider can be
tested without a network or a key.

`taxman` no longer reads anything out of a response — it writes `raw` verbatim —
so nothing here is asserted against field by field any more. They are kept
because they are expensive to reproduce (each one cost a real API call) and
because they are the reference for what an OpenAI response actually looks like:
the shape any downstream tool that *does* parse this data has to handle.

| File | What it captures |
|---|---|
| `response_basic.json` | An ordinary text answer. |
| `response_refusal.json` | A `refusal` content part instead of `text`. |
| `response_incomplete.json` | `incomplete_details.reason: max_output_tokens`. |
| `response_web_search.json` | A web-search answer carrying `url_citation` annotations. |

Re-record against the [Responses create
reference](https://developers.openai.com/api/reference/resources/responses/methods/create.md)
rather than editing by hand.
