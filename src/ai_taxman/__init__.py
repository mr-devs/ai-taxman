"""ai-taxman: audit popular AI systems from the command line, or from Python.

The command line is the main interface::

    taxman init
    taxman audits new openai gpt-5-probe
    taxman collect gpt-5-probe

The same workflow from Python::

    from ai_taxman import load_audit, run_audit

    result = run_audit("gpt-5-probe")
    print(result.n_ok, result.output_path)

Every public name is resolved on first use rather than at import time. Shell
completion runs `taxman` on each Tab press, so importing this package has to
stay cheap - eagerly pulling in the config parser and the async runner made
every completion pay for machinery it never touches. `from ai_taxman import
run_audit` behaves exactly as it always has; it just loads the module behind it
at that moment.
"""

import logging
from typing import TYPE_CHECKING

__version__ = "0.0.2"

# A library does not configure logging for its caller. `taxman collect` attaches
# a real handler; importing `ai_taxman` and using the Python API stays silent.
logging.getLogger("ai_taxman").addHandler(logging.NullHandler())

#: Public name -> the module that defines it.
_EXPORTS = {
    "AuditConfig": "ai_taxman.core.config",
    "load_audit": "ai_taxman.core.config",
    "AuditNotFoundError": "ai_taxman.core.errors",
    "ConfigError": "ai_taxman.core.errors",
    "MessageFileError": "ai_taxman.core.errors",
    "ProviderDependencyError": "ai_taxman.core.errors",
    "ProviderError": "ai_taxman.core.errors",
    "ProviderNotFoundError": "ai_taxman.core.errors",
    "TaxmanError": "ai_taxman.core.errors",
    "Message": "ai_taxman.core.messages",
    "read_messages": "ai_taxman.core.messages",
    "ResponseRecord": "ai_taxman.core.records",
    "RunManifest": "ai_taxman.core.records",
    "RunResult": "ai_taxman.core.runner",
    "run_audit": "ai_taxman.core.runner",
    "run_audit_async": "ai_taxman.core.runner",
}

if TYPE_CHECKING:  # pragma: no cover - for type checkers and editors only
    from ai_taxman.core.config import AuditConfig, load_audit
    from ai_taxman.core.errors import (
        AuditNotFoundError,
        ConfigError,
        MessageFileError,
        ProviderDependencyError,
        ProviderError,
        ProviderNotFoundError,
        TaxmanError,
    )
    from ai_taxman.core.messages import Message, read_messages
    from ai_taxman.core.records import ResponseRecord, RunManifest
    from ai_taxman.core.runner import RunResult, run_audit, run_audit_async


def __getattr__(name: str) -> object:
    """Import the module behind a public name the first time it is asked for."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from importlib import import_module

    value = getattr(import_module(module_name), name)
    globals()[name] = value  # resolved once; later lookups skip __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "__version__",
    # workflow
    "run_audit",
    "run_audit_async",
    "load_audit",
    "read_messages",
    # data
    "AuditConfig",
    "Message",
    "ResponseRecord",
    "RunManifest",
    "RunResult",
    # errors
    "TaxmanError",
    "AuditNotFoundError",
    "ConfigError",
    "MessageFileError",
    "ProviderDependencyError",
    "ProviderError",
    "ProviderNotFoundError",
]
