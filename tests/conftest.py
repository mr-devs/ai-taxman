"""Shared fixtures.

`FakeProvider` exists so the runner and CLI can be tested end to end without a
network, an API key, or any provider SDK installed.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from ai_taxman.core import registry
from ai_taxman.core.discovery import Layout
from ai_taxman.providers.base import Provider, Request

#: Every folder directly under the project root. Most tests build a project by
#: hand, and short paths keep them readable; the defaults are tested on their own.
FLAT = Layout(data="data", audits="audits", messages="messages", prompts="prompts", logs="logs")


@pytest.fixture
def flat_layout():
    return FLAT


@dataclass
class FakeProvider(Provider):
    """A provider that answers instantly, from memory."""

    name: str = "fake"
    default_api_key_env: str = "FAKE_API_KEY"
    #: A test double has nothing to authenticate against.
    requires_api_key: bool = False

    #: Exceptions to raise, keyed by message id, popped one per attempt.
    failures: dict[str, list[Exception]] = field(default_factory=dict)
    #: Seconds each send should take, to exercise concurrency.
    delay: float = 0.0
    #: Raised by startup(), standing in for a missing API key.
    startup_error: Exception | None = None
    #: Seconds startup() takes, so two runs can be caught between plan and start.
    startup_delay: float = 0.0
    #: startup() waits for this, when a test sets it: a collect held back until
    #: another has done whatever the test needs done first.
    startup_gate: asyncio.Event | None = None
    #: Every send waits for this, when a test sets it: the run stays going, with
    #: nothing answered, for exactly as long as the test needs to look at it.
    gate: asyncio.Event | None = None
    retryable: tuple[type[BaseException], ...] = ()

    sent: list[Request] = field(default_factory=list)
    api_key: str | None = None
    started: int = 0
    stopped: int = 0
    #: Peak number of overlapping sends observed.
    peak_concurrency: int = 0
    _in_flight: int = 0

    def known_models(self) -> list[str]:
        return ["fake-1", "fake-2"]

    def validate_model_config(self, block: dict[str, Any]) -> dict[str, Any]:
        if "boom" in block:
            raise ValueError("the fake provider rejects `boom`")
        return {"name": "fake-1", **block}

    def describe_model(self, model: dict[str, Any]) -> str:
        return str(model.get("name", ""))

    def render_template(self, defaults: dict[str, Any]) -> str:
        lines = [f"  name: {defaults.get('name') or 'fake-1'}"]
        lines += [f"  {key}: {value}" for key, value in defaults.items() if key != "name"]
        return "model:\n" + "\n".join(lines) + "\n"

    async def startup(self, *, api_key: str | None = None) -> None:
        self.started += 1
        self.api_key = api_key
        if self.startup_delay:
            await asyncio.sleep(self.startup_delay)
        if self.startup_gate is not None:
            await self.startup_gate.wait()
        if self.startup_error is not None:
            raise self.startup_error

    async def shutdown(self) -> None:
        self.stopped += 1

    def is_retryable(self, exc: BaseException) -> bool:
        return isinstance(exc, self.retryable)

    async def send(self, request: Request, *, timeout_s: float) -> dict[str, Any]:
        self._in_flight += 1
        self.peak_concurrency = max(self.peak_concurrency, self._in_flight)
        try:
            self.sent.append(request)
            if self.gate is not None:
                await self.gate.wait()
            if self.delay:
                await asyncio.sleep(self.delay)
            queued = self.failures.get(request.message.id)
            if queued:
                raise queued.pop(0)
            return {
                "echo": request.message.text,
                "repeat": request.repeat,
                "usage": {"input_tokens": 1, "output_tokens": 2},
            }
        finally:
            self._in_flight -= 1


@pytest.fixture
def fake_provider():
    """A registered `FakeProvider`, removed again after the test."""
    provider = FakeProvider()
    registry.register_provider(provider, override=True)
    try:
        yield provider
    finally:
        registry.unregister_provider(provider.name)


@pytest.fixture
def until():
    """Wait, in an async test, for something a run is about to do.

    A fixed sleep is a guess at how long the run takes to get there, which a busy
    machine proves wrong. This waits for the thing itself, and fails the test if
    it never happens.
    """

    async def wait(condition, timeout=10.0):
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                pytest.fail(f"gave up after {timeout}s waiting for the run")
            await asyncio.sleep(0.01)

    return wait
