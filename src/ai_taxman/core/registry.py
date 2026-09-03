"""Finding providers by name, without core ever importing one directly.

Nothing in `core/` or `cli/` may `import ai_taxman.providers.openai`. They ask
this module for a name and get back something implementing `Provider`. Providers
are imported lazily, so listing them never pulls in an optional SDK, and a
broken or uninstalled provider cannot take the rest of the tool down with it.

Third-party providers can register themselves through the `ai_taxman.providers`
entry-point group without living in this package at all.
"""

from __future__ import annotations

import importlib
import pkgutil
from importlib.metadata import EntryPoint, entry_points

from ai_taxman.core.errors import ProviderDependencyError, ProviderNotFoundError
from ai_taxman.providers.base import Provider

ENTRY_POINT_GROUP = "ai_taxman.providers"
PROVIDER_ATTRIBUTE = "PROVIDER"
BUILTIN_PACKAGE = "ai_taxman.providers"

#: Providers registered at runtime (tests, plugins, embedding applications).
_registered: dict[str, Provider] = {}


def register_provider(provider: Provider, *, override: bool = False) -> None:
    """Register `provider` under its own name."""
    key = provider.name.lower()
    if key in _registered and not override:
        raise ValueError(
            f"A provider named {provider.name!r} is already registered. "
            "Pass override=True if that is deliberate."
        )
    _registered[key] = provider


def unregister_provider(name: str) -> None:
    """Remove a registered provider. Unknown names are ignored."""
    _registered.pop(name.lower(), None)


def get_provider(name: str) -> Provider:
    """Return the provider called `name`, importing it if necessary."""
    key = name.lower()

    if key in _registered:
        return _registered[key]

    for loader in (_load_builtin, _load_entry_point):
        provider = loader(key)
        if provider is not None:
            return provider

    available = ", ".join(available_providers()) or "none"
    raise ProviderNotFoundError(f"No provider named {name!r}. Available providers: {available}.")


def available_providers() -> list[str]:
    """Every provider name that could be resolved, sorted.

    Cheap enough for shell completion: it reads package and entry-point
    metadata, and imports nothing.
    """
    names = set(_registered)
    names.update(_builtin_names())
    names.update(point.name.lower() for point in _entry_points())
    return sorted(names)


def _builtin_names() -> list[str]:
    package = importlib.import_module(BUILTIN_PACKAGE)
    return [
        module.name.lower() for module in pkgutil.iter_modules(package.__path__) if module.ispkg
    ]


def _entry_points() -> list[EntryPoint]:
    return list(entry_points(group=ENTRY_POINT_GROUP))


def _load_builtin(key: str) -> Provider | None:
    if key not in _builtin_names():
        return None
    return _provider_from_module(f"{BUILTIN_PACKAGE}.{key}", key)


def _load_entry_point(key: str) -> Provider | None:
    for point in _entry_points():
        if point.name.lower() == key:
            return _as_provider(point.load(), key)
    return None


def _provider_from_module(module_path: str, key: str) -> Provider:
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        # The provider's optional SDK is missing, or its own imports are broken.
        raise ProviderDependencyError(
            f"The {key!r} provider could not be loaded: {exc}. "
            f"If its SDK is missing, add it with `uv add ai-taxman[{key}]`."
        ) from exc

    provider = getattr(module, PROVIDER_ATTRIBUTE, None)
    if provider is None:
        raise ProviderNotFoundError(f"{module_path} does not export a {PROVIDER_ATTRIBUTE} object.")
    return _as_provider(provider, key)


def _as_provider(candidate: object, key: str) -> Provider:
    if isinstance(candidate, type) and issubclass(candidate, Provider):
        candidate = candidate()
    if not isinstance(candidate, Provider):
        raise ProviderNotFoundError(
            f"The {key!r} provider does not implement ai_taxman.providers.base.Provider."
        )
    return candidate
