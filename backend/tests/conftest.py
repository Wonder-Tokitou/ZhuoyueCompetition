"""Prevent tests from inheriting developer model-provider credentials."""
import os

import pytest


@pytest.fixture(autouse=True)
def disable_live_model_credentials(monkeypatch):
    """Tests must opt into fake credentials and a mocked transport explicitly."""
    for name in tuple(os.environ):
        if name.startswith("LLM_") or name.startswith("DEEPSEEK_") or name.startswith("TAVILY_"):
            monkeypatch.delenv(name, raising=False)
