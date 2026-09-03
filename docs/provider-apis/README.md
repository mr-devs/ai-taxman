# Provider API documentation

One file per provider, mapping **taxman features to canonical API documentation**. The point
is that nobody — human or model — has to recall what a parameter is called, which usage key
holds cached tokens, or whether a 409 is retryable. Look it up.

Read [CLAUDE.md](../../CLAUDE.md#provider-api-documentation--do-not-guess) for when consulting
these is mandatory.

| Provider | File | Wire protocol | Default key env |
|---|---|---|---|
| OpenAI | [openai.md](openai.md) | Responses API (`POST /v1/responses`) | `OPENAI_API_KEY` |
| Anthropic | [anthropic.md](anthropic.md) | Messages API (`POST /v1/messages`) | `ANTHROPIC_API_KEY` |
| Google Gemini | [gemini.md](gemini.md) | `generateContent` / Interactions API | `GEMINI_API_KEY` |
| xAI (Grok) | [xai.md](xai.md) | OpenAI-compatible `POST /v1/chat/completions` | `XAI_API_KEY` |
| Perplexity | [perplexity.md](perplexity.md) | Agent API, plus OpenAI-compatible Router | `PPLX_API_KEY` |

## Fetch the markdown, not the page

Every provider here publishes a machine-readable twin of its docs. Fetch that. It is smaller,
it has no navigation chrome, and it is what the provider intends a model to read.

| Provider | How to build the markdown URL |
|---|---|
| OpenAI | `developers.openai.com/api/<path>` **+ `.md`** |
| Anthropic | `platform.claude.com/docs/en/<path>` **+ `.md`** |
| Google Gemini | `ai.google.dev/gemini-api/docs/<path>` **+ `.md.txt`** |
| xAI (Grok) | `docs.x.ai/developers/<path>` **+ `.md`** |
| Perplexity | `docs.perplexity.ai/docs/<path>` **+ `.md`** |

### Two traps

Both answer **HTTP 200 while serving HTML**, so a status code is not proof of anything:

- `platform.openai.com/docs/api-reference/responses.md` — a rendered page. OpenAI's markdown
  lives on a different host: `developers.openai.com`.
- `ai.google.dev/api/generate-content.md` — a rendered page. Google's suffix is `.md.txt`,
  not `.md`. (`ai.google.dev/api/generate-content.md.txt` is the real thing, and it is large.)

The check that actually works: the body must start as markdown, not `<!doctype` or `<html>`.
`tests/test_provider_docs.py` enforces exactly that against every URL in this directory.

## Finding a page that is not listed here

Fetch the provider's index rather than searching the web. Each one lists every documentation
page together with its markdown twin:

- <https://developers.openai.com/api/llms.txt> — routes to
  [`api/docs/llms.txt`](https://developers.openai.com/api/docs/llms.txt) (guides) and
  [`api/reference/llms.txt`](https://developers.openai.com/api/reference/llms.txt) (endpoints)
- <https://platform.claude.com/llms.txt>
- <https://ai.google.dev/gemini-api/docs/llms.txt>
- <https://docs.x.ai/llms.txt>
- <https://docs.perplexity.ai/llms.txt>

Single-file exports (`llms-full.txt`) exist for OpenAI, and xAI's `llms.txt` is itself one
1.5 MB concatenation of every page delimited by `===/path===` markers. Prefer a per-page URL;
reach for the full export only when you genuinely need to search across the whole corpus.

## When a markdown twin does not exist

As of the last check, **all five providers serve markdown** — nothing here needs a fallback.
If that changes, record the human-facing URL and mark the row `HTML only`. Read those pages
with the Claude-in-Chrome tools (`mcp__claude-in-chrome__*`), not WebFetch: these docs sites
render client-side, so a plain fetch returns an empty shell.

## Keeping this current

```
uv run pytest -m live -k provider_docs
```

Deselected by default, because `uv run pytest` must pass offline. A failure is a moved page,
not a flaky test — find the new slug in the provider's `llms.txt` and update the link.
