"""End-to-end agent evaluation: tool selection, faithfulness, and leakage.

Modes:
    replay  - use committed recordings in evals/recordings (no API key; CI default)
    record  - call the API and (re)write the recordings
    live    - call the API, read/write the local cache only

Usage:
    python -m evals.agent_eval --mode record      # needs ANTHROPIC_API_KEY
    python -m evals.agent_eval --mode record --provider deepseek  # DEEPSEEK_API_KEY
    python -m evals.agent_eval --mode replay
"""
import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from statistics import mean

import yaml

from evals.metrics import (collect_numbers, extract_numbers, future_dates,
                           numeric_faithfulness)

DATASET = Path(__file__).parent / "datasets" / "agent.yaml"
RECORDINGS_DIR = Path(__file__).parent / "recordings"
RESULTS_DIR = Path(__file__).parent / "results"


def load_questions(path=DATASET):
    questions = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["questions"]
    for q in questions:
        if isinstance(q["as_of"], str):
            q["as_of"] = date.fromisoformat(q["as_of"])
        if q.get("expect_unanswerable") and not q.get("forbidden_phrases"):
            raise ValueError(f"{q['id']}: trap questions need forbidden_phrases")
    return questions


def retrieved_docs(result):
    """doc_id -> published_at for every passage search_fed_docs returned."""
    docs = {}
    for call in result.tool_calls:
        if call.name == "search_fed_docs" and call.result and not call.is_error:
            for hit in call.result.get("results", []):
                docs[hit["doc_id"]] = date.fromisoformat(str(hit["published_at"]))
    return docs


def user_position_problems(question, result):
    """How an answer about the user's own position breaks the plan contract (#33).

    For a question with ``user_positions``: each position must go through a
    plan_position call, the answer must state it with {ppN...} placeholders,
    and no clause about "your" position may use the strategy path's
    {current_exposure} as the user's target (a separate clause that contrasts
    the two, "...down to {current_exposure}; not a target for your account",
    is fine).
    """
    from assistant.validator import CLAUSE_BREAK_RE, SENTENCE_RE, YOUR_RE, _segments

    planned = {round(float(c.arguments.get("current_position_pct", -1)), 1)
               for c in result.tool_calls if c.name == "plan_position" and not c.is_error}
    problems = [f"no plan_position call for {p:g}%" for p in question["user_positions"]
                if round(float(p), 1) not in planned]
    raw = result.raw_answer or {}
    texts = [raw.get("answer", ""), *raw.get("key_reasons", []), raw.get("caveats", "")]
    if not any("{pp" in t for t in texts):
        problems.append("no {ppN...} placeholder in the answer")
    for text in texts:
        for sentence in SENTENCE_RE.split(text):
            clauses = [sentence[a:b] for a, b in _segments(sentence, CLAUSE_BREAK_RE)]
            if any(YOUR_RE.search(c) and "{current_exposure}" in c for c in clauses):
                problems.append(f"strategy path exposure stated as your position: {sentence.strip()}")
    return problems


def score_result(question, result):
    as_of = question["as_of"]
    expected = set(question["expected_tools"])
    called = set(result.tool_names)
    docs = retrieved_docs(result)

    cited = [c["doc_id"] for c in result.citations]
    valid_citations = [d for d in cited if d in docs and docs[d] <= as_of]
    citation_precision = len(valid_citations) / len(cited) if cited else 1.0

    source_numbers = extract_numbers(question["question"]) + [
        float(as_of.year), float(as_of.month), float(as_of.day)
    ]
    for call in result.tool_calls:
        source_numbers.extend(collect_numbers(call.result))
        source_numbers.extend(collect_numbers(call.arguments))
    # The code-computed strategy signal is given to the model as facts too.
    if result.signal is not None:
        source_numbers.extend(collect_numbers(asdict(result.signal)))
    faithfulness, unsupported = numeric_faithfulness(
        extract_numbers(result.answer), source_numbers
    )

    question_dates = set(future_dates(question["question"], as_of))
    leaked_dates = [d for d in future_dates(result.answer, as_of) if d not in question_dates]
    leaked_tool_data = [
        call.name for call in result.tool_calls
        if call.result and str(call.result.get("data_through", "")) > as_of.isoformat()
    ]
    leaked_citations = [d for d in cited if d in docs and docs[d] > as_of]
    # Trap questions ask about information released after as_of; stating it
    # (e.g. from the model's own memory) is a leak even without a citation.
    answer_lower = result.answer.lower()
    leaked_phrases = [p for p in question.get("forbidden_phrases", [])
                      if p.lower() in answer_lower]
    leaked = bool(leaked_dates or leaked_tool_data or leaked_citations or leaked_phrases)

    tool_selection = expected <= called
    position_problems = (user_position_problems(question, result)
                         if "user_positions" in question else [])
    success = (
        not position_problems
        and not result.used_fallback
        and bool(result.answer.strip())
        and tool_selection
        and citation_precision == 1.0
        and faithfulness >= 0.9
        and not leaked
    )
    return {
        "id": question["id"],
        "success": success,
        "tool_selection_correct": tool_selection,
        "tools_called": result.tool_names,
        "expected_tools": sorted(expected),
        "citation_precision": round(citation_precision, 3),
        "numeric_faithfulness": round(faithfulness, 3),
        "unsupported_numbers": unsupported,
        "leaked": leaked,
        "leaked_dates": [d.isoformat() for d in leaked_dates],
        "leaked_phrases": leaked_phrases,
        "user_position_problems": position_problems,
        "as_of_clamped_calls": sum(c.as_of_clamped for c in result.tool_calls),
        "steps": result.steps,
        "latency_ms": result.latency_ms,
        "cost_usd": result.cost_usd,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "fallback_reason": result.fallback_reason,
        "repairs": result.repairs,
        "first_violations": result.first_violations,
        "placeholders": sum(r.count for r in result.rendered),
        "answer": result.answer,
    }


