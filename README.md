<p align="center">
  <img src="assets/taxman-logo.jpeg" alt="ai-taxman" width="600">
</p>

# ai-taxman 🧾

> *knock knock*
>
> Open up. It's the taxman — you're being audited.

`ai-taxman`: a command line interface to audit popular AI systems.
Also usable as a regular Python package.

> ⚠️ **`ai-taxman` is still in development.**

## Install

```bash
uv tool install "ai-taxman[openai]"
```

Then `taxman init` sets up a project, `taxman audits new openai <audit-name>` writes a fully
commented audit file, and you fill in the blanks. There is no configuration command and
nothing to set up per machine — the audit file is the only place a run's settings are chosen. Full walkthrough below. Provider SDKs are
optional extras, so you only install the ones you audit.

<details>
<summary>Working on <code>ai-taxman</code> itself</summary>

```bash
uv sync --all-extras
uv run pytest
uv run taxman --help
```

</details>

## Setting up

A full walkthrough, from nothing to a first audit. Roughly two minutes.

### 1. Install

```console
$ uv tool install "ai-taxman[openai]"
Installed 1 executable: taxman
```

If uv warns that `~/.local/bin` is not on your `PATH`, run `uv tool update-shell` and open
a new terminal. `taxman doctor` checks this for you at any time.

### 2. Export your API key

taxman never stores a key. It reads exactly one environment variable, whichever one your
audit names, from the environment the command runs in:

```bash
export OPENAI_API_KEY="sk-..."          # add it to ~/.zshrc to make it stick
```

Any variable name works — `OPENAI_API_KEY` is just OpenAI's convention, and each audit
names its own. That means one machine can hold several keys for the same provider (a
personal one and a lab one, say) and each audit says which it uses. In CI, use the
runner's secret mechanism; nothing else changes.

### 3. Set up a project

taxman is project-scoped from top to bottom. A project is a directory with a `taxman.yaml`
at its root, and `taxman init` is how you make one. It asks where each folder taxman uses
should go, relative to the project root; press Enter to keep a default:

```console
$ cd ~/research/election-study
$ taxman init
Setting up a taxman project in /Users/you/research/election-study
Each folder is relative to this directory. Press Enter to keep the default.

  Audit files    [taxman/audits]:
  Message files  [taxman/messages]:
  System prompts [taxman/prompts]:
  Collected data [taxman/data]:
  Run logs       [taxman/logs]:
Started a taxman project at /Users/you/research/election-study
  taxman.yaml marks the root and records its folders:
    Audit files     taxman/audits/
    Message files   taxman/messages/
    System prompts  taxman/prompts/
    Collected data  taxman/data/
    Run logs        taxman/logs/

Next, create an audit for each provider you want to audit:
  taxman audits new <provider> <audit>
```

`taxman init --yes` accepts every default without asking, for scripts. A project is set up
once: `init` refuses to run inside an existing one. To move a folder later, edit
`taxman.yaml` — new audits pick up the change; existing audits keep the paths they were
written with.

From then on every taxman command works from anywhere inside the project, the way git does:

```
~/research/election-study/
├── taxman.yaml               <- the project root, and its folders
└── taxman/
    ├── audits/
    ├── messages/
    ├── prompts/
    ├── data/
    └── logs/
```

Run a command somewhere that isn't a project and it says so, rather than guessing:

```console
$ cd /tmp && taxman audits list
error: this directory is not a taxman project: no taxman.yaml in /tmp or any
parent directory. Start one with `taxman init`, or change to a directory inside
an existing project.
```

There are no user-global audits — nothing hidden in your home directory that a collaborator
who clones your project would not get. If an audit is worth reusing, commit it to each
project that uses it.

### 4. Create an audit for each provider

`audits new` takes the provider and a name for the audit, and nothing else:

```console
$ taxman audits new openai election-probe
Created taxman/audits/election-probe.yaml

Next:
  1. Add messages in taxman/messages/election-probe.txt
  2. (Optional) Add a system prompt in taxman/prompts/
  3. Finalize details in taxman/audits/election-probe.yaml
  4. Collect data by running `taxman collect election-probe`
```

