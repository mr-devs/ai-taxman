"""Exceptions raised by ai-taxman.

Every error here is meant to be shown to a user at the command line, so messages
should say what went wrong *and* what to do about it.
"""


class TaxmanError(Exception):
    """Base class for every error ai-taxman raises deliberately."""


class MessageFileError(TaxmanError):
    """The `.txt` file of messages is missing, unreadable, or empty."""


class AuditChangedError(TaxmanError):
    """An audit no longer matches the run it would resume."""


class RunInProgressError(TaxmanError):
    """Another process is collecting the run this one would resume."""


class ResponseFileError(TaxmanError):
    """A file of collected responses holds a row that cannot be read."""


class NotATaxmanProjectError(TaxmanError):
    """No `taxman.yaml` marker in the working directory or any parent."""


class AuditNotFoundError(TaxmanError):
    """No audit YAML matches the requested name."""


class ConfigError(TaxmanError):
    """An audit YAML is malformed or fails validation."""


class ProviderNotFoundError(TaxmanError):
    """No provider is registered under the requested name."""


class ProviderDependencyError(TaxmanError):
    """A provider's optional SDK is not installed."""


class ProviderError(TaxmanError):
    """A provider could not be prepared to run - missing credentials, say."""


class MissingApiKeyError(TaxmanError):
    """The environment variable an audit names holds no API key."""
