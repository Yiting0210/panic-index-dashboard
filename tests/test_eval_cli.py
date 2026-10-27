"""The unified eval CLI (evals/run.py): parsing, live-run safety, exit codes.

No test here calls a model provider: provider and judge classes are patched
to raise, and live runs use a stub agent.
"""
import io
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import assistant  # noqa: E402
from assistant import AgentResult, ResponseCache  # noqa: E402
from evals import agent_eval, consistency_eval, judge, retrieval_eval  # noqa: E402
from evals import run  # noqa: E402


class ProviderCalled(AssertionError):
    pass


def _forbidden(*args, **kwargs):
    raise ProviderCalled("a provider or judge client was constructed")


@pytest.fixture
def no_providers(monkeypatch):
    for name in ("ResearchAgent", "DeepSeekAgent"):
        monkeypatch.setattr(assistant, name, _forbidden)
    monkeypatch.setattr(judge, "DeepSeekJudge", _forbidden)


@pytest.fixture
def results_dir(tmp_path, monkeypatch):
    for module in (agent_eval, consistency_eval, retrieval_eval):
        monkeypatch.setattr(module, "RESULTS_DIR", tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def cache_dirs(tmp_path, monkeypatch):
    """Keep live runs away from the real .cache/llm and .cache/llm-runs."""
    default = tmp_path / "llm"
    monkeypatch.setattr("assistant.cache.CACHE_DIR", default)
    monkeypatch.setattr(run, "FRESH_CACHE_ROOT", tmp_path / "llm-runs")
    # The repository's own .env must not supply keys to these tests.
    monkeypatch.setattr(run, "DOTENV", tmp_path / ".env")
    return default


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")


@pytest.fixture
def no_tty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))


class StubAgent:
    """Answers without a model; any call counts as a provider call."""

    def __init__(self, provider, cache_dir=None, answer=True):
        self.provider = provider
        self.model = "stub-model"
        self.cache_dir = cache_dir
        self.answer = answer
        self.ledger = SimpleNamespace(daily_budget_usd=5.00, spent_today=lambda: 0.0)

    async def arun(self, question, as_of, market=None, current_position_pct=None):
        if not self.answer:
            raise ProviderCalled("the agent was asked a question")
        ResponseCache(self.cache_dir).put("stub", {"answer": "x"})
        return AgentResult(question=question, as_of=as_of, answer="",
                           model_responses=2, cached_responses=1)


def use_stub_agent(monkeypatch, answer=True):
    built = []

    def build_agent(mode, provider, cache_dir=None):
        assert mode == "live"
        agent = StubAgent(provider, cache_dir, answer)
        built.append(agent)
        return agent

    monkeypatch.setattr(agent_eval, "build_agent", build_agent)
    return built


def live_summary(**overrides):
    summary = {"provider": "deepseek", "model": "stub-model", "decision_questions": 3,
               "decision_correct": 3, "signal_mismatches": 0, "leakage_rate": 0,
               "task_success_rate": 0.1, "citation_precision": 0.2,
               "numeric_faithfulness": 0.3, "judge_groundedness": 0.1,
               "judge_contradicted": 9, "model_responses": 15, "cached_responses": 0,
               "cache_dir": "runs/x"}
    return {**summary, **overrides}


def stub_suite(monkeypatch, module, summary):
    monkeypatch.setattr(module, "evaluate", lambda agent, judge=None, extra=None: (summary, []))


# ---------------------------------------------------------------- offline

def test_ci_runs_every_offline_suite_without_a_provider(no_providers, results_dir, monkeypatch):
    monkeypatch.setattr(run, "run_point_in_time_tests", lambda: 0)
    assert run.main(["ci"]) == run.PASSED
    assert {p.name for p in results_dir.iterdir()} == {"consistency_offline.json",
                                                         "retrieval.json"}


@pytest.mark.parametrize("command", ["offline", "consistency", "retrieval"])
def test_offline_commands_do_not_build_an_agent(command, no_providers, monkeypatch):
    monkeypatch.setattr(run, "run_point_in_time_tests", lambda: 0)
    monkeypatch.setattr(agent_eval, "build_agent", _forbidden)
    monkeypatch.setattr(consistency_eval, "evaluate",
                        lambda: ({"signal_mismatches": 0, "detection_rate": 1,
                                  "false_positive_rate": 0}, []))
    monkeypatch.setattr(retrieval_eval, "evaluate_configs",
                        lambda: {"hybrid": {"summary": {"leakage_rate": 0}}})
    assert run.main([command]) == run.PASSED