That is the whole command — there are no other arguments or flags. Every setting is written
at its default with a comment saying what it does, and the project's folders are filled into
its paths, so you change things by editing the file rather than by memorising options. Each
provider gets its own audit file.

One field is left blank on purpose, because only you know the answer:

```yaml
# The one environment variable holding this audit's API key.
# taxman reads this name and no other. Export the variable in your
# shell, then put its name here.
# For openai this is usually OPENAI_API_KEY.
api_key_env: <insert_api_key_env_var_here>
```

Replace the placeholder with the name of the variable you exported in step 2. Leave it as
it is and `taxman collect` stops before sending anything, saying that
`<insert_api_key_env_var_here>` is not set.

### 5. Write your messages and check it

```console
$ cat > taxman/messages/election-probe.txt <<'EOF'
When is the next US federal election?
Who is eligible to vote by mail?
EOF

$ taxman audits validate election-probe
taxman/audits/election-probe.yaml is valid.
2 message(s) x 1 repeat(s) = 2 request(s).
```

`validate` resolves the config, checks the settings against the provider, confirms the
variable in `api_key_env:` is exported, and counts the requests — **without sending
anything or spending anything.** Run it before every real collection.

### 6. Run it

```console
$ taxman collect election-probe
Collecting election-probe: openai (gpt-5), 1 repeat(s) per message.

2 ok  ->  taxman/data/election-probe/20260830T142201Z-a1b2c3/responses.jsonl
```

This one makes real API calls and bills your account.

### If something's wrong

```console
$ taxman doctor
taxman is ready: on your PATH, with tab completion.
```

`doctor` checks that `taxman` is on your `PATH` and that tab completion is installed, and
offers to fix either. Nothing is written to a shell config unless you say yes, and a
"don't ask again" is remembered.

> **Chicken-and-egg:** if `taxman` isn't on your `PATH` yet, your shell can't find it to
> run this. Use `uvx ai-taxman doctor` for the first run — it needs no install and no
> `PATH` — or use the full path uv printed.

## The workflow

Once the project is set up and your key is exported, the loop is three steps.

**1. Describe the audit.**

```bash
taxman audits new openai election-probe
```

That writes `taxman/audits/election-probe.yaml` with every setting at its default and
commented, ready for you to edit:

```yaml
audit: election-probe
provider: openai
api_key_env: OPENAI_API_KEY   # you fill this in; the one variable taxman reads

messages: taxman/messages/election-probe.txt
system_prompt:             # a file in taxman/prompts/, sent with every message; blank for none

output:
  dir: taxman/data/{audit}/{run_id}
  filename: responses.jsonl
  compress: false          # true to gzip the output

execution:
  repeats: 1               # times to send EACH message; all repeats go out together
  max_concurrency: 8
  batch: false
  timeout_s: 120
  max_retries: 5
  on_error: continue
  shuffle: false

model:                     # settings specific to the `openai` provider
  name: gpt-5
  temperature:
  reasoning_effort:

  search:                  # web search: off unless web_search is true
    web_search:
```

**2. Write your messages** — one per line, in a plain `.txt` file. Blank lines
and lines starting with `#` are ignored.

```
# taxman/messages/election-probe.txt
When is the next US federal election?
Who is eligible to vote by mail?
```

To send a system prompt with every message, write it in a file in the prompts folder
and name that file in the audit:

```yaml
system_prompt: taxman/prompts/neutral.txt
```