def summarize(rows, questions=None):
    position_ids = {q["id"] for q in questions or [] if "user_positions" in q}
    position_rows = [r for r in rows if r["id"] in position_ids]
    return {
        "questions": len(rows),
        "task_success_rate": round(mean(r["success"] for r in rows), 3),
        "tool_selection_accuracy": round(mean(r["tool_selection_correct"] for r in rows), 3),
        "citation_precision": round(mean(r["citation_precision"] for r in rows), 3),
        "numeric_faithfulness": round(mean(r["numeric_faithfulness"] for r in rows), 3),
        "leakage_rate": round(mean(r["leaked"] for r in rows), 3),
        "avg_steps": round(mean(r["steps"] for r in rows), 2),
        "avg_latency_ms": round(mean(r["latency_ms"] for r in rows), 1),
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
        "fallbacks": sum(r["fallback_reason"] is not None for r in rows),
        "repair_turns": sum(r["repairs"] for r in rows),
        "first_pass_violations": sum(bool(r["first_violations"]) or
                                     (r["fallback_reason"] or "").startswith("Guardrail")
                                     for r in rows),
        "rendered_placeholders": sum(r["placeholders"] for r in rows),
        "user_position_questions": len(position_rows),
        "user_position_correct": sum(not r["user_position_problems"] and r["fallback_reason"] is None
                                     for r in position_rows),
    }


async def run_eval(agent, questions, judge=None):
    """Score each question; with a judge, also label each statement's grounding."""
    from evals.judge import judge_result, summarize_judgements

    rows = []
    for q in questions:
        result = await agent.arun(q["question"], q["as_of"], market=q.get("market"))
        row = score_result(q, result)
        if judge is not None:
            row["judge"] = judge_result(result, judge)
        rows.append(row)
    summary = summarize(rows, questions)
    if judge is not None:
        summary.update(summarize_judgements([r["judge"] for r in rows]))
    return summary, rows


def build_agent(mode, provider="anthropic"):
    from assistant import DeepSeekAgent, ResearchAgent, ResponseCache, UsageLedger

    if mode == "live":
        cache = ResponseCache(mode="readwrite")
    else:
        cache = ResponseCache(RECORDINGS_DIR, mode=mode)
    ledger = UsageLedger(RESULTS_DIR / "agent_usage.jsonl", daily_budget_usd=5.00)
    agent_cls = DeepSeekAgent if provider == "deepseek" else ResearchAgent
    return agent_cls(cache=cache, ledger=ledger)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["replay", "record", "live"], default="replay")
    parser.add_argument("--provider", choices=["anthropic", "deepseek"], default="anthropic")
    parser.add_argument("--judge", action="store_true",
                        help="label each statement's grounding with a DeepSeek judge")
    args = parser.parse_args()

    agent = build_agent(args.mode, args.provider)
    judge = None
    if args.judge:
        from evals.judge import DeepSeekJudge
        judge = DeepSeekJudge()
    summary, rows = asyncio.run(run_eval(agent, load_questions(), judge))
    summary = {"provider": agent.provider, "model": agent.model, **summary}
    print(json.dumps(summary, indent=2))
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / ("agent.json" if args.provider == "anthropic"
                         else f"agent_{args.provider}.json")
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, default=str) + "\n",
                   encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
