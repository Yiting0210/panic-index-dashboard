"""Agent loop, guardrails, caching, and cost accounting with a scripted client."""
import json
import os
import sys
from datetime import date

import anthropic
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from assistant import (BudgetExceeded, ResearchAgent, ResponseCache,
                       UsageLedger, cost_usd)
from assistant.cache import CacheMiss, request_hash
from mcp_server import build_server

USAGE = {"input_tokens": 1000, "output_tokens": 200,
         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return self._payload


def tool_use_response(name, arguments, tool_id="toolu_1"):
    return {
        "model": "claude-opus-5-5", "stop_reason": "tool_use", "usage": USAGE,
        "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": arguments}],
    }


def final_response(answer, citations=()):
    body = {"answer": answer, "citations": list(citations), "caveats": "none"}
    return {
        "model": "claude-opus-5-5", "stop_reason": "end_turn", "usage": USAGE,
        "content": [{"type": "text", "text": json.dumps(body)}],
    }


class ScriptedClient:
    """Mimics client.beta.messages.create, returning scripted responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = self
        self.messages = self

    def create(self, **params):
        self.requests.append(json.loads(json.dumps(params, default=str)))
        if not self.responses:
            raise AssertionError("unexpected extra API call")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)


@pytest.fixture
def make_agent(tmp_path):
    def make(responses, budget=1.0, cache_mode="readwrite"):
        client = ScriptedClient(responses)
        agent = ResearchAgent(
            server=build_server(), client=client,
            cache=ResponseCache(tmp_path / "cache", mode=cache_mode),
            ledger=UsageLedger(tmp_path / "usage.jsonl", daily_budget_usd=budget),
        )
        return agent, client
    return make


def test_tool_loop_returns_structured_answer(make_agent):
    agent, client = make_agent([
        tool_use_response("get_regime", {"market": "QQQ", "as_of": "2022-10-13"}),
        final_response("QQQ was in the panic regime with a Panic Index of 79.8."),
    ])
    result = agent.run("What regime is QQQ in?", date(2022, 10, 13))

    assert result.fallback_reason is None
    assert result.tool_names == ["get_regime"]
    assert result.tool_calls[0].result["regime"] == "panic"
    assert "79.8" in result.answer
    assert result.steps == 2
    # The tool result is sent back on the second request.
    tool_result = client.requests[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert '"regime": "panic"' in tool_result["content"]


def test_request_uses_structured_output_caching_and_fallbacks(make_agent):
    agent, client = make_agent([final_response("No tools needed.")])
    agent.run("Hello", date(2022, 10, 13))
    request = client.requests[0]
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert request["cache_control"] == {"type": "ephemeral"}
    assert request["fallbacks"] == "default"
    assert [t["name"] for t in request["tools"]] == sorted(t["name"] for t in request["tools"])


def test_later_as_of_requested_by_model_is_clamped(make_agent):
    agent, _ = make_agent([
        tool_use_response("get_regime", {"market": "QQQ", "as_of": "2025-12-31"}),
        final_response("Done."),
    ])
    result = agent.run("What regime is QQQ in?", date(2022, 10, 13))
    call = result.tool_calls[0]
    assert call.as_of_clamped
    assert call.arguments["as_of"] == "2022-10-13"
    assert call.result["data_through"] <= "2022-10-13"


def test_identical_request_is_served_from_cache_at_zero_cost(make_agent, tmp_path):
    script = [
        tool_use_response("get_regime", {"market": "QQQ", "as_of": "2022-10-13"}),
        final_response("Panic regime."),
    ]
    agent, client = make_agent(list(script))
    first = agent.run("What regime is QQQ in?", date(2022, 10, 13))
    second = agent.run("What regime is QQQ in?", date(2022, 10, 13))

    assert len(client.requests) == 2  # second run made no API calls
    assert first.cost_usd == pytest.approx(2 * cost_usd("claude-opus-5-5", 1000, 200))
    assert second.cost_usd == 0
    assert second.cached_responses == 2
    assert second.answer == first.answer


def test_replay_mode_raises_on_unrecorded_request(make_agent):
    agent, _ = make_agent([], cache_mode="replay")
    with pytest.raises(CacheMiss):
        agent.run("Unrecorded question", date(2022, 10, 13))


def test_exhausted_budget_falls_back_to_rule_based_summary(make_agent, tmp_path):
    agent, client = make_agent([final_response("unused")], budget=0.0)
    result = agent.run("What regime is QQQ in?", date(2022, 10, 13), market="QQQ")
    assert client.requests == []
    assert result.fallback_reason.startswith("BudgetExceeded")
    assert "Panic Index" in result.answer


def test_api_error_falls_back(make_agent):
    error = anthropic.APIConnectionError(request=None)
    agent, _ = make_agent([error])
    result = agent.run("What regime is SMH in?", date(2023, 12, 29), market="SMH")
    assert result.used_fallback
    assert "Semiconductors (SMH)" in result.answer


def test_missing_credentials_fall_back(make_agent):
    auth_error = TypeError('"Could not resolve authentication method. Expected one of api_key"')
    agent, _ = make_agent([auth_error])
    result = agent.run("What regime is QQQ in?", date(2022, 10, 13), market="QQQ")
    assert result.fallback_reason == "RuntimeError: no Anthropic credentials configured"


def test_refusal_falls_back(make_agent):
    refusal = {"model": "claude-opus-5-5", "stop_reason": "refusal", "usage": USAGE, "content": []}
    agent, _ = make_agent([refusal])
    result = agent.run("anything", date(2023, 12, 29))
    assert result.used_fallback


def test_ledger_budget_and_pricing(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.jsonl", daily_budget_usd=0.01)

    class Usage:
        input_tokens = 2000
        output_tokens = 500
        cache_read_input_tokens = 10000
        cache_creation_input_tokens = 0

    record = ledger.record(Usage, "claude-opus-5-5", 120.0, "abc")
    assert record.cost_usd == pytest.approx((2000 * 4 + 500 * 20 + 10000 * 0.2) / 1e6)
    assert ledger.spent_today() == pytest.approx(record.cost_usd)
    with pytest.raises(BudgetExceeded):
        ledger.check_budget()


def test_request_hash_is_order_independent():
    assert request_hash({"a": 1, "b": [1, 2]}) == request_hash({"b": [1, 2], "a": 1})
    assert request_hash({"a": 1}) != request_hash({"a": 2})


def test_unlisted_model_is_priced_conservatively_without_crashing(tmp_path):
    from assistant.ledger import FALLBACK_RATES

    ledger = UsageLedger(tmp_path / "usage.jsonl")

    class Usage:
        input_tokens = 1_000_000
        output_tokens = 0
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 0

    record = ledger.record(Usage, "claude-some-future-model", 1.0, "abc")
    assert record.cost_usd == pytest.approx(FALLBACK_RATES["input"])
    assert record.priced_as == "unlisted-model-max-rate"


def test_response_from_fallback_model_is_recorded(make_agent):
    served_by_fallback = final_response("Answered after a fallback.")
    served_by_fallback["model"] = "claude-opus-4-8"
    agent, _ = make_agent([served_by_fallback])
    result = agent.run("anything", date(2023, 12, 29))
    assert result.fallback_reason is None
    assert result.model == "claude-opus-4-8"
    assert result.cost_usd == pytest.approx(cost_usd("claude-opus-4-8", 1000, 200))


def test_budget_counts_records_from_other_processes_incrementally(tmp_path):
    path = tmp_path / "usage.jsonl"
    reader = UsageLedger(path, daily_budget_usd=1.0)
    writer = UsageLedger(path, daily_budget_usd=1.0)

    class Usage:
        input_tokens = 100_000
        output_tokens = 0
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 0

    assert reader.spent_today() == 0
    writer.record(Usage, "claude-opus-5-5", 1.0, "a")
    assert reader.spent_today() == pytest.approx(0.4)
    writer.record(Usage, "claude-opus-5-5", 1.0, "b")
    assert reader.spent_today() == pytest.approx(0.8)
    path.write_text("")  # rotated log
    assert reader.spent_today() == 0