It is set the same way for every provider, and each provider sends it the way its API
takes one (OpenAI's `instructions`, for example). The manifest records which file was sent,
its text, and a hash of that text, so the run still says what was sent after the file
changes. Every response row carries the same hash.

**3. Run it.**

```bash
taxman collect election-probe
```

Every message is sent `repeats` times, concurrently, and each raw response is
written to `taxman/data/election-probe/<run_id>/responses.jsonl` as it arrives —
alongside a `manifest.json` recording exactly what was run.

Both files are written as the run happens, not at the end. The manifest lands
before the first request, so a run you kill halfway still describes itself, and
every response that came back before that moment is already on disk:

```json
{
  "run_id": "20260830T142201Z-a1b2c3",
  "status": "running",
  "taxman_version": "0.0.2",
  "messages_hash": "sha256:…",
  "system_prompt_path": "taxman/prompts/neutral.txt",
  "system_prompt_hash": "sha256:…",
  "system_prompt_text": "You are a neutral assistant. Answer briefly.",
  "n_messages": 40, "repeats": 3,
  "started_at": "…", "finished_at": null,
  "n_ok": 26, "n_error": 0,
  "config": {  }
}
```

`status` is `running` until the run ends, then `complete`, `stopped_early`,
`interrupted`, or `failed` — so a partial directory is never mistaken for a
finished one. `n_messages × repeats` is the number of responses to expect.

Each run gets its own directory. If you edit `output.dir` and drop `{run_id}`,
the second run into that directory is refused rather than appended to the first.

`--run-id <id>` names a run instead of taking the generated timestamp, and
running again with the same id adds to it. An id has to work both as a directory
name and as a field in every record, so it may contain only letters, digits,
dots, dashes and underscores, and must start with a letter or a digit — anything
else is refused before the run starts.

### Watching a run

A collection logs as it goes — one line per response — to the terminal, and to
`<output.log_dir>/<run_id>.log` (`taxman/logs/<audit>/<run_id>.log` in a new audit), so every run
leaves its log behind next to the project:

```
2026-08-30T14:22:01.004Z INFO    ai_taxman.core.runner  run starting  run_id=20260830T142201Z-a1b2c3 audit=election-probe provider=openai model=gpt-5 messages=40 repeats=3 expected=120 concurrency=8 output=taxman/data/election-probe/20260830T142201Z-a1b2c3/responses.jsonl
2026-08-30T14:22:01.816Z INFO    ai_taxman.core.runner  ok  message=m0000 repeat=0 attempts=1 latency_ms=812
2026-08-30T14:22:04.219Z WARNING ai_taxman.core.runner  retrying  message=m0003 repeat=1 attempt=1/5 in=0.5s RateLimitError: 429
```

On the terminal the log goes to stderr and the summary to stdout, so you can
keep either:

```bash
taxman collect election-probe > summary.txt        # summary to a file, log on screen
taxman collect election-probe --log-file run.log   # the log to this file only
taxman collect election-probe --log-level warning
```

Message text and API keys are never logged — ids and counts only. The text is
already in the JSONL, and a log is a file you might paste into an issue.

### Running in the background

`--background` (`-b`) starts the run detached and gives you the prompt back. It
survives the terminal that started it, logs to the logs folder like any other
run, and writes its pid to `collect.pid` in the run directory:

```bash
$ taxman collect election-probe -b
Collecting election-probe in the background: openai (gpt-5), 3 repeat(s) per message.

  run id   20260830T142201Z-a1b2c3
  pid      51234
  log      taxman/logs/election-probe/20260830T142201Z-a1b2c3.log
  output   taxman/data/election-probe/20260830T142201Z-a1b2c3/responses.jsonl
  stop     kill 51234   (or: kill $(cat taxman/data/election-probe/20260830T142201Z-a1b2c3/collect.pid))
```

That block goes to stderr; **stdout is just the pid**, so a script can hold on
to it. Auditing two providers at once is then a few lines:

```bash
#!/bin/bash
openai=$(taxman collect openai-probe -b)
anthropic=$(taxman collect anthropic-probe -b)

while kill -0 "$openai" 2>/dev/null || kill -0 "$anthropic" 2>/dev/null; do sleep 5; done
echo "both finished"
```

`kill <pid>` stops a run **gracefully**: requests already in flight are finished
and written, and the manifest is closed out as `stopped_early`. Everything
collected up to that point is kept. (`kill -9` does not get that courtesy — the
responses already on disk survive, but the manifest is left saying `running`,
which is how you will know.)

The audit is validated before the fork, so a bad `model:` block, an unset key
variable, or a missing message file is an error at the prompt rather than a pid
for a run that was never going to work.

## Commands

| Command | What it does |
|---|---|
| `taxman doctor` | Check PATH and tab completion, and offer to fix them |
| `taxman init` | Set up a project here, asking where each folder goes |
| `taxman audits new <provider> <audit>` | Scaffold an audit YAML in the project's audits folder |
| `taxman collect <audit>` | Run an audit |
| `taxman audits list` | List the audits in this project |
| `taxman audits show <audit>` | Print an audit's fully resolved settings |
| `taxman audits validate <audit>` | Check an audit without sending anything |
| `taxman providers` | List the providers this install can audit |
| `taxman --install-completion` | Install tab completion for your shell |

Tab completion covers command names, provider names, model names, **and your
audit names**. `taxman doctor` offers to install it; `taxman --install-completion` does it
directly. On zsh, if your terminal starts more slowly afterward, see
[Tab completion](#tab-completion).

## Tab completion

Install it the normal way — `taxman doctor` offers it, or run it directly:

```bash
taxman --install-completion
```

Restart your shell, and `taxman <Tab>` completes commands, provider names, model names,
and your audit names.

### zsh: check your startup time afterward

One thing to watch for, on zsh only.

The installer appends its own `compinit` call to `~/.zshrc`. If your shell already runs one
— oh-my-zsh, prezto, Homebrew's snippet, and most custom prompts all do — you now have two.
The second sees an `fpath` that changed after the first ran, so zsh rebuilds its completion
cache on **every** shell start.

Check it. Run this twice; the first run does a one-time rebuild, the second is your real
startup time:

```console
$ time zsh -i -c exit
```

Comfortably under ~0.2s means there is nothing to do. If it is closer to a second, apply the
fix below. (On the machine this was measured on, it was the difference between 0.10s and
1.0-1.3s on every new terminal.)

### The fix, if it is slow

Two changes, and **both** are needed. Doing only the first is the common mistake: it stops
the slowdown but silently breaks completion.

**1. Drop the duplicate `compinit`.** Find the line the installer appended to `~/.zshrc`:

```zsh
fpath+=~/.zfunc; autoload -Uz compinit; compinit
```

and cut it down to just:

```zsh
fpath+=~/.zfunc
```

**2. Move that line to the very top of `~/.zshrc`** — above everything, in particular above
any line that *sources* another file:

```zsh
# taxman additions - delete these lines and ~/.zfunc/_taxman to uninstall
fpath+=~/.zfunc

source ~/.my-prompt          # <- your compinit probably lives in here
```

This step is easy to skip, because your `compinit` is usually not visible in `~/.zshrc` at
all. oh-my-zsh, prezto, and hand-rolled prompt files all run it from inside a file you
source, so "above your `compinit`" means above the `source` line, not below it. `compinit`
scans `fpath` once and never looks again — a directory added afterwards is ignored, even
though `fpath` looks correct by the time your shell finishes starting.

**Then check it worked.** Open a new terminal:

```console
$ print -r -- ${_comps[taxman]:-NOT REGISTERED}
_taxman
```

`_taxman` means completion is wired up. `NOT REGISTERED` means `fpath+=~/.zfunc` is still
below whatever runs `compinit`. If you had already started a shell with the old ordering,
delete the stale cache first — `rm ~/.zcompdump` — and open a new terminal.

### The other line it adds

```zsh
zstyle ':completion:*' menu select
```

The installer adds this as well. It is cosmetic and has nothing to do with speed — it gives
you arrow-key selection menus on Tab, for **every** command on your system, not just
`taxman`. Plenty of people like it. Delete it if you would rather not have it.

### If `taxman <Tab>` does nothing

Check whether zsh ever registered it. In a new terminal:

```console
$ print -r -- ${_comps[taxman]:-NOT REGISTERED}
```

`NOT REGISTERED` almost always means `~/.zfunc` is on `fpath` but was added *after*
`compinit` ran, so it was never scanned. Move `fpath+=~/.zfunc` to the top of `~/.zshrc`,
above any `source` line, then `rm ~/.zcompdump` and open a new terminal. See
[The fix, if it is slow](#the-fix-if-it-is-slow) for the detail.

If the file itself is missing or empty, reinstall with `taxman --install-completion`.

### Why there is a small pause before completions appear

Each Tab press starts a Python process and asks taxman what the options are — that is how
this style of completion works, and it is unrelated to the startup issue above. taxman
defers its heavier imports so a completion loads only what it needs, which keeps that pause
to roughly a tenth of a second rather than most of a second.

### Removing it

```bash
rm ~/.zfunc/_taxman
```

and delete the block from `~/.zshrc`. The installer labels it, so it is easy to find:

```zsh
# taxman additions - delete these lines and ~/.zfunc/_taxman to uninstall
```

### bash and fish

No caveat either way. fish gets `~/.config/fish/completions/taxman.fish` and needs no config
change at all. bash gets a single ordinary `source` line in `~/.bashrc`.

## API keys

An audit names exactly one environment variable in its `api_key_env:` field, and taxman
reads that one and nothing else — there is no fallback chain to reason about, and taxman
never stores a key of its own.

```yaml
api_key_env: OPENAI_API_KEY
```

Export it however you normally would: your shell profile, `direnv`, or a CI secret. The
name is yours to choose, so one machine can hold several keys for the same provider and
each audit says which it uses.

`taxman audits new` leaves the field as `<insert_api_key_env_var_here>` — it will not guess a
variable for you, because guessing is how an audit ends up billing a key it never named.

If the field is still the placeholder, is missing, or names a variable holding nothing,
`taxman collect` stops before sending a single message and tells you which variable it
wanted.

## From Python

```python
from ai_taxman import load_audit, run_audit

result = run_audit("election-probe")
print(result.n_ok, result.n_error, result.output_path)
```

An audit name resolves the way it does on the command line, in the project around the
working directory; a path ending in `.yaml` is read as a path. `run_audit` also accepts a
loaded `AuditConfig`, and takes an `on_record` callback if you want to watch responses
land.

## Output

One JSON object per response. The schema is the same for every provider:

```json
{
  "schema_version": 2,
  "audit": "election-probe",
  "run_id": "20260830T142201Z-a1b2c3",
  "message_id": "m0007",
  "message_hash": "sha256:…",
  "message": "When is the next US federal election?",
  "repeat": 2,
  "system_prompt_hash": "sha256:…",
  "provider": "openai",
  "model": "gpt-5",
  "requested_at": "…", "received_at": "…", "latency_ms": 812,
  "status": "ok",
  "error": null,
  "attempts": 1,
  "raw": {  }
}
```

`raw` is the provider's response verbatim, and it is the whole of it. taxman
collects; it does not parse, clean, or summarise what came back. Pulling the
answer text, token counts, or citations out of `raw` is a separate step, run
against the data on disk — which means a change to how responses are read can
never silently change what was collected.

### Schema versions

Changes are additive: a field may be added, and old files stay readable. The one
exception so far is noted here.

| Version | Change |
|---|---|
| 2 | Removed `text` and `usage`. Collection no longer derives anything from `raw`. |
| 1 | Initial schema. Rows carried `text` and `usage` alongside `raw`. |

Version 1 files remain valid JSONL and lose nothing: everything `text` and
`usage` held was copied out of `raw`, which is still there.

## Supported providers

| Provider | Extra | Status |
|---|---|---|
| OpenAI | `ai-taxman[openai]` | Responses API; batch mode not yet |

Providers are fully isolated from each other — adding one cannot break another.
See [CLAUDE.md](CLAUDE.md) for the contract and a checklist for adding one, and
[`docs/provider-apis/`](docs/provider-apis/) for each provider's API documentation, mapped
feature by feature.

## License

MIT — see [LICENSE](LICENSE). No tax is owed on this one.
