<p align="center">
  <img src="assets/taxman-logo.jpeg" alt="ai-taxman" width="600">
</p>

# ai-taxman 🧾

> *knock knock*
>
> Open up. It's the taxman — you're being audited.

A command-line tool for auditing AI providers: send a file of messages to a model and record
every raw response. Also usable as a Python package.

## Install

```bash
uv tool install "ai-taxman[all]"                  # every provider
#uv tool install "ai-taxman[openai]"              # one provider
#uv tool install "ai-taxman[openai,anthropic]"    # several
```

Currently available: `openai`, `gemini`, and `anthropic`.

## Quick start

```bash
taxman init                              # set up a project in this directory
taxman audits new openai <audit-name>    # writes taxman/audits/<audit-name>.yaml
```

Edit the audit file: set `api_key_env:` to the name of the variable holding your key, and change any other setting.
Then write your messages, one per line:

```bash
export OPENAI_API_KEY="sk-..."
echo "When is the next US federal election?" > taxman/messages/<audit-name>.txt

taxman audits validate <audit-name>    # check everything; sends nothing
taxman collect <audit-name>            # sends the messages; bills your account
```

Responses are written to `taxman/data/<audit-name>/<run_id>/responses.jsonl`.

## How it works

1. **Set up a project.** `taxman init` creates a `taxman` project, which contains a handful of directories.
2. **Create an audit.** `taxman audits new <provider> <audit-name>` creates an "audit file" (`<audit-name>.yaml`) for the user-specified provider (`<provider>`). All audit settings and parameters are specified here with human-readable text.
3. **Write messages.** A `.txt` "messages file" contains all of the messages that will be sent (separately) to the AI provider. One message per line.
4. **Add a system prompt (optional).** A system prompt, sent with every message, can be specified in a separate `.txt` file.
5. **Collect.** `taxman collect <audit-name>` collects the audit as specified in the audit file and creates a manifest of what was run.

## Projects

A project is a directory with a `taxman.yaml` at its root.
`taxman init` creates a project in the current working directory and prompts the user to set paths manually or accept the defaults.
If `taxman init --yes` is called, default paths are automatically accepted.

```sh
taxman.yaml        # marks the root and records the folders below
taxman/
├── audits/        # one YAML file per audit
├── messages/      # .txt messages files
├── prompts/       # system prompt files
├── data/          # collected responses
└── logs/          # run logs
```

Commands work from anywhere inside the project, like `git`.

## Audits

`taxman audits new <provider> <audit-name>` takes no other arguments.
It writes every setting at its default, with a comment above each giving its type, default, options, and a link to the provider's docs.
You configure an audit by editing that file.
Without the comments:

```yaml
audit: <audit-name>
provider: openai
api_key_env: <insert_api_key_env_var_here>
messages: taxman/messages/<audit-name>.txt
system_prompt:

output:
  dir: taxman/data/{audit}/{run_id}
  filename: responses.jsonl
  compress: false
  log_dir: taxman/logs/{audit}

execution:
  repeats: 1             # times each message is sent
  max_concurrency: 8
  batch: false
  timeout_s: 120
  max_retries: 5
  on_error: continue
  shuffle: false

model:                   # provider-specific; this is OpenAI's
  name: gpt-5
  temperature:
  # ...
```

Relative paths resolve against the project root.

### API keys

`api_key_env:` names one environment variable, and taxman reads only that one. It never
stores a key. Export the variable however you like (shell profile, `direnv`, CI secret).
Because each audit names its own variable, one machine can hold several keys for the same
provider.

If the field is missing, still the placeholder, or names an unset variable, `collect` stops
before sending anything.

### Messages

One message per line in a `.txt` file. Blank lines and lines starting with `#` are skipped;
start a line with `\#` to send a message that begins with `#`.
Each message may appear only once; to send one more than once, set `execution.repeats`.

### System prompt

Put the text in a file and name it in the audit:

```yaml
system_prompt: taxman/prompts/neutral.txt
```

Each provider sends it through its own API field. The manifest records the file's path
and text.

## Collecting

```bash
taxman collect <audit-name>
```

Each message is sent `repeats` times, concurrently. Every setting comes from the audit file;
there are no command-line overrides. Each run writes to its own directory:

- `responses.jsonl`: one row per response, flushed as it arrives.
- `manifest.json`: resolved config, tool version, message ids, counts, and timings.
  It is written before the first request and updated when the run ends.

The manifest's `status` is `running` until the run ends, then `complete`, `stopped_early`,
`interrupted`, or `failed`. `n_messages × repeats` is the number of responses expected. A
killed run keeps every response already received.

### Collecting again

`taxman collect <audit-name>` on an audit already collected finishes its latest run, in the same
directory. It sends only what that run is missing: responses never received, and responses that
failed. It says so before it starts.

- **If the run already has every response**, nothing is sent; it says the audit is complete.
- **If the audit has changed** since the run began, it refuses, and lists what changed. Every
  setting counts, as do the messages and the system prompt text. Reordering lines or editing
  `#` comments does not count.
- **If another `collect` is still collecting the run**, it refuses rather than send the same
  messages twice.

`--new-run` starts a new run beside the old ones, whatever state the latest is in. That needs
`{run_id}` in `output.dir`, which `taxman audits new` writes.

| Option | Effect |
|---|---|
| `--new-run` | Start a new run, instead of finishing the latest one |
| `-q`, `--quiet` | Print only the summary |
| `--log-file PATH` | Write the log to this file only |
| `--log-level LEVEL` | `debug`, `info` (default), `warning`, or `error` |
| `-b`, `--background` | Run detached; print the pid |