@pytest.mark.parametrize("pit, consistency, leakage", [
    (1, {}, 0),
    (0, {"signal_mismatches": 1}, 0),
    (0, {"detection_rate": 0.9}, 0),
    (0, {"false_positive_rate": 0.1}, 0),
    (0, {}, 0.1),
])
def test_offline_correctness_failures_exit_1(pit, consistency, leakage, monkeypatch):
    summary = {"signal_mismatches": 0, "detection_rate": 1, "false_positive_rate": 0,
               **consistency}
    monkeypatch.setattr(run, "run_point_in_time_tests", lambda: pit)
    monkeypatch.setattr(consistency_eval, "evaluate", lambda: (summary, []))
    monkeypatch.setattr(retrieval_eval, "evaluate_configs",
                        lambda: {"hybrid": {"summary": {"leakage_rate": leakage}}})
    assert run.main(["offline"]) == run.FAILED


# ---------------------------------------------------------------- refusals

@pytest.mark.parametrize("argv", [
    ["agent"],
    ["agent", "--provider", "deepseek"],
    ["agent", "--live"],
    ["consistency", "--judge"],
    ["consistency", "--fresh"],
    ["consistency", "--provider", "claude"],
])
def test_refusals_exit_2_without_a_provider(argv, no_providers, keys, monkeypatch):
    monkeypatch.setattr(agent_eval, "build_agent", _forbidden)
    assert run.main(argv) == run.REFUSED


@pytest.mark.parametrize("argv", [["offline", "--live"], ["retrieval", "--provider", "claude"],
                                  ["agent", "--provider", "openai", "--live"]])
def test_unknown_flags_are_usage_errors(argv):
    with pytest.raises(SystemExit) as exc:
        run.main(argv)
    assert exc.value.code == run.REFUSED


def test_live_run_without_tty_or_yes_is_refused(keys, no_tty, monkeypatch):
    built = use_stub_agent(monkeypatch, answer=False)
    assert run.main(["agent", "--provider", "deepseek", "--live"]) == run.REFUSED
    assert len(built) == 1  # built, but never asked anything


def test_declined_prompt_is_refused(keys, monkeypatch):
    use_stub_agent(monkeypatch, answer=False)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert run.main(["agent", "--provider", "deepseek", "--live"]) == run.REFUSED


@pytest.mark.parametrize("argv, missing", [
    (["agent", "--provider", "claude", "--live", "--yes"], "ANTHROPIC_API_KEY"),
    (["agent", "--provider", "deepseek", "--live", "--yes"], "DEEPSEEK_API_KEY"),
    (["agent", "--provider", "claude", "--live", "--judge", "--yes"], "DEEPSEEK_API_KEY"),
])
def test_missing_key_is_refused_before_any_call(argv, missing, keys, monkeypatch, capsys):
    monkeypatch.delenv(missing)
    monkeypatch.setattr(agent_eval, "build_agent", _forbidden)
    assert run.main(argv) == run.REFUSED
    assert missing in capsys.readouterr().err


def test_live_run_reads_keys_from_dotenv(tmp_path, results_dir, monkeypatch):
    for name in run.KEY_ENV.values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")
    (tmp_path / ".env").write_text(
        "# test keys\nexport DEEPSEEK_API_KEY='from-dotenv'\nANTHROPIC_API_KEY=from-dotenv\n",
        encoding="utf-8")
    use_stub_agent(monkeypatch)
    stub_suite(monkeypatch, agent_eval, live_summary())
    assert run.main(["agent", "--provider", "deepseek", "--live", "--yes"]) == run.PASSED
    assert os.environ["DEEPSEEK_API_KEY"] == "from-dotenv"
    assert os.environ["ANTHROPIC_API_KEY"] == "from-shell"


def test_offline_commands_do_not_read_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.setattr(run, "run_point_in_time_tests", lambda: 0)
    monkeypatch.setattr(consistency_eval, "evaluate",
                        lambda: ({"signal_mismatches": 0, "detection_rate": 1,
                                  "false_positive_rate": 0}, []))
    monkeypatch.setattr(retrieval_eval, "evaluate_configs",
                        lambda: {"hybrid": {"summary": {"leakage_rate": 0}}})
    assert run.main(["offline"]) == run.PASSED
    assert "DEEPSEEK_API_KEY" not in os.environ


