"""One entry point for the evaluation suites (#38).

Offline commands never call a model provider:
    python -m evals.run offline        # point-in-time tests + consistency + retrieval
    python -m evals.run ci             # the same suites; what CI runs
    python -m evals.run consistency    # offline consistency only
    python -m evals.run retrieval      # retrieval only

Live commands call a paid provider. They need --live and an explicit
--provider, and ask for confirmation first (--yes skips it; without a TTY,
--yes is required):
    python -m evals.run agent --provider deepseek --live --fresh
    python -m evals.run agent --provider claude --live --fresh --judge
    python -m evals.run consistency --provider claude --live

Live commands read API keys (ANTHROPIC_API_KEY, DEEPSEEK_API_KEY) from .env;
a variable already set in the environment wins. --fresh answers from a new,
empty cache directory under .cache/llm-runs/ and leaves .cache/llm/
untouched. --judge adds the DeepSeek judge, whatever the provider, so it
also needs DEEPSEEK_API_KEY.

Exit codes: 0 passed; 1 a correctness check failed (offline: point-in-time
tests, consistency signal and probes, retrieval leakage; live: decision
correctness or leakage); 2 a usage or safety refusal, before any provider
call. Other live metrics, judge scores included, are report-only.

Record and replay modes stay on the suite modules (python -m evals.agent_eval
--mode ...); this CLI does not expose them.
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRESH_CACHE_ROOT = ROOT / ".cache" / "llm-runs"
# Test-only API keys; read by live commands, never by the app.
DOTENV = ROOT / ".env"
POINT_IN_TIME_TESTS = "tests/test_point_in_time.py"

# CLI provider name -> the name the agents and result files use.
PROVIDERS = {"claude": "anthropic", "deepseek": "deepseek"}
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}
CONCURRENCY_WARNING = "Warning: do not run live evals concurrently with the full test suite."

PASSED, FAILED, REFUSED = 0, 1, 2

# Summary label -> existing summary fields, in the AGENTS.md eval priority
# order. UI-input grounding and what-if input fidelity are both checks inside
# decision_problems, so they are counted in decision_correct.
LABELS = {
    "agent": [
        ("decision correctness", ("decision_correct", "decision_questions")),
        ("tool selection / task success", ("tool_selection_accuracy", "task_success_rate")),
        ("citation precision", ("citation_precision",)),
        ("numeric faithfulness", ("numeric_faithfulness",)),
        ("look-ahead leakage", ("leakage_rate",)),
        ("explanation contract violations", ("first_pass_violations", "contract_fallbacks")),
        ("repairs", ("repair_turns",)),
        ("fallbacks", ("fallbacks",)),
        ("judge", ("judge_groundedness", "judge_contradicted", "judge_unsupported",
                   "judge_unjudged")),
        ("cost", ("total_cost_usd", "judge_cost_usd")),
    ],
    "consistency": [
        ("decision correctness", ("signal_mismatches",)),
        ("look-ahead leakage", ("leakage_rate",)),
        ("explanation contract violations", ("first_pass_violation_rate", "violation_rate")),
        ("repairs", ("repaired",)),
        ("fallbacks", ("other_fallbacks",)),
        ("judge", ("judge_groundedness", "judge_contradicted", "judge_unsupported",
                   "judge_unjudged")),
        ("cost", ("total_cost_usd", "judge_cost_usd")),
    ],
}


class Refused(Exception):
    """A usage or safety refusal; no provider has been called."""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m evals.run", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    commands.add_parser("offline", help="point-in-time tests + consistency + retrieval")
    commands.add_parser("ci", help="the offline suites, as CI runs them")
    commands.add_parser("retrieval", help="retrieval recall@5 / MRR / leakage (offline)")
    for name, help_text in (("consistency", "consistency eval (offline unless --live)"),
                            ("agent", "agent eval (live only)")):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--live", action="store_true",
                         help="call the provider's API (costs money)")
        sub.add_argument("--provider", choices=sorted(PROVIDERS),
                         help="model provider for a live run (required with --live)")
        sub.add_argument("--fresh", action="store_true",
                         help="use a new, empty response cache under .cache/llm-runs/")
        sub.add_argument("--judge", action="store_true",
                         help="also label statements with the DeepSeek judge (live only)")
        sub.add_argument("--yes", action="store_true",
                         help="skip the confirmation prompt")
    return parser


def check_args(args):
    if args.command not in ("consistency", "agent"):
        return
    if args.live:
        if args.provider is None:
            raise Refused("--live needs an explicit --provider (claude or deepseek).")
        return
    if args.command == "agent":
        raise Refused("agent has no offline mode; pass --live --provider claude|deepseek.")
    given = [f"--{flag}" for flag in ("provider", "fresh", "judge", "yes")
             if getattr(args, flag)]
    if given:
        raise Refused(f"{', '.join(given)} only apply to a live run; add --live.")


# ---------------------------------------------------------------- offline

def run_point_in_time_tests():
    return subprocess.run([sys.executable, "-m", "pytest", POINT_IN_TIME_TESTS, "-q"],
                          cwd=ROOT).returncode


def consistency_failures(summary):
    failures = []
    if summary["signal_mismatches"] > 0:
        failures.append(f"consistency: signal_mismatches {summary['signal_mismatches']}")
    if summary["detection_rate"] < 1:
        failures.append(f"consistency: detection_rate {summary['detection_rate']}")
    if summary["false_positive_rate"] > 0:
        failures.append(f"consistency: false_positive_rate {summary['false_positive_rate']}")
    return failures


def retrieval_failures(report):
    return [f"retrieval: {config} leakage_rate {entry['summary']['leakage_rate']}"
            for config, entry in report.items() if entry["summary"]["leakage_rate"] > 0]


def run_offline(suites):
    from evals import consistency_eval, retrieval_eval

    failures = []
    if "point_in_time" in suites and run_point_in_time_tests() != 0:
        failures.append(f"point-in-time tests failed ({POINT_IN_TIME_TESTS})")
    if "consistency" in suites:
        summary, _ = consistency_eval.evaluate()
        failures += consistency_failures(summary)
    if "retrieval" in suites:
        failures += retrieval_failures(retrieval_eval.evaluate_configs())
    return report_failures(failures)


# ---------------------------------------------------------------- live

def fresh_cache_dir(provider):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = FRESH_CACHE_ROOT / f"{stamp}-{provider}"
    if directory.exists():
        raise Refused(f"{directory} already exists; try again.")
    return directory


def load_dotenv(path=None):
    """Set KEY=value lines from .env; variables already in the environment win."""
    path = Path(path or DOTENV)
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.removeprefix("export ").strip()
        os.environ.setdefault(name, value.strip().strip("\"'"))


def check_keys(provider, judge):
    needed = [KEY_ENV[provider]]
    if judge and KEY_ENV["deepseek"] not in needed:
        needed.append(KEY_ENV["deepseek"])
    missing = [name for name in needed if not os.environ.get(name)]
    if missing:
        raise Refused(f"missing {', '.join(missing)}"
                      + (" (the --judge model is DeepSeek)" if judge else "") + ".")


def confirm(args, agent, items, cache_dir):
    ledger = agent.ledger
    spent = ledger.spent_today()
    print(f"Live eval: {args.command}, provider {args.provider} (model {agent.model}), "
          f"{items} {'questions' if args.command == 'agent' else 'fixtures'}")
    if args.judge:
        from evals.judge import JUDGE_MODEL
        print(f"Judge: DeepSeek {JUDGE_MODEL}, billed separately")
    print(f"Cost bound: daily budget ${ledger.daily_budget_usd:.2f}, ${spent:.4f} spent "
          f"today (UTC), so at most ${max(ledger.daily_budget_usd - spent, 0):.2f} more "
          f"for the agent")
    if args.fresh:
        print(f"Cache: {cache_dir} (fresh, empty; .cache/llm/ is not used)")
    else:
        print(f"Cache: {cache_dir} (cached responses may be replayed; use --fresh)")
    print(CONCURRENCY_WARNING)
    if args.yes:
        return
    if not sys.stdin.isatty():
        raise Refused("stdin is not a terminal; pass --yes to confirm a live run.")
    try:
        answer = input("Proceed? [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        raise Refused("live run declined.")


def live_failures(command, summary):
    failures = []
    if command == "agent" and summary["decision_correct"] < summary["decision_questions"]:
        failures.append(f"decision_correct {summary['decision_correct']} / "
                        f"{summary['decision_questions']}")
    if command == "consistency" and summary["signal_mismatches"] > 0:
        failures.append(f"signal_mismatches {summary['signal_mismatches']}")
    if summary["leakage_rate"] > 0:
        failures.append(f"leakage_rate {summary['leakage_rate']}")
    return failures


def print_live_summary(command, summary):
    total, cached = summary["model_responses"], summary["cached_responses"]
    print(f"\n== {command} / {summary['provider']} ({summary['model']}) ==")
    for label, fields in LABELS[command]:
        values = [f"{name}={summary[name]}" for name in fields if summary.get(name) is not None]
        if values:
            print(f"{label}: {', '.join(values)}")
    print(f"cached_responses: {cached} / {total}")
    print(f"fresh_responses: {total - cached} / {total}")
    print(f"fresh run: {'yes' if cached == 0 else 'no'}")
    print(f"cache_dir: {summary['cache_dir']}")


def run_live(args):
    from assistant.cache import CACHE_DIR
    from evals import agent_eval, consistency_eval

    provider = PROVIDERS[args.provider]
    load_dotenv()
    check_keys(provider, args.judge)
    cache_dir = fresh_cache_dir(provider) if args.fresh else CACHE_DIR
    agent = agent_eval.build_agent("live", provider, cache_dir=cache_dir)
    suite = agent_eval if args.command == "agent" else consistency_eval
    items = len(agent_eval.load_questions() if args.command == "agent"
                else consistency_eval.load_fixtures())
    confirm(args, agent, items, cache_dir)

    judge = None
    if args.judge:
        from evals.judge import DeepSeekJudge
        judge = DeepSeekJudge()
    extra = {"cache_dir": str(cache_dir), "fresh_cache": args.fresh}
    summary, _ = suite.evaluate(agent, judge, extra=extra)
    print_live_summary(args.command, summary)
    return report_failures(live_failures(args.command, summary))


# ---------------------------------------------------------------- main

def report_failures(failures):
    if failures:
        print("\nFAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return FAILED
    print("\nPASSED")
    return PASSED


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        check_args(args)
        if args.command in ("offline", "ci"):
            return run_offline({"point_in_time", "consistency", "retrieval"})
        if args.command == "retrieval":
            return run_offline({"retrieval"})
        if args.command == "consistency" and not args.live:
            return run_offline({"consistency"})
        return run_live(args)
    except Refused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return REFUSED


if __name__ == "__main__":
    sys.exit(main())
