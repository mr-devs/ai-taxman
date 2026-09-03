"""The OpenAI provider package.

`PROVIDER` is what `ai_taxman.core.registry` looks for.
"""

from ai_taxman.providers.openai.provider import PROVIDER, OpenAIProvider

__all__ = ["PROVIDER", "OpenAIProvider"]
