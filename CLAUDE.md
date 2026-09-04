# CLAUDE.md — ai-taxman

## What this project is

`ai-taxman` is a CLI (`taxman`) for auditing AI/LLM providers, also usable as a plain Python
package. The CLI is the priority; the Python API is a thin, honest wrapper over the same code.

The one canonical workflow, and the thing every design decision must keep simple:

```
1. The user writes a plain .txt file of messages, one per line, however they like.
2. taxman init openai <audit-name>            ->  scaffolds audits/<name>.yaml
3. The user fills in the blanks.
4. taxman collect <audit-name>                ->  data/<audit>/<run_id>/responses.jsonl
```

An **audit** is one YAML file. Its `audit:` key is the name `taxman collect` matches on.

`taxman init` takes a provider and an audit name, and **nothing else**. Every setting is
written at its default with a comment explaining it, and the user edits the file. Do not
add flags or `key=value` arguments back: routing settings to their owner on the command
line meant core had to know which keys were the provider's, which is the seam this project
exists to keep clean. Audits are always written to `./audits/` in the working directory.

## uv only — never pip

This project installs, runs, and builds with **uv**. Never write `pip install`,
`uv pip`, or `python -m pip` anywhere: not in the README, not in docs, not in CI, not in
comments, and **not in error messages shown to users**. There are no exceptions, and no
"just this once for a user who might not have uv".

Use `uv add` / `uv sync --all-extras` for dependencies, `uv run <cmd>` to run anything,
and `uv tool install` for a standalone command. `tests/test_conventions.py` fails the
suite if `pip` reappears.

## Terminology (strict)

The unit of input is a **message**. Never "query", never "prompt" — not in code, config keys, CLI
help, docstrings, docs, or test names. Use `Message`, `message_id`, `read_messages()`, `messages:`.

Provider APIs call their chat-turn arrays `messages` too. Keep that sense **inside the provider
adapter** and name the variable `request_messages` so the two never blur.

## TDD is mandatory

No implementation without a failing test first. For every unit of behaviour:

1. Write the test. Run it. Watch it fail for the right reason.
2. Write the minimum code to pass.
3. Refactor with the test green.

Do not write a module and backfill tests. If you are about to, stop and write the test.

**No network in the default test run.** Real API calls go behind `@pytest.mark.live`, which is
deselected by default via `addopts`. `uv run pytest` must pass offline, with no API keys set.

## Provider isolation (the core architectural rule)

Each provider is a self-contained subpackage under `src/ai_taxman/providers/<name>/`. Adding,
changing, or breaking one provider must not touch another.

- `core/` and `cli/` **never** import `providers.<name>` directly. They go through
  `core/registry.py`, which lazy-imports by name and reads the module-level `PROVIDER`.
- No `if provider == "openai"` anywhere outside that provider's own directory.
- One provider never imports another.
- Provider SDKs are **optional extras** (`ai-taxman[openai]`). A missing SDK must produce a clear
  "`uv add ai-taxman[openai]`" message, never a raw ImportError traceback.
- All shared behaviour lives in `providers/base.py` (the contract) or the conformance test suite.

### Pure / IO split

Inside each provider, separate:

- **pure**: `build_request(...)` — no I/O, unit-tested against checked-in JSON fixtures in
  `tests/providers/<name>/fixtures/`.
- **thin I/O**: `send(...)` — the only function that touches the network, kept as small as possible
  and not exercised in the default test run.

## First-run experience

`uv tool install` must be the only thing a user has to do. Anything else — `PATH`, shell
completion — is taxman's job to detect and offer to fix, never the user's job to look up.

`core/environment.py` detects; `cli/doctor_cmd.py` reports and fixes. `taxman doctor` is
the only place those checks run — no other command may fold them in silently.

Rules for that code:

- **Never edit a shell startup file without an explicit yes.** A "no, don't ask again" is
  remembered in `~/.taxman/state.yaml`.
- **Shell out to `uv tool update-shell` for `PATH`** — uv owns that file, not us.
- **Only warn about `PATH` for a `uv tool` install.** A `uv run` or `uvx` binary is
  deliberately not on `PATH`; warning there is noise.
- **Detection never raises.** A machine we cannot read is reported as unknown, and we stay
  quiet rather than guess.

