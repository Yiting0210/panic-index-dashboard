"""Evaluation metrics and agent-run scoring."""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from assistant import AgentResult, ToolCall
from evals.agent_eval import score_result
from evals.metrics import (collect_numbers, extract_numbers, future_dates,
                           numeric_faithfulness, recall_at_k, reciprocal_rank)


def test_recall_and_mrr():
    retrieved = ["a", "b", "c"]
    assert recall_at_k(retrieved, ["b", "z"], k=2) == 0.5
    assert reciprocal_rank(retrieved, ["c"]) == pytest.approx(1 / 3)
    assert reciprocal_rank(retrieved, ["z"]) == 0


def test_alternative_labels_count_as_one_answer():
    retrieved = ["a", "b", "c"]
    assert recall_at_k(retrieved, [["z", "b"]], k=2) == 1.0
    assert recall_at_k(retrieved, [["z", "c"], "a"], k=2) == 0.5
    assert reciprocal_rank(retrieved, [["z", "b"]]) == 0.5


def test_extract_numbers_skips_years_and_iso_dates():
    text = "On 2022-10-13 the index was 79.8 (92.6th percentile) in 2022, up 1,250 points."
    assert extract_numbers(text) == [79.8, 92.6, 1250.0]


def test_numeric_faithfulness_allows_rounding():
    score, unsupported = numeric_faithfulness([26.7, 55.1, 99.0], [26.69, 55.05])
    assert score == pytest.approx(2 / 3)
    assert unsupported == [99.0]


def test_collect_numbers_walks_nested_results():
    payload = {"a": 1.5, "b": [{"c": 2}], "flag": True, "note": "worth 3.25"}
    assert sorted(collect_numbers(payload)) == [1.5, 2.0, 3.25]


def test_future_dates():
    text = "The Fed cut in September 2024 and on 2024-12-18."
    assert future_dates(text, date(2024, 10, 1)) == [date(2024, 12, 18)]


def make_result(answer, citations, tool_calls):
    return AgentResult(question="q", as_of=date(2022, 3, 18), answer=answer,
                       citations=citations, tool_calls=tool_calls, steps=2)


SEARCH = ToolCall(
    name="search_fed_docs", arguments={"query": "hike", "as_of": "2022-03-18"},
    result={"results": [{"doc_id": "statement-20220316", "published_at": "2022-03-16"}]},
    is_error=False, latency_ms=1.0,
)
REGIME = ToolCall(
    name="get_regime", arguments={"market": "SPX", "as_of": "2022-03-18"},
    result={"data_through": "2022-03-18", "panic_index": 55.31}, is_error=False, latency_ms=1.0,
)
QUESTION = {"id": "t", "question": "What did the Fed decide?", "as_of": date(2022, 3, 18),
            "expected_tools": ["search_fed_docs", "get_regime"]}


def test_score_result_success():
    result = make_result(
        "The FOMC raised rates in March 2022; the Panic Index was 55.3.",
        [{"doc_id": "statement-20220316", "published_at": "2022-03-16"}],
        [SEARCH, REGIME],
    )
    row = score_result(QUESTION, result)
    assert row["success"]
    assert row["citation_precision"] == 1.0
    assert row["numeric_faithfulness"] == 1.0
    assert not row["leaked"]


def test_score_result_flags_hallucinated_citation_number_and_future_date():
    result = make_result(
        "Rates rose to 0.5% and the Fed hiked again in May 2022.",
        [{"doc_id": "statement-20220504", "published_at": "2022-05-04"}],
        [SEARCH, REGIME],
    )
    row = score_result(QUESTION, result)
    assert not row["success"]
    assert row["citation_precision"] == 0.0
    assert row["unsupported_numbers"] == [0.5]
    assert row["leaked"] and row["leaked_dates"] == ["2022-05-01"]


def test_score_result_requires_expected_tools():
    result = make_result("Panic Index 55.31.", [], [REGIME])
    row = score_result(QUESTION, result)
    assert not row["tool_selection_correct"]
    assert not row["success"]


def test_prose_dates_are_not_counted_as_numbers():
    text = "Signals fired on April 9 and Apr 11, 2025, and again on September 18th at 72.1."
    assert extract_numbers(text) == [72.1]
    score, _ = numeric_faithfulness(extract_numbers(text), [72.13])
    assert score == 1.0


