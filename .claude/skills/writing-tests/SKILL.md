---
name: writing-tests
description: How to write, change, merge or delete tests in ai-taxman without the suite growing back. Use whenever you add, edit, move or remove anything under tests/, write a failing test before implementing behaviour, add a provider or a provider setting, or are about to run pytest with -m, live tests, or anything that might call a provider API.
---

# Writing tests in ai-taxman

The suite is lean on purpose: every test exists to catch a bug no other test
catches. Keep it that way.

## Start, and the TDD loop

- Start every session with `uv run pytest`. Know the suite is green before you
  change anything, so a red test later is yours.
- Red, green, refactor, as CLAUDE.md requires: write the test, run it, watch it
  fail for the right reason, write the least code that passes, then tidy with
  the test green.
- A test that replaces or absorbs others must be shown red too. When the defect
  would live in `src/`, show it with a throwaway script outside the repo (a
  subclass or a patched function with the one defect), never by editing `src/`
  for the demonstration.

## Before you add a test

Name the bug it would catch that no existing test catches - one sentence, in
your head or in the docstring. If you cannot, do not add it.

Then, in order of preference:

1. Add a row to an existing `parametrize` table.
2. Add an assertion to the test that already sets up the same scenario.
3. Only then write a new test function.

## Where a test goes

- **The lowest layer that can see the behaviour.** Discovery rules in
  `tests/core/test_discovery.py`, file repair in `test_writer.py`, planning and
  resuming in `test_runs.py` / `test_resume.py`, the run itself in
  `test_runner.py`. A CLI test covers what only the command does - its exit code,
  what it prints, what it refuses - plus one test that the wiring reaches core.
  Do not re-check a core rule through `CliRunner`.
- **Shared provider behaviour goes in `tests/providers/test_conformance.py`**,
  parametrized over the registry, never copied into each provider's folder. If a
  rule holds for every provider (unknown settings refused, `extra:` sent as
  written, a run never borrows another's client, the template shows every
  setting), it is a conformance test. A provider folder tests only what its API
  makes different: the wire format, the payload, its own SDK's errors.
- **Wire-level provider tests** go through the real SDK client with a fake
  transport, as `tests/providers/anthropic/test_send.py` does. No network.

## What to test

- **The rules taxman chooses**, not pydantic's. A thinking budget below
  `max_tokens`, a domain with a scheme refused rather than rewritten, a search
  setting without `web_search` refused: each gets a test. That a field is `ge=1`
  or a `Literal` of four values is pydantic's job; each provider has one table
  of refused values (`test_a_value_the_field_does_not_take_is_refused` in
  `tests/providers/anthropic/test_request.py`) with one row per bound or fixed
  set - add a row there, do not write a function.
- "Blank means default" is tested once, in `tests/providers/test_settings.py`,
  and "an unknown setting is refused" once, in the conformance suite. Do not
  repeat either per block.
- **Never assert on documentation wording**: not CLAUDE.md, not the README, not
  `docs/`, not the sentence a field's `description=` renders into a template.
  Wording changes on purpose, and a test of it only makes the change twice. Test
  the structure instead: that every setting explains itself, that a link is one
  the docs file lists.
- Never test the test double. A test that only exercises `FakeProvider` cannot
  catch a bug in `src/`.
- An allowlist beats a list of forbidden names: `test_the_cli_offers_only_these_commands`
  catches a configuration command under any name; a test that `setup` is absent
  catches only `setup`.

## Patterns to copy

- `tests/providers/anthropic/test_send.py` - wire level: the real SDK client,
  `httpx.MockTransport`, assertions on exactly what went over the wire.
- `tests/providers/test_conformance.py` - shared contracts: one test, every
  provider, names no provider.
- `tests/cli/test_audits_new.py` - CLI: what the command does, end to end, with
  the rules themselves tested in core.

## Cost: never spend the user's money

- **Never run live tests.** `-m live` selects tests that call provider APIs and
  bill the user's keys, which are exported in their shell. Passing `-m` at all
  replaces the default from `addopts`, so any `-m` you pass must say
  `and not live and not network`, and so must anything that runs pytest for you.
- **Never set or export an API key variable**, and never read one to "check" it.
- **Never re-record `tests/providers/*/fixtures/`.**
- If a live check seems needed, stop and ask the user, naming the tests and
  roughly how many API calls they would make.
- The default run blocks the network (pytest-socket). A test that needs it is
  `live` (spends money) or `network` (free, e.g. docs link checks), and is
  marked `enable_socket` too.

## Deleting or weakening a test

Never delete, skip, loosen, or `xfail` a test without the user's approval - not
to make the suite pass, and not because it looks redundant. Propose it, naming
the test that still covers the behaviour, and wait.
