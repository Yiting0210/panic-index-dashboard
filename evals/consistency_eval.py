"""Strategy-consistency evaluation: does the answer agree with the signal?

Two layers:
    offline - no API key (CI). Checks that each fixture's standard backtest
              lands in its expected scenario, and that the validator accepts
              the consistent probe answer and rejects every planted
              contradiction (fabricated position, contradictory direction).
    agent   - asks each fixture's question through the agent and counts
              answers the validator rejected, plus as-of leakage.
              --mode record/live calls the real API; replay uses recordings.

Usage:
    python -m evals.consistency_eval                                # offline
    python -m evals.consistency_eval --mode record --provider anthropic
    python -m evals.consistency_eval --mode record --provider deepseek
"""
import argparse
import asyncio
import json
from datetime import date
from pathlib import Path
from statistics import mean

import yaml

from evals.agent_eval import RECORDINGS_DIR, RESULTS_DIR, score_result

DATASET = Path(__file__).parent / "datasets" / "consistency.yaml"


def load_fixtures(path=DATASET):
    fixtures = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["fixtures"]
    for fixture in fixtures:
        if isinstance(fixture["as_of"], str):
            fixture["as_of"] = date.fromisoformat(fixture["as_of"])
    return fixtures


def signal_mismatches(fixture, signal):
    """Expected signal fields that the standard backtest did not reproduce."""
    actual = {"regime": signal.regime, "final_position_pct": signal.final_position_pct,
              "action": signal.action}
    return {key: {"expected": value, "actual": actual[key]}
            for key, value in fixture["expect"].items() if actual[key] != value}


def run_offline(fixtures):
    """Fixture scenarios and validator probes; needs no API key."""
    from assistant import build_strategy_signal, render, validate_answer

    rows = []
    for fixture in fixtures:
        signal = build_strategy_signal(fixture["market"], fixture["as_of"])
        for kind, text in fixture["probes"].items():
            # Probes are written like model output: placeholders, then rendering.
            violations = validate_answer(render(text, signal), signal)
            should_pass = kind == "consistent"
            rows.append({
                "id": fixture["id"],
                "probe": kind,
                "signal_mismatches": signal_mismatches(fixture, signal),
                "correct": bool(violations) != should_pass,
                "violations": violations,
            })
    planted = [r for r in rows if r["probe"] != "consistent"]
    clean = [r for r in rows if r["probe"] == "consistent"]
    summary = {
        "fixtures": len(fixtures),
        "signal_mismatches": sum(bool(r["signal_mismatches"]) for r in clean),
        "planted_contradictions": len(planted),
        "detection_rate": round(mean(r["correct"] for r in planted), 3),
        "false_positive_rate": round(mean(not r["correct"] for r in clean), 3),
    }
    return summary, rows


async def run_agent(agent, fixtures, judge=None):
    from evals.judge import judge_result, summarize_judgements

    rows = []
    for fixture in fixtures:
        result = await agent.arun(fixture["question"], fixture["as_of"],
                                  market=fixture["market"])
        scored = score_result({**fixture, "expected_tools": []}, result)
        rows.append({
            "id": fixture["id"],
            "signal_mismatches": (signal_mismatches(fixture, result.signal)
                                  if result.signal else {"signal": "missing"}),
            "violations": result.violations,
            "first_violations": result.first_violations,
            "repairs": result.repairs,
            "placeholders": sum(r.count for r in result.rendered),
            "fallback_reason": None if result.violations else result.fallback_reason,
            "leaked": scored["leaked"],
            "unsupported_numbers": scored["unsupported_numbers"],
            "cost_usd": result.cost_usd,
            "answer": result.answer,
            "key_reasons": result.key_reasons,
            "judge": judge_result(result, judge) if judge is not None else None,
        })
    answered = [r for r in rows if r["fallback_reason"] is None]
    summary = {
        "provider": agent.provider,
        "model": agent.model,
        "fixtures": len(rows),
        "answered": len(answered),
        # Before the repair turn, and after it (an answer still violating is
        # replaced by the rule-based summary).
        "first_pass_violation_rate": round(mean(bool(r["first_violations"] or r["violations"])
                                                for r in answered), 3) if answered else None,
        "violation_rate": round(mean(bool(r["violations"]) for r in answered), 3)
        if answered else None,
        "repaired": sum(r["repairs"] > 0 and not r["violations"] for r in answered),
        "rendered_placeholders": sum(r["placeholders"] for r in rows),
        "leakage_rate": round(mean(r["leaked"] for r in rows), 3),
        "other_fallbacks": len(rows) - len(answered),
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
    }
    if judge is not None:
        summary.update(summarize_judgements([r["judge"] for r in rows]))
    return summary, rows


def build_agent(mode, provider):
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
    parser.add_argument("--mode", choices=["offline", "replay", "record", "live"],
                        default="offline")
    parser.add_argument("--provider", choices=["anthropic", "deepseek"], default="anthropic")
    parser.add_argument("--judge", action="store_true",
                        help="label each statement's grounding with a DeepSeek judge")
    args = parser.parse_args()

    fixtures = load_fixtures()
    if args.mode == "offline":
        summary, rows = run_offline(fixtures)
        out = RESULTS_DIR / "consistency_offline.json"
    else:
        judge = None
        if args.judge:
            from evals.judge import DeepSeekJudge
            judge = DeepSeekJudge()
        summary, rows = asyncio.run(run_agent(build_agent(args.mode, args.provider), fixtures,
                                              judge))
        out = RESULTS_DIR / f"consistency_{args.provider}.json"
    print(json.dumps(summary, indent=2))
    RESULTS_DIR.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, default=str) + "\n",
                   encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
