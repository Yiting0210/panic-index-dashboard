"""Per-call traces and the context budget (assistant/trace.py)."""
import json
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from assistant.trace import CHARS_PER_TOKEN, SECTIONS, context_budget, context_sections
from evals.agent_eval import score_result, summarize
from test_agent import (ds_final, ds_tool_call, final_response,  # noqa: F401
                        make_agent, make_deepseek_agent, tool_use_response)

DECISION = "Strategy facts (computed by code):\n- regime: panic"


def anthropic_params():
    return {
        "system": "S" * 100,
        "tools": [{"name": "get_regime", "description": "d", "input_schema": {}}],
        "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": {}}},
        "messages": [
            {"role": "user", "content": f"as_of: 2022-10-13\n\n{DECISION}\n\nQuestion: q?"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "t1",
                                               "name": "get_regime", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                          "content": "R" * 40}]},
            {"role": "user", "content": "Fix these violations."},
        ],
    }


def test_anthropic_request_is_split_into_every_section():
    params = anthropic_params()
    sizes = context_sections(params, DECISION)
    assert set(sizes) == set(SECTIONS)
    assert sizes["system"] == 100
    assert sizes["decision_facts"] == len(DECISION)
    first = params["messages"][0]["content"]
    assert sizes["question"] == len(first) - len(DECISION)
    assert sizes["tool_results"] > 40
    assert sizes["assistant_turns"] > 0
    assert sizes["follow_ups"] == len("Fix these violations.")
    assert sizes["tool_schemas"] > 0 and sizes["output_schema"] > 0


def test_openai_format_counts_system_message_and_tool_role():
    params = {
        "messages": [
            {"role": "system", "content": "S" * 50},
            {"role": "user", "content": "Question: q?"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": "R" * 30},
        ],
        "tools": [{"type": "function", "function": {"name": "get_regime"}}],
    }
    sizes = context_sections(params)
    assert sizes["system"] == 50
    assert sizes["tool_results"] == 30
    assert sizes["decision_facts"] == 0
    assert sizes["question"] == len("Question: q?")
    assert sizes["output_schema"] == 0


def test_decision_block_not_in_the_message_is_not_counted():
    sizes = context_sections({"messages": [{"role": "user", "content": "Question: q?"}]},
                             "a block that was never sent")
    assert sizes["decision_facts"] == 0


def test_claude_run_traces_every_model_call(make_agent):
    agent, client = make_agent([
        tool_use_response("get_regime", {"market": "QQQ", "as_of": "2022-10-13"}),
        final_response("QQQ was in the panic regime."),
    ])
    result = agent.run("What regime is QQQ in?", date(2022, 10, 13), market="QQQ",
                       current_position_pct=0)

    assert [t.step for t in result.trace] == [1, 2]
    assert not any(t.cached for t in result.trace)
    first, second = result.trace
    assert first.input_tokens == 1000 and first.output_tokens == 200
    # The decision facts the model saw are measured on every call.
    expected = len(result.decision.prompt_block())
    assert first.sections["decision_facts"] == expected
    assert second.sections["decision_facts"] == expected
    # Only the second request carries the tool result back.
    assert first.sections["tool_results"] == 0 < second.sections["tool_results"]
    assert first.sections["system"] == len(client.requests[0]["system"])


def test_replayed_run_is_traced_as_cached(make_agent):
    script = [tool_use_response("get_regime", {"market": "QQQ", "as_of": "2022-10-13"}),
              final_response("Panic regime.")]
    agent, _ = make_agent(list(script))
    first = agent.run("What regime is QQQ in?", date(2022, 10, 13))
    second = agent.run("What regime is QQQ in?", date(2022, 10, 13))
    assert [t.cached for t in second.trace] == [True, True]
    # Cached calls keep their recorded size, so replays still show the budget.
    assert [t.input_tokens for t in second.trace] == [t.input_tokens for t in first.trace]
    assert [t.sections for t in second.trace] == [t.sections for t in first.trace]


def test_fallback_run_keeps_the_trace_of_calls_made(make_agent):
    agent, _ = make_agent([
        tool_use_response("get_regime", {"market": "QQQ", "as_of": "2022-10-13"}),
        RuntimeError("provider down"),
    ])
    result = agent.run("What regime is QQQ in?", date(2022, 10, 13))
    assert result.used_fallback
    assert len(result.trace) == 1


def test_deepseek_run_maps_cache_hits_and_tool_role(make_deepseek_agent):
    final = json.dumps({"summary": "Neutral.", "why": [], "historical_context": "",
                        "risks": [], "citations": []})
    agent, _ = make_deepseek_agent([
        ds_tool_call("get_regime", {"market": "QQQ", "as_of": "2022-10-13"}),
        ds_final(final),
    ])
    result = agent.run("Regime?", date(2022, 10, 13))
    first, second = result.trace
    assert (first.input_tokens, first.cache_read_tokens) == (1000, 200)
    assert first.sections["system"] > 0 and first.sections["output_schema"] == 0
    assert first.sections["tool_results"] == 0 < second.sections["tool_results"]


def test_eval_rows_carry_the_trace_and_summary_has_a_budget(make_agent):
    agent, _ = make_agent([
        tool_use_response("get_regime", {"market": "QQQ", "as_of": "2022-10-13"}),
        final_response("Panic regime."),
    ])
    result = agent.run("What regime is QQQ in?", date(2022, 10, 13))
    question = {"id": "t", "question": "What regime is QQQ in?",
                "as_of": date(2022, 10, 13), "expected_tools": ["get_regime"]}
    row = score_result(question, result)
    json.dumps(row, default=str)  # the result file must stay serializable
    assert len(row["trace"]) == 2
    assert row["trace"][0]["estimated_tokens"]["system"] == round(
        row["trace"][0]["sections"]["system"] / CHARS_PER_TOKEN)

    budget = summarize([row])["context_budget"]
    assert budget["model_calls"] == 2
    assert budget["mean_input_tokens"] == 1000
    assert sum(s["share"] for s in budget["sections"].values()) == pytest.approx(1, abs=0.01)


def test_empty_budget():
    assert context_budget([]) == {"model_calls": 0, "sections": {}}