### Logs

Each run logs one line per response to stderr and to `<output.log_dir>/<run_id>.log`. The
summary goes to stdout. Message text and API keys are never logged.

### Background runs

`-b` detaches the run. stdout is the pid and nothing else; the run id, log path, and output
path go to stderr. The pid is also saved to `collect.pid` in the run directory. The audit is
validated, and the run to collect chosen, before the run detaches: a complete audit prints
nothing on stdout.

```bash
openai=$(taxman collect openai-probe -b)
anthropic=$(taxman collect anthropic-probe -b)
while kill -0 "$openai" 2>/dev/null || kill -0 "$anthropic" 2>/dev/null; do sleep 5; done
```

`kill <pid>` stops gracefully: in-flight requests finish and the manifest is set to
`stopped_early`. After `kill -9` the responses on disk survive, but the manifest still says
`running`.

## Output

One JSON object per line, the same shape for every provider:

```json
{
  "schema_version": 1,
  "audit": "<audit-name>",
  "run_id": "20260830T142201Z-a1b2c3",
  "message_id": "4b841e8b-62f2-5814-b265-36aeb267e4b0",
  "message": "When is the next US federal election?",
  "repeat": 2,
  "provider": "openai",
  "model": "gpt-5",
  "requested_at": "…", "received_at": "…", "latency_ms": 812,
  "status": "ok",
  "error": null,
  "attempts": 1,
  "raw": {}
}
```

`raw` is the provider's response, verbatim. taxman does not parse it; extracting text,
token counts, or citations is a separate step run on the data afterward.

### Message ids

`message_id` is a UUID5 of the message's text, so the same message has the same id in every
file and every run. An id can be computed from text, but never turned back into it.

```bash
taxman messages ids taxman/messages/probe.txt   # each message in a file with its id, as CSV
taxman messages ids -t "Hello" -t "Goodbye"     # the same, for messages typed here
taxman messages text <id> taxman/messages/probe.txt   # the message in a file with this id
```

In Python, `from ai_taxman import message_id` gives the id of a string.
Outside taxman, an id is `uuid5(uuid5(NAMESPACE_URL, "https://matthewdeverna.com/"), text)`,
where `text` is the line with surrounding whitespace removed. The trailing `/` is part of the
namespace.

### Schema versions

Changes are additive unless listed here.

| Version | Change |
|---|---|
| 1 | Initial schema. |

## Commands

| Command | What it does |
|---|---|
| `taxman init` | Set up a project in this directory |
| `taxman audits new <provider> <audit-name>` | Write a new audit file |
| `taxman audits list` | List the project's audits |
| `taxman audits show <audit-name>` | Print an audit's resolved settings |
| `taxman audits validate <audit-name>` | Check an audit without sending anything |
| `taxman collect <audit-name>` | Run an audit |
| `taxman messages ids <file>` | Print each message in a file with its id |
| `taxman messages ids -t <message>...` | Print the id of each message given |
| `taxman messages text <id> <file>` | Print the message in a file with this id |
| `taxman providers` | List the installed providers |
| `taxman doctor` | Check `PATH` and tab completion, and offer to fix them |

## Python

```python
from ai_taxman import run_audit

result = run_audit("<audit-name>")
print(result.n_ok, result.n_error, result.output_path)
```

A name resolves as on the command line; a path ending in `.yaml` is read directly.
`run_audit` also accepts an `AuditConfig` from `load_audit()` and an `on_record` callback.
Like `collect`, it finishes the audit's latest run; `new_run=True` starts another, and
`result.resumed` and `result.already_complete` say which happened.

## Tab completion

```bash
taxman --install-completion      # or accept the offer from `taxman doctor`
```

Restart your shell. Completion covers commands, providers, models, and audit names.

**zsh: check startup time.** The installer appends
`fpath+=~/.zfunc; autoload -Uz compinit; compinit` to `~/.zshrc`. If your setup already runs
`compinit` (oh-my-zsh, prezto, most prompts), zsh then rebuilds its completion cache on every
start. Run `time zsh -i -c exit` twice; if the second run takes much more than 0.2s:

1. Shorten the installer's line to `fpath+=~/.zfunc`.
2. Move it to the top of `~/.zshrc`, above any `source` line. `compinit` reads `fpath`
   once, so a directory added after it is ignored.
3. `rm ~/.zcompdump` and open a new terminal.

Check with `print -r -- ${_comps[taxman]:-NOT REGISTERED}`. It should print `_taxman`; if it
prints `NOT REGISTERED`, the `fpath` line is still below `compinit`.

The installer also adds `zstyle ':completion:*' menu select`, which enables arrow-key menus
for every command. Delete it if you don't want that.

To uninstall: `rm ~/.zfunc/_taxman` and delete the block in `~/.zshrc` marked
`# taxman additions`. bash and fish need no extra steps.

## Supported providers

| Provider | Extra | Status |
|---|---|---|
| OpenAI | `ai-taxman[openai]` | Responses API; no batch mode yet |
| Anthropic | `ai-taxman[anthropic]` | Messages API; no batch mode yet |
| Google Gemini | `ai-taxman[gemini]` | Interactions API; no batch mode or safety settings |

Each provider is isolated from the others. To add one, see [CLAUDE.md](CLAUDE.md) and
[`docs/provider-apis/`](docs/provider-apis/).

## Development

```bash
uv sync --all-extras
uv run pytest
```

## License

MIT — see [LICENSE](LICENSE). No tax is owed on this one.
