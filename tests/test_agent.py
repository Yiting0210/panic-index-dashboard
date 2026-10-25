"""Agent loop, guardrails, caching, and cost accounting with a scripted client."""
import json
import os
import sys
from datetime import date

import anthropic
import openai
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


def test_visitor_key_spend_does_not_count_against_owner_budget(tmp_path):
    path = tmp_path / "usage.jsonl"
    owner = UsageLedger(path, daily_budget_usd=0.5)
    visitor = UsageLedger(path, daily_budget_usd=float("inf"), billed_to="user")

    class Usage:
        input_tokens = 1_000_000
        output_tokens = 0
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 0

    visitor.record(Usage, "claude-opus-5-5", 1.0, "a")
    assert owner.spent_today() == 0
    assert owner.check_budget() == pytest.approx(0.5)
    assert visitor.spent_today() == pytest.approx(4.0)
    assert [r["billed_to"] for r in owner.records()] == ["user"]


# ── DeepSeek (OpenAI-compatible) agent ───────────────────────────────────────
DS_USAGE = {"prompt_tokens": 1200, "completion_tokens": 100,
            "prompt_cache_hit_tokens": 200, "prompt_cache_miss_tokens": 1000}


def ds_tool_call(name, arguments, call_id="call_1"):
    return {"model": "deepseek-flash", "usage": DS_USAGE, "choices": [{
        "finish_reason": "tool_calls",
        "message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }]},
    }]}


def ds_final(content, finish="stop"):
    return {"model": "deepseek-flash", "usage": DS_USAGE, "choices": [{
        "finish_reason": finish, "message": {"role": "assistant", "content": content},
    }]}


class FakeDeepSeekResponse:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return self._payload