def test_claude_without_judge_needs_no_deepseek_key(keys, results_dir, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    use_stub_agent(monkeypatch)
    stub_suite(monkeypatch, agent_eval, live_summary(provider="anthropic"))
    assert run.main(["agent", "--provider", "claude", "--live", "--yes"]) == run.PASSED


# ---------------------------------------------------------------- live runs

def test_claude_maps_to_anthropic_and_keeps_result_file_names(keys, results_dir, monkeypatch):
    built = use_stub_agent(monkeypatch)
    run.main(["agent", "--provider", "claude", "--live", "--yes"])
    run.main(["consistency", "--provider", "claude", "--live", "--yes"])
    assert [agent.provider for agent in built] == ["anthropic", "anthropic"]
    assert {p.name for p in results_dir.glob("*.json")} == {"agent.json",
                                                            "consistency_anthropic.json"}
    summary = json.loads((results_dir / "agent.json").read_text(encoding="utf-8"))["summary"]
    questions = len(agent_eval.load_questions())
    assert summary["model_responses"] == 2 * questions
    assert summary["cached_responses"] == questions


def test_fresh_uses_a_new_cache_and_leaves_the_default_untouched(keys, results_dir, cache_dirs,
                                                                  tmp_path, monkeypatch):
    default = cache_dirs
    default.mkdir()
    (default / "old.json").write_bytes(b'{"kept": true}')
    before = {p.name: p.read_bytes() for p in default.iterdir()}
    built = use_stub_agent(monkeypatch)

    run.main(["agent", "--provider", "deepseek", "--live", "--fresh", "--yes"])
    cache_dir = built[0].cache_dir
    assert cache_dir.parent == tmp_path / "llm-runs"
    assert cache_dir.name.endswith("-deepseek")
    assert (cache_dir / "stub.json").exists()
    assert {p.name: p.read_bytes() for p in default.iterdir()} == before

    run.main(["agent", "--provider", "deepseek", "--live", "--yes"])
    assert built[1].cache_dir == default


def test_live_summary_reports_both_cache_counts(keys, monkeypatch, capsys):
    use_stub_agent(monkeypatch)
    stub_suite(monkeypatch, agent_eval, live_summary(model_responses=15, cached_responses=4))
    run.main(["agent", "--provider", "deepseek", "--live", "--yes"])
    out = capsys.readouterr().out
    assert "cached_responses: 4 / 15" in out
    assert "fresh_responses: 11 / 15" in out
    assert "fresh run: no" in out
    assert run.CONCURRENCY_WARNING in out


@pytest.mark.parametrize("command, module, overrides, code", [
    ("agent", agent_eval, {}, run.PASSED),
    ("agent", agent_eval, {"decision_correct": 2}, run.FAILED),
    ("agent", agent_eval, {"leakage_rate": 0.067}, run.FAILED),
    # Model-quality scores are report-only.
    ("agent", agent_eval, {"citation_precision": 0.0, "judge_groundedness": 0.0,
                           "numeric_faithfulness": 0.0, "task_success_rate": 0.0,
                           "fallbacks": 9, "repair_turns": 9}, run.PASSED),
    ("consistency", consistency_eval, {}, run.PASSED),
    ("consistency", consistency_eval, {"signal_mismatches": 1}, run.FAILED),
    ("consistency", consistency_eval, {"leakage_rate": 0.1}, run.FAILED),
])
def test_live_exit_codes(command, module, overrides, code, keys, monkeypatch):
    use_stub_agent(monkeypatch)
    stub_suite(monkeypatch, module, live_summary(**overrides))
    argv = [command, "--provider", "deepseek", "--live", "--judge", "--yes"]
    monkeypatch.setattr(judge, "DeepSeekJudge", lambda: object())
    assert run.main(argv) == code


def test_help_names_every_mode(capsys):
    with pytest.raises(SystemExit):
        run.main(["agent", "--help"])
    out = capsys.readouterr().out
    for flag in ("--live", "--provider", "--fresh", "--judge", "--yes"):
        assert flag in out
