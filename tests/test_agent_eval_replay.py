"""Replays the committed agent-eval recordings (no API key needed).

Recordings are produced with `python -m evals.agent_eval --mode record`.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from evals.agent_eval import RECORDINGS_DIR, build_agent, load_questions, run_eval  # noqa: E402

pytestmark = pytest.mark.skipif(
    not RECORDINGS_DIR.exists() or not any(RECORDINGS_DIR.glob("*.json")),
    reason="no agent-eval recordings committed yet",
)


def test_replayed_agent_runs_have_zero_leakage():
    summary, rows = asyncio.run(run_eval(build_agent("replay"), load_questions()))
    assert summary["leakage_rate"] == 0, [r["id"] for r in rows if r["leaked"]]
    assert summary["fallbacks"] == 0
    assert summary["citation_precision"] == 1.0
