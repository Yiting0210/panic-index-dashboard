"""The Research Assistant page calls a model only with a key typed into it.

Provider keys in the environment (e.g. from .env for the evals) must not be
picked up by the page.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import anthropic  # noqa: E402
import openai  # noqa: E402
import streamlit as st  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

import assistant  # noqa: E402
import utils  # noqa: E402

PAGE = next((Path(__file__).resolve().parents[1] / "pages").glob("3_*Research_Assistant.py"))


class FakeClient:
    """Records the key it was built with; any API call raises."""

    built = []

    def __init__(self, api_key=None, **kwargs):
        FakeClient.built.append(api_key)

    def __getattr__(self, name):
        raise RuntimeError("fake client: no network in tests")


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-key")
    FakeClient.built = []
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    monkeypatch.setattr(assistant, "ResponseCache",
                        lambda: assistant.cache.ResponseCache(tmp_path / "llm"))
    monkeypatch.setattr(assistant, "UsageLedger",
                        lambda **kw: assistant.ledger.UsageLedger(tmp_path / "usage.jsonl", **kw))
    # st.page_link needs the multipage app, which AppTest does not load.
    monkeypatch.setattr(utils, "add_sidebar_nav", lambda: None)
    st.cache_resource.clear()
    at = AppTest.from_file(str(PAGE), default_timeout=300)
    at.run()
    assert not at.exception
    return at


def ask(at, provider, key=""):
    at.selectbox[0].set_value(provider)
    at.run()  # the key field's label, and so its widget, depends on the provider
    at.text_input[0].input(key)
    at.text_area[0].input("What exposure does the strategy hold right now, and why?")
    at.run()  # Ask is disabled until the question is in
    at.button[0].click()
    at.run()
    assert not at.exception
    return at.session_state["last_result"]


@pytest.mark.parametrize("provider", ["Claude (Anthropic)", "DeepSeek"])
def test_no_typed_key_builds_no_client_and_falls_back(page, provider):
    result = ask(page, provider)
    assert FakeClient.built == []
    assert result.fallback_reason == "RuntimeError: no API key entered"
    assert result.answer


@pytest.mark.parametrize("provider", ["Claude (Anthropic)", "DeepSeek"])
def test_typed_key_is_the_only_key_used(page, provider):
    ask(page, provider, key="typed-key")
    assert FakeClient.built == ["typed-key"]
