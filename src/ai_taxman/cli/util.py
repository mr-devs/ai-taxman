"""Shared command-line helpers."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

import typer

from ai_taxman.core.errors import TaxmanError

F = TypeVar("F", bound=Callable[..., Any])


def fail(message: str, code: int = 1) -> None:
    """Print an error and exit, without a traceback."""
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def handles_taxman_errors(function: F) -> F:
    """Turn a deliberate library error into a clean message and exit code."""

    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except TaxmanError as exc:
            fail(str(exc))

    return wrapper  # type: ignore[return-value]
