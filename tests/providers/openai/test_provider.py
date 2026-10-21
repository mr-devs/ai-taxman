import pytest

from ai_taxman.providers.openai.provider import PROVIDER, OpenAIProvider


def comment_above(key, text=None):
    """The comment above `key:` in the template, at any depth, without its `#`s."""
    lines = (text or PROVIDER.render_template()).splitlines()
    index = next(i for i, line in enumerate(lines) if line.lstrip().startswith(f"{key}:"))
    above = []
    for line in reversed(lines[:index]):
        if not line.lstrip().startswith("#"):
            break
        above.insert(0, line.strip().removeprefix("#").strip())
    return above


def test_max_output_tokens_says_what_it_counts():
    assert "reasoning tokens included" in " ".join(comment_above("max_output_tokens"))


def test_a_blank_sampling_setting_leaves_the_models_default():
    assert "Default:  blank (model default)" in comment_above("temperature")


def test_reasoning_effort_lists_every_level_openai_documents():
    assert "Options:  none | minimal | low | medium | high | xhigh | max" in comment_above(
        "reasoning_effort"
    )


def test_include_says_what_each_option_records():
    above = comment_above("include")

    assert any(line.startswith("Options:  web_search_call.action.sources") for line in above)
    assert "Default:  [web_search_call.action.sources]" in above


def test_store_is_off_unless_the_user_turns_it_on():
    assert "Default:  false" in comment_above("store")


def test_the_search_block_says_its_settings_need_web_search():
    above = " ".join(comment_above("search"))

    assert "web_search must be true to use any other setting in this block" in above


@pytest.mark.parametrize(
    "exc_name, retryable",
    [
        ("RateLimitError", True),
        ("APITimeoutError", True),
        ("APIConnectionError", True),
        ("InternalServerError", True),
        ("BadRequestError", False),
        ("AuthenticationError", False),
    ],
)
def test_retryability_matches_the_sdk_error_class(exc_name, retryable):
    exc = _make_openai_error(exc_name)

    assert PROVIDER.is_retryable(exc) is retryable


def _make_openai_error(name):
    """An instance of a real SDK error class, built without an HTTP round trip.

    `__new__` skips the constructors, which want live request/response objects
    from whichever HTTP library the SDK currently vendors.
    """
    import openai

    cls = getattr(openai, name)
    return cls.__new__(cls)


# -- the key comes from core, never from the environment -------------------


async def test_the_client_is_the_one_startup_was_given():
    pytest.importorskip("openai")
    provider = OpenAIProvider()

    await provider.startup(api_key="sk-one")
    try:
        assert provider._require_client().api_key == "sk-one"
    finally:
        await provider.shutdown()