## API keys

Every audit names exactly **one** environment variable in `api_key_env:`, and that is the
only name taxman looks up. There is no fallback chain of variable names. Missing field, or
a name that resolves to nothing → hard error, before anything is sent.

The variable is satisfied **one** way: the user exports it. taxman never stores a key, so
there is no file to protect, no copy to go stale, and no precedence rule to explain.
`core/credentials.resolve_api_key()` is one `os.environ` lookup and must stay that way —
do not add a `.env` reader, a keyring, or a config file back.

`taxman init` writes `api_key_env: <insert_api_key_env_var_here>` and never guesses a
variable name. A provider's `default_api_key_env` is rendered into a *comment* as a hint;
core must never resolve a key from it. Guessing is how an audit ends up billing a key it
never named.

Core owns "read the variable this audit names". Providers own "use this key". A provider
never reads the environment itself, and never sees a variable name.

## There is no configuration command

`taxman init` writes a fully commented audit file and the user edits it. That is the only
way any setting is ever chosen — the `api_key_env:` variable name included.

There was a `taxman setup` that interviewed the user for provider defaults and persisted
them to `~/.taxman/providers/<name>.yaml`. It is gone, and so is `set-key`. **Do not add
either back**, in any form: a second channel for setting audit values means core has to
route each one to its owner, providers have to validate arbitrary keys, and the rendered
template has to round-trip saved values through YAML. All of that existed and all of it
was deleted. If a setting is hard to discover, fix its comment in the template.

The only interactive prompt left in the CLI is `doctor`'s yes/no, which lives in
`cli/doctor_cmd.py` and asks about the machine, never about an audit.

## Config contract

Top-level YAML blocks (`audit`, `provider`, `messages`, `output`, `execution`) are **core-owned** and
identical across providers. The `model:` block is **provider-owned** and opaque to core — core hands
it to `provider.validate_model_config()` and never inspects its keys. That split *is* the isolation
boundary; do not leak provider-specific keys upward.

**Core must never read a key out of the `model:` block** — not `name`, not anything. It
looks like a shortcut and it breaks the moment a provider names things differently. To get
the model name for a record, call `provider.describe_model(validated)`. To resolve the
block, call `provider.validate_model_config(block)`. `tests/test_conventions.py` fails the
suite if `core/` or `cli/` reads a known provider key, imports `providers.<name>`, or
hardcodes a provider's name.

## Collection never parses a response

`taxman collect` sends messages and writes what came back. It does **not** extract, clean,
summarise, or derive anything from a provider's response. There was a `Provider.extract()`
producing `text` and `usage` convenience fields; it is gone, and
`tests/test_collection_only.py` fails the suite if it or anything like it reappears —
including a provider defining its own `extract_*` function.

Parsing is a separate tool's job, working from `raw` on disk. Keeping the two apart means a
change to how responses are read can never alter what was collected, and a re-read of an old
run always gives the same answer as a fresh one.

`response.model_dump(mode="json")` in `send()` is serialisation, not extraction: it is what
makes the SDK's object writable at all.

## Record schema is stable

`core/records.py` defines the JSONL row, shared by all providers. `raw` holds the provider
response verbatim, and is the only response data in the row.

Changes are **additive** — auditors depend on old data staying readable. Removing or renaming a
field is a breaking change: it needs a `RESPONSE_SCHEMA_VERSION` bump and a row in the README's
"Schema versions" table. That has happened once, for v2 (`text` and `usage` removed).

Every run also writes `manifest.json` beside the JSONL: resolved config, tool version, message-file
hash, counts, timings, and `status`. Reproducibility is the point of an audit tool.

It is written **before the first request** and rewritten when the run ends, never only at the
end. A run killed halfway through still says what it was running, with which settings, and how
many responses it expected — without that denominator a run cut off at 40% is indistinguishable
from a complete run over a shorter message file. `status` is `running` until the run ends, then
`complete`, `stopped_early`, `interrupted`, or `failed`.

One directory holds exactly one run. `output.dir` is the user's to set, and dropping `{run_id}`
from it would append two runs into one file under a manifest describing only the later one, so
the runner refuses a directory that already claims a different `run_id`. The same id is allowed
— that is what appending to a named run means.

