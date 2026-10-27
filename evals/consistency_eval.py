"""Strategy-consistency evaluation: does the answer agree with the signal?

Two layers:
    offline - no API key (CI). Checks that each fixture's code-owned decision
              (the standard backtest) lands in its expected scenario, and
              that the runtime contract accepts the consistent probe and
              rejects every planted restatement of a position (fabricated
              position, current_equals_*). Contradictory-direction probes are
              reported but not blocked at runtime (#35): the judge measures
              prose that contradicts the decision.
    agent   - asks each fixture's question through the agent and counts
              answers that broke the contract, plus as-of leakage.
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

from evals.agent_eval import RESULTS_DIR, build_agent, score_result

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
            violations = validate_answer(render(text, signal))
            # Direction wording is eval-only now, so the runtime passes it.
            should_pass = kind in ("consistent", "contradictory_direction")
            rows.append({
                "id": fixture["id"],
                "probe": kind,
                "signal_mismatches": signal_mismatches(fixture, signal),
                "correct": bool(violations) != should_pass,
                "violations": violations,
            })
    planted = [r for r in rows if r["probe"] not in ("consistent", "contradictory_direction")]
    clean = [r for r in rows if r["probe"] == "consistent"]
    direction = [r for r in rows if r["probe"] == "contradictory_direction"]
    summary = {
        "fixtures": len(fixtures),
        "signal_mismatches": sum(bool(r["signal_mismatches"]) for r in clean),
        "planted_restatements": len(planted),
        "detection_rate": round(mean(r["correct"] for r in planted), 3),
        "false_positive_rate": round(mean(not r["correct"] for r in clean), 3),
        # Not blocked at runtime by design; listed so the change is visible.
        "direction_probes_not_blocked": sum(r["correct"] for r in direction),
        "direction_probes": len(direction),
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
            "model_responses": result.model_responses,
            "cached_responses": result.cached_responses,
            "answer": result.answer,
            "key_reasons": result.key_reasons,
            "judge": judge_result(result, judge) if judge is not None else None,
        })
    answered = [r for r in rows if r["fallback_reason"] is None]
    summary = {
        "provider": agent.provider,
        "model": agent.model,
        "fixtures": len(rows),
        "signal_mismatches": sum(bool(r["signal_mismatches"]) for r in rows),
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
        "model_responses": sum(r["model_responses"] for r in rows),
        "cached_responses": sum(r["cached_responses"] for r in rows),
    }
    if judge is not None:
        summary.update(summarize_judgements([r["judge"] for r in rows]))
    return summary, rows


def evaluate(agent=None, judge=None, extra=None):
    """Run the offline layer (agent=None) or the agent layer, and write its
    result file; extra joins the summary."""
    fixtures = load_fixtures()
    if agent is None:
        summary, rows = run_offline(fixtures)
        out = RESULTS_DIR / "consistency_offline.json"
    else:
        summary, rows = asyncio.run(run_agent(agent, fixtures, judge))
        out = RESULTS_DIR / f"consistency_{agent.provider}.json"
    summary = {**summary, **(extra or {})}
    print(json.dumps(summary, indent=2))
    RESULTS_DIR.mkdir(exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, default=str) + "\n",
                   encoding="utf-8")
    print(f"Wrote {out}")
    return summary, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["offline", "replay", "record", "live"],
                        default="offline")
    parser.add_argument("--provider", choices=["anthropic", "deepseek"], default="anthropic")
    parser.add_argument("--judge", action="store_true",
                        help="label each statement's grounding with a DeepSeek judge")
    args = parser.parse_args()

    if args.mode == "offline":
        evaluate()
        return
    judge = None
    if args.judge:
        from evals.judge import DeepSeekJudge
        judge = DeepSeekJudge()
    evaluate(build_agent(args.mode, args.provider), judge)


if __name__ == "__main__":
    main()