def test_trap_question_flags_answers_that_state_the_future_outcome():
    trap = {"id": "trap", "question": "What did the FOMC decide at the September 2024 meeting?",
            "as_of": date(2024, 8, 30), "expected_tools": ["search_fed_docs"],
            "expect_unanswerable": True, "forbidden_phrases": ["50 basis", "4-3/4 to 5"]}
    search = ToolCall(name="search_fed_docs", arguments={"query": "q", "as_of": "2024-08-30"},
                      result={"results": []}, is_error=False, latency_ms=1.0)
    honest = make_result("No statement from that meeting was available as of the cutoff.",
                         [], [search])
    leaky = make_result("The Fed cut rates by 50 basis points.", [], [search])
    honest.as_of = leaky.as_of = date(2024, 8, 30)
    assert score_result(trap, honest)["leaked"] is False
    row = score_result(trap, leaky)
    assert row["leaked"] and row["leaked_phrases"] == ["50 basis"]
    assert not row["success"]


# Scoring false positives found in the first live DeepSeek run (2026-10-25).
def test_month_day_dates_are_not_counted_as_numbers():
    text = "Peaks on 03-07 and 05-09; realtime fired on 06-15/06-21 at 81.66."
    assert extract_numbers(text) == [81.66]


def test_unit_suffix_keeps_the_whole_decimal():
    assert extract_numbers("drag +106.04pp vs cushion -83.87pp, 25bp, 1.5x") == \
        [106.04, -83.87, 25.0, 1.5]


def test_signal_numbers_count_as_sources():
    from assistant import build_strategy_signal

    result = make_result("The signal holds 100% after the latest observation.", [], [])
    result.as_of = date(2022, 3, 25)
    question = {**QUESTION, "as_of": date(2022, 3, 25), "expected_tools": []}
    assert score_result(question, result)["unsupported_numbers"] == [100.0]
    result.signal = build_strategy_signal("QQQ", date(2022, 3, 25))
    assert result.signal.final_position_pct == 100.0
    assert score_result(question, result)["numeric_faithfulness"] == 1.0


def test_two_day_meeting_dates_are_not_counted_as_numbers():
    assert extract_numbers("the March 15–16, 2022 meeting and Jan 25-26 cut 0.25") == [0.25]


POSITION_QUESTION = {"id": "p", "question": "I have no position. What would the rule do today?",
                     "as_of": date(2022, 3, 18), "expected_tools": ["plan_position"],
                     "user_positions": [0]}


def plan_call(position, target):
    return ToolCall(
        name="plan_position",
        arguments={"market": "QQQ", "as_of": "2022-03-18", "current_position_pct": position},
        result={"data_through": "2022-03-18", "current_position_pct": float(position),
                "target_position_pct": float(target), "applied_change_pct": float(target - position)},
        is_error=False, latency_ms=1.0, call_id="pp1",
    )


def test_user_position_question_needs_plan_placeholders():
    raw = "From your {pp1.current_position_pct} position the rule adds today."
    ok = make_result("From your 0% position the rule adds today.", [], [plan_call(0, 20)])
    ok.raw_answer = {"answer": raw, "key_reasons": [], "caveats": "none"}
    row = score_result(POSITION_QUESTION, ok)
    assert row["user_position_problems"] == [] and row["success"]


@pytest.mark.parametrize("tool_calls, raw, problem", [
    ([], "The rule holds {current_exposure} exposure.", "no plan_position call for 0%"),
    ([plan_call(0, 20)], "The rule holds {current_exposure} exposure.", "no {ppN...} placeholder"),
    ([plan_call(0, 20)], "Your target is {current_exposure}; from {pp1.current_position_pct} it adds.",
     "strategy path exposure stated as your position"),
])
def test_user_position_problems(tool_calls, raw, problem):
    result = make_result("text", [], tool_calls)
    result.raw_answer = {"answer": raw, "key_reasons": [], "caveats": "none"}
    row = score_result(POSITION_QUESTION, result)
    assert any(problem in p for p in row["user_position_problems"]) and not row["success"]


def test_contrasting_the_strategy_path_with_your_position_is_fine():
    # Run 7, a14: the path figure sits in its own clause, contrasted with "your account".
    raw = ("From {pp1.current_position_pct} the rule reduces you to {pp1.target_position_pct}. "
           "Separately, the strategy path has already worked down to {current_exposure}; "
           "that is the rule's own path, not a target for your account.")
    result = make_result("text", [], [plan_call(0, 20)])
    result.raw_answer = {"answer": raw, "key_reasons": [], "caveats": "none"}
    assert score_result(POSITION_QUESTION, result)["user_position_problems"] == []
