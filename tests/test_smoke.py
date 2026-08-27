from importlib.metadata import version

import ai_taxman


def test_version_is_exposed():
    assert ai_taxman.__version__ == version("ai-taxman")