Everything on disk is written as it is produced. Responses are flushed per row, so an audit that
is killed keeps every response already paid for, and `read_jsonl` stops at a truncated tail
rather than raising — including a gzip stream with no end-of-stream marker.

## Logging and background runs

`core/logging.py` owns the format and `setup_logging()`; **core modules only ever ask for a
logger and emit**. Handlers are attached by `cli/collect_cmd.py`, never by the library, and a
`NullHandler` on the package logger keeps the Python API silent. Log records go to stderr so
stdout stays the command's own; `--log-file` redirects them to a path instead.

**The API key is never logged**, at any level, and neither is message text — ids and counts
only. `tests/core/test_logging.py` asserts both.

`collect --background` re-runs taxman as a detached child (`cli/background.py`): the parent
validates the audit, picks the `run_id`, spawns `python -m ai_taxman collect ... --run-id ...`
in a new session, and exits. Three rules:

- **The parent validates first.** A pid handed back for a run that could never work is worse
  than an error at the prompt.
- **The parent picks the run id**, because otherwise nothing could name the directory or the
  log before the child starts. That is what `--run-id` is for; it also makes appending to a
  named run possible, which the collision guard deliberately allows. A user-supplied id goes
  through `records.validate_run_id()` first — it is interpolated into `output.dir` and then
  resolved as a path, so `..` or `/` in one would move the data elsewhere on disk while every
  record still claimed the id. The runner validates too, so the Python API is held to the same
  rule.
- **stdout is the pid and nothing else**, like `docker run -d`. The human block goes to
  stderr. Do not add fields to stdout — `PID=$(taxman collect probe -b)` is the whole point.

SIGTERM is a *graceful* stop: `run_audit_async(stop_signals=...)` sets the runner's stop flag,
so in-flight requests finish and the manifest is finalised. The default is no signal handling,
because a library does not take its caller's handlers.

## Repeat semantics

`execution.repeats: N` means each message is sent N times. The runner expands the audit into a flat
list of `(message_id, repeat_index)` pairs — the full cartesian product — and feeds *that* to one
concurrency-limited pool. Repeats interleave; they are **not** sequential passes over the file. Do
not "optimize" this into a loop of passes.

## Everything is project-scoped

taxman is a **project tool, top to bottom**. An audit belongs to a project on disk, next to
the messages it sends and the data it collects, the way a `pyproject.toml` belongs to a
package. There is no user-global audit, and **there never will be**.

- **No `~/.taxman/audits/`.** No global audit directory, no search fallback, no
  local-shadows-global precedence, and no `source: local | global` on a discovered audit.
- **One name resolves to exactly one file**: `<project root>/audits/<name>.yaml`. If it
  isn't there, that is a hard error naming the root that was searched — never a quiet
  second lookup somewhere else.
- **Outside a project is an error, not a fallback.** A command that needs an audit and
  finds no project root says so plainly ("this directory is not a taxman project") instead
  of guessing at the current working directory.

`~/.taxman/` survives for exactly one thing: `state.yaml`, the remembered "no, don't ask
again" from `doctor`. That is a fact about the machine, not about an audit. Nothing else
belongs in it — an audit, a provider default, a key, a message file, an output path, none
of it.

The reason is reproducibility, which is the whole point of an audit tool. A global audit
is invisible in the repo that depends on it: a collaborator clones the project, runs the
same command, and silently gets a different audit or none at all, and the manifest cannot
record what it could not see. Precedence rules make that worse, not better — "it worked on
my machine" is exactly the shadowing bug they produce.

If an audit is worth reusing across projects, it is worth committing to each one, or
distributing as a file people copy. Both leave a trace on disk. A hidden global does not.

## The project root

The root is found by walking **up** from the working directory to the nearest `taxman.yaml`
marker file — so every command works from anywhere inside a project, like `git`. Relative
paths in an audit (`messages:`, `output.dir`) resolve against that root, never against the
working directory and never against the audit file's own parent.

The marker is a **visible file**, not a hidden `.taxman/` directory and not the presence of
`audits/`. `audits/` is a common directory name in exactly the repos taxman's users keep —
compliance, security, smart-contract — and a walk-up that matched it would happily adopt a
stranger's folder of PDFs as a project root and write `data/` into it.

