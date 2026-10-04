"""Shared command-line helpers."""

from __future__ import annotations

import os
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

import typer

from ai_taxman.core.errors import TaxmanError

F = TypeVar("F", bound=Callable[..., Any])


def display_path(path: Path) -> str:
    """A path written from where the user is standing.

    Commands work from anywhere inside a project, so output has to say which
    file it means. Under the working directory a relative path says it in the
    fewest characters; outside it, only an absolute path does.
    """
    try:
        cwd = Path.cwd()
        # As written first, so a file in a folder linked in from elsewhere is still
        # shown under the working directory it was reached from.
        candidates = ((Path(os.path.abspath(path)), cwd), (path.resolve(), cwd.resolve()))
    except OSError:
        return str(path)
    for written, base in candidates:
        try:
            relative = written.relative_to(base)
        except ValueError:
            continue
        return str(relative) if relative.parts else "."
    return str(path)


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