class ScriptedDeepSeekClient:
    """Mimics openai.OpenAI(...).chat.completions.create."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.chat = self
        self.completions = self

    def create(self, **params):
        self.requests.append(json.loads(json.dumps(params, default=str)))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeDeepSeekResponse(item)


@pytest.fixture
def make_deepseek_agent(tmp_path):
    from assistant import DeepSeekAgent

    def make(responses):
        client = ScriptedDeepSeekClient(responses)
        agent = DeepSeekAgent(
            server=build_server(), client=client,
            cache=ResponseCache(tmp_path / "ds-cache"),
            ledger=UsageLedger(tmp_path / "usage.jsonl", billed_to="user",
                               daily_budget_usd=float("inf")),
        )
        return agent, client
    return make


def test_deepseek_tool_loop_and_final_json(make_deepseek_agent):
    final = json.dumps({"answer": "QQQ was in the panic regime (79.8).",
                        "citations": [], "caveats": "none"})
    agent, client = make_deepseek_agent([
        ds_tool_call("get_regime", {"market": "QQQ", "as_of": "2025-12-31"}),
        ds_final(f"```json\n{final}\n```"),
    ])
    result = agent.run("What regime is QQQ in?", date(2022, 10, 13))

    assert result.fallback_reason is None
    assert result.answer.startswith("QQQ was in the panic regime")
    call = result.tool_calls[0]
    assert call.as_of_clamped and call.arguments["as_of"] == "2022-10-13"
    first, second = client.requests
    assert first["tools"][0]["type"] == "function"
    assert first["extra_body"] == {"thinking": {"type": "disabled"}}
    assert first["messages"][0]["role"] == "system"
    tool_message = second["messages"][-1]
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "call_1"
    assert second["messages"][-2]["tool_calls"][0]["function"]["name"] == "get_regime"
    # Cache misses at the input rate, hits at the cache-read rate.
    assert result.cost_usd == pytest.approx(2 * cost_usd("deepseek-flash", 1000, 100, 200), abs=2e-6)


def test_deepseek_unparseable_final_answer_gets_one_json_retry(make_deepseek_agent):
    agent, client = make_deepseek_agent([
        ds_final("QQQ looks calm."),
        ds_final(json.dumps({"answer": "QQQ is neutral.", "citations": [], "caveats": ""})),
    ])
    result = agent.run("Regime?", date(2022, 10, 13))
    assert result.answer == "QQQ is neutral."
    retry = client.requests[1]
    assert retry["response_format"] == {"type": "json_object"}
    assert "tools" not in retry


def test_deepseek_without_key_falls_back(tmp_path, monkeypatch):
    from assistant import DeepSeekAgent

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    agent = DeepSeekAgent(server=build_server(), cache=ResponseCache(tmp_path / "c"),
                          ledger=UsageLedger(tmp_path / "u.jsonl"))
    result = agent.run("Regime?", date(2022, 10, 13), market="QQQ")
    assert result.fallback_reason == "RuntimeError: no DeepSeek API key configured"
    assert "Panic Index" in result.answer


def test_deepseek_api_error_and_truncation_fall_back(make_deepseek_agent):
    import httpx2

    request = httpx2.Request("POST", "https://api.deepseek.com/chat/completions")
    response = httpx2.Response(401, request=request)
    auth_error = openai.AuthenticationError("invalid key", response=response, body=None)
    agent, _ = make_deepseek_agent([auth_error])
    assert agent.run("Regime?", date(2022, 10, 13)).fallback_reason.startswith("AuthenticationError")

    agent, _ = make_deepseek_agent([ds_final("partial", finish="length")])
    assert "max_tokens" in agent.run("Regime?", date(2022, 10, 13)).fallback_reason


def test_deepseek_pricing_families():
    from assistant.ledger import rates_for

    assert rates_for("deepseek-flash")[1] == "deepseek-flash"
    assert rates_for("deepseek-v4-flash")[1] == "deepseek-flash"
    assert rates_for("deepseek-v5-preview")[1] == "deepseek-v4-pro"


def test_dashboard_strategy_settings_reach_tool_calls(make_agent):
    from utils.strategy import StrategyParams

    strategy = StrategyParams(buy_threshold=60, add_pct=30, reduce_pct=5, max_position_pct=80)
    agent, client = make_agent([
        tool_use_response("run_backtest", {"market": "QQQ", "as_of": "2022-10-13"}),
        tool_use_response("run_backtest", {"market": "QQQ", "as_of": "2022-10-13",
                                           "add_pct": 10}, tool_id="toolu_2"),
        final_response("Done."),
    ])
    result = agent.run("How many units should I hold?", date(2022, 10, 13), strategy=strategy)

    assert "Strategy settings: panic regime above 60" in client.requests[0]["messages"][0]["content"]
    first, what_if = result.tool_calls
    assert first.arguments["buy_threshold"] == 60
    assert first.result["add_pct"] == 30
    assert first.result["max_position_pct"] == 80
    assert first.result["final_position_pct"] <= 80
    # A value the model passes explicitly is a what-if and is kept.
    assert what_if.arguments["add_pct"] == 10
    assert what_if.arguments["reduce_pct"] == 5


def test_without_strategy_settings_tool_defaults_apply(make_agent):
    agent, client = make_agent([
        tool_use_response("run_backtest", {"market": "QQQ", "as_of": "2022-10-13"}),
        final_response("Done."),
    ])
    result = agent.run("Backtest QQQ", date(2022, 10, 13))
    assert "Strategy settings" not in client.requests[0]["messages"][0]["content"]
    assert "add_pct" not in result.tool_calls[0].arguments
    assert result.tool_calls[0].result["reduce_pct"] == 10


def test_strategy_signal_is_computed_by_code_and_sent_to_the_model(make_agent):
    agent, client = make_agent([final_response("The rule holds 20% exposure.")])
    result = agent.run("What exposure does the strategy hold?", date(2024, 8, 5), market="QQQ")

    assert result.fallback_reason is None
    assert (result.signal.final_position_pct, result.signal.action) == (20.0, "add")
    first_message = client.requests[0]["messages"][0]["content"]
    assert "final_position_pct: 20" in first_message
    assert "do not restate it with different numbers" in first_message


def structured_final(answer, claims=(), action="not_stated", key_reasons=()):
    body = {"answer": answer, "key_reasons": list(key_reasons), "citations": [],
            "caveats": "none", "position_claims": list(claims), "current_action": action}
    response = final_response("")
    response["content"][0]["text"] = json.dumps(body)
    return response


BAD = "The framework suggests 60% exposure, so it is now reducing exposure."


def test_contradiction_is_sent_back_once_then_falls_back(make_agent):
    agent, client = make_agent([final_response(BAD), final_response(BAD)])
    result = agent.run("What exposure does the strategy hold?", date(2024, 8, 5), market="QQQ")

    assert result.repairs == 1
    assert len(result.first_violations) == 2
    assert result.fallback_reason.startswith("GuardrailViolation")
    assert len(result.violations) == 2
    assert result.signal.final_position_pct == 20.0  # the code-filled panels still render
    assert "60%" not in result.answer
    repair = client.requests[1]["messages"][-1]
    assert repair["role"] == "user" and "final_position_pct 20" in repair["content"]


def test_repaired_answer_is_kept(make_agent):
    fixed = structured_final("The rule added 20% today and now holds 20% exposure.",
                             claims=[{"value": 20, "role": "current", "what_if": False},
                                     {"value": 20, "role": "add_step", "what_if": False}],
                             action="add")
    agent, _ = make_agent([final_response(BAD), fixed])
    result = agent.run("What exposure does the strategy hold?", date(2024, 8, 5), market="QQQ")

    assert result.fallback_reason is None
    assert result.repairs == 1 and result.first_violations
    assert result.violations == []
    assert "now holds 20% exposure" in result.answer


def test_declared_claims_are_checked_by_role(make_agent):
    # 20 is the current exposure; the cap is 100, so this role is wrong.
    wrong = structured_final("Exposure is 20%.",
                             claims=[{"value": 20, "role": "cap", "what_if": False}])
    agent, _ = make_agent([wrong, wrong])
    result = agent.run("Why?", date(2024, 8, 5), market="QQQ")
    assert any("declared as the exposure cap" in v for v in result.violations)


def test_declared_action_is_checked(make_agent):
    wrong = structured_final("Holding steady.", action="reduce")
    agent, _ = make_agent([wrong, wrong])
    result = agent.run("Why?", date(2024, 8, 5), market="QQQ")
    assert any("current_action 'reduce'" in v for v in result.violations)


def test_undeclared_tool_figure_next_to_exposure_is_not_a_violation(make_agent):
    # The live-run false positive: a return beside "exposure". With claims
    # declared, an undeclared figure fails only if no tool or signal reports it.
    text = (f"The rule holds 20% exposure; the strategy returned "
            f"{8.01}% vs. Buy & Hold.")
    ok = structured_final(text, claims=[{"value": 20, "role": "current", "what_if": False}],
                          action="add")
    agent, _ = make_agent([ok])
    result = agent.run("Why?", date(2024, 8, 5), market="QQQ")
    assert result.violations == [] and result.fallback_reason is None


def test_undeclared_fabricated_figure_still_fails(make_agent):
    lie = structured_final("The rule holds 20% exposure, or 63% exposure in practice.",
                           claims=[{"value": 20, "role": "current", "what_if": False}])
    agent, _ = make_agent([lie, lie])
    result = agent.run("Why?", date(2024, 8, 5), market="QQQ")
    assert any("63%" in v for v in result.violations)


def test_key_reasons_are_parsed_and_validated(make_agent):
    bad = structured_final("Panic regime.", key_reasons=["The rule now holds 45% exposure."])
    agent, _ = make_agent([bad, bad])
    result = agent.run("Why?", date(2024, 8, 5), market="QQQ")
    assert result.violations and "45%" in result.violations[0]


def test_deepseek_repair_turn(make_deepseek_agent):
    good = json.dumps({"answer": "The rule now holds 20% exposure.", "key_reasons": [],
                       "citations": [], "caveats": "none",
                       "position_claims": [{"value": 20, "role": "current", "what_if": False}],
                       "current_action": "add"})
    agent, client = make_deepseek_agent([ds_final(json.dumps({
        "answer": BAD, "key_reasons": [], "citations": [], "caveats": "none"})),
        ds_final(good)])
    result = agent.run("What exposure?", date(2024, 8, 5), market="QQQ")

    assert result.fallback_reason is None and result.repairs == 1
    sent = client.requests[1]["messages"]
    assert sent[-2]["role"] == "assistant" and sent[-1]["role"] == "user"
    assert "contradicts the code-computed strategy signal" in sent[-1]["content"]