`taxman.yaml` is a **marker, not a config file**. Core reads its schema version and nothing
else. Do not add a setting to it, ever: a project-level default is the same second
configuration channel that `taxman setup` and `~/.taxman/providers/<name>.yaml` were, and
it is deleted for the same reasons. Settings live in the audit, which `taxman init` writes
fully commented. `tests/test_conventions.py` fails the suite if core reads any other key
out of the marker.

`taxman init` creates the marker when there isn't one — that is what makes a directory a
project. There is no separate "init a project" command to learn.

## Layout

```
src/ai_taxman/
├── __init__.py       # __version__ + public Python API
├── __main__.py       # python -m ai_taxman
├── cli/              # Typer app: init, collect, audits, doctor, completion
├── core/             # config, credentials, discovery, messages, records,
│                     # writer, runner, registry, state, environment, errors
└── providers/
    ├── base.py       # the ONLY shared provider contract + setup question types
    └── openai/       # provider.py, config.py, models.py

docs/provider-apis/   # API docs per provider - read before touching provider code
```

Audit YAMLs resolve to `<project root>/audits/<name>.yaml`, and nowhere else.

## Commands

```
uv run pytest              # full suite, offline, live tests deselected
uv run pytest -m live      # opt in to real API calls (needs keys)
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run taxman --help
```

## Provider API documentation — do not guess

`docs/provider-apis/` maps every provider feature to canonical, machine-readable API
documentation. **Read the doc before writing or changing provider code.** Do not rely on
recalled parameter names, response shapes, usage keys, or error semantics — providers rename
and deprecate, and a wrong field name in an adapter fails silently at audit time: the run
completes, the JSONL fills up, and the data is wrong.

Consult it when you: change `build_request()` or `extract()`; add a key to a `model:` block;
touch retry or error handling; refresh `known_models()`; or implement a new provider.

Every provider we target serves a markdown twin of its docs. Fetch that, not the HTML page:

| Provider | Markdown URL |
|---|---|
| OpenAI | `developers.openai.com/api/<path>` + `.md` |
| Anthropic | `platform.claude.com/docs/en/<path>` + `.md` |
| Gemini | `ai.google.dev/gemini-api/docs/<path>` + `.md.txt` |
| xAI (Grok) | `docs.x.ai/developers/<path>` + `.md` |
| Perplexity | `docs.perplexity.ai/docs/<path>` + `.md` |

Two soft-200 traps: `platform.openai.com/docs/*.md` and `ai.google.dev/*.md` both answer HTTP
200 with HTML. A 200 is not proof — the body must start as markdown.

For a page not listed in `docs/provider-apis/`, fetch that provider's `llms.txt` (each file
links its own); it indexes every page with its markdown twin. Prefer that to a web search.

If a markdown twin genuinely does not exist, record the human-facing URL and mark it
`HTML only`. Read those with the Claude-in-Chrome tools (`mcp__claude-in-chrome__*`), not
WebFetch — these docs sites render client-side, so a plain fetch returns an empty shell.

When a doc contradicts the code, the doc wins — but fix the code with a failing test first,
and update `docs/provider-apis/` if the URL moved.

## Adding a new provider

1. Read `docs/provider-apis/<name>.md` end to end and fetch the request/response reference it
   links. If that file does not exist, write it first — the markdown convention and the
   provider's index URL are in `docs/provider-apis/README.md`.
2. `mkdir src/ai_taxman/providers/<name>/` — mirror the `openai/` layout exactly.
3. Add the SDK as an optional extra in `pyproject.toml` (and to the `all` extra), then `uv sync --all-extras`.
4. Write failing tests first: `build_request`, `extract` against a recorded fixture, template parses.
5. Implement `Provider` from `providers/base.py`; export `PROVIDER` at module level.
6. Implement `known_models()`, `render_template()`, `describe_model()` and
   `default_api_key_env` — these feed `taxman init` and shell completion. `render_template()`
   takes no arguments and renders every parameter blank but commented.
7. Confirm the conformance suite (`tests/providers/test_conformance.py`) picks it up and passes.
8. Change **nothing** under `core/` or `cli/`. If you need to, the seam is wrong — fix the seam.

## Commit messages

Describe the change and nothing else. No AI attribution of any kind — no co-author trailers, no
session links, no "generated with" lines.
