"""LLM-judge groundedness scoring with a scripted judge (no API key)."""
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from assistant import AgentResult, ToolCall, build_decision  # noqa: E402
from evals.judge import evidence, judge_result, statements, summarize_judgements  # noqa: E402


class ScriptedJudge:
    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return json.dumps({"verdicts": self.verdicts}), 0.001


def make_result(**kwargs):
    decision = build_decision("QQQ", date(2024, 8, 5), current_position_pct=50)
    call = ToolCall("run_backtest", {"market": "QQQ"}, {"final_position_pct": 20.0}, False, 1.0,
                    call_id="bt1")
    defaults = dict(question="q", as_of=date(2024, 8, 5),
                    answer="The rule holds 20% exposure. Panic Index is 83.5.",
                    key_reasons=["The index is above 64."], risks=["none"],
                    decision=decision, signal=decision.signal, tool_calls=[call])
    return AgentResult(**{**defaults, **kwargs})


def test_statements_and_evidence():
    result = make_result(historical_context="Panic days were rare. They clustered in August.",
                         risks=["Hindsight labels.", "Not advice."])
    assert statements(result) == ["The rule holds 20% exposure.", "Panic Index is 83.5.",
                                  "The index is above 64.", "Panic days were rare.",
                                  "They clustered in August.", "Hindsight labels.", "Not advice."]
    text = evidence(result)
    assert "(final_position_pct): 20" in text and "[bt1] run_backtest" in text
    # The judge sees the same decision facts the model got, your-position included.
    assert "current position: 50%" in text


def test_judge_counts_verdicts_and_marks_missing_ones():
    judge = ScriptedJudge([{"id": 1, "verdict": "supported"},
                           {"id": 2, "verdict": "contradicted", "reason": "83.47"},
                           {"id": 3, "verdict": "maybe"}])
    scored = judge_result(make_result(), judge)
    assert (scored["supported"], scored["contradicted"], scored["unjudged"]) == (1, 1, 1)
    assert scored["groundedness"] == 0.5
    assert "1. The rule holds 20% exposure." in judge.prompts[0]

    summary = summarize_judgements([scored, None])
    assert summary["judge_answers"] == 1 and summary["judge_groundedness"] == 0.5
    assert summary["judge_contradicted"] == 1


def test_fallback_answers_are_not_judged():
    assert judge_result(make_result(fallback_reason="RuntimeError: x"), ScriptedJudge([])) is None
