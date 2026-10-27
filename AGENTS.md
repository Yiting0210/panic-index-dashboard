# AGENTS.md

Guidance for coding agents (Claude Code, Codex, Cursor, ...) working in this repository.

## Project goal

A point-in-time AI market research system:

- composite market sentiment analytics (VIX + CNN Fear & Greed → Panic Index)
- deterministic strategy and position logic
- causal backtesting and forward-return analysis
- FOMC retrieval (statements, minutes, Chair press conferences)
- MCP tools (`mcp_server.py`, `tools/`)
- LLM-generated explanation (`assistant/`, Research Assistant page)

Keep it technically defensible, reproducible, and free of look-ahead leakage.

## Core principles

### 1. Code owns critical decision facts

Critical strategy state is computed by deterministic code, never authored or recomputed by the LLM:

- the user's current position
- strategy path exposure
- add / reduce / hold
- applied position change
- resulting / target position
- cap / floor behavior
- the next deterministic trigger

`assistant/presentation.py` (`DecisionFacts`) builds these facts. The page renders them, and the same object goes to the model as read-only context. The LLM may explain them, but it must not override or recompute them.

### 2. Simplify the architecture instead of validating prose

Do not handle ordinary LLM wording variants with phrasing-specific regexes. Do not grow validators that try to infer whether a sentence means the current position, the strategy path, a hypothetical position, or add / reduce / hold. Earlier versions did this, and it never converged (#18, #28, #31, #33, replaced in #35).

Runtime validation enforces machine-checkable contracts. Add a new runtime guard only when an incorrect authoritative fact could otherwise reach the user. Measure ordinary prose mistakes in evals.

### 3. The user's position is authoritative UI state

For the user's actual current-position decision:

- use the position from the page input (`ResearchAgent.run(..., current_position_pct=...)`);
- never rely on the LLM to extract it from the question text;
- pass it directly to deterministic position logic (`tools.plan_position`).

For hypothetical or comparison scenarios, the model may choose the tool calls, but every input position must stay visible (the scenario table) and testable.

### 4. Preserve point-in-time semantics

Everything on an `as_of` path (`tools/`, `assistant/`, `rag/`, `mcp_server.py`, the evals) may use only information available on that date. Do not introduce:

- interpolation that borrows later prices (the tools forward-fill)
- market observations after `as_of`
- FOMC documents published after `as_of` (documents are indexed by `published_at`)
- normalization or retrieval that sees later data

If a change touches time semantics, add or update a look-ahead test (`tests/test_point_in_time.py`).

The dashboard pages intentionally use full-sample VIX normalization and linear interpolation for retrospective display. Both choices are documented in the README methodology. Do not "fix" them, and do not copy them into the tools.

### 5. Keep the historical strategy path separate from user decisions

Strategy path exposure (`run_backtest.final_position_pct` of the standard backtest) is path-dependent. Never present it as the user's current position, as a personalized target, or as an instruction to jump to that exposure. Use `plan_position` for user-position decisions. `run_backtest(initial_position_pct=...)` is the position at the start of the backtest window, not today's position.

## LLM response architecture

```text
UI inputs (market, as_of, your position)
  -> deterministic decision logic
  -> code-owned decision facts
       -> code-rendered UI
       -> read-only context for the LLM

User question
  -> LLM planner
  -> retrieval / research / what-if tools
  -> evidence
  -> LLM explanation
```

The LLM owns explanation, historical context, qualitative interpretation, risks and caveats, and Fed / FOMC context. Its output schema is `summary`, `why`, `historical_context`, `risks`, `citations`. It does not own position-transition facts.

## Validator rules

`assistant/validator.py` keeps to simple deterministic checks:

- structured output has the expected shape
- placeholder call ids and fields exist
- citations come from this run's `search_fed_docs` results and are not published after `as_of`
- restated critical decision figures are caught by one shallow format rule: a raw percentage in a clause with a position word, or "N of M units"

Do not add finite wording tables unless there is a demonstrated high-severity correctness path that cannot be removed architecturally. One repair turn followed by the deterministic rule-based fallback is acceptable behavior. First-pass perfection is not a goal.

## Placeholder policy

- Keep `{btN.*}` placeholders for model-invoked what-if backtests, whose values no deterministic panel shows.
- Do not require the model to restate standard strategy-state or `plan_position` facts through placeholders when they are already code-rendered.
- Do not expand placeholder coverage just to fix an isolated prose failure.

## Scope control

Keep PRs and issues narrow. Do not fix unrelated problems while working on a focused issue unless correctness requires it. In particular, avoid opportunistic changes to unrelated placeholder fields, wording edge cases, prompt style, strategy math, and threshold calibration. Document known limitations (README "Limitations") instead of expanding scope.

## Testing

Run tests with the project virtualenv:

```bash
.venv/Scripts/python.exe -m pytest -q        # Windows; .venv/bin/python elsewhere
python -m evals.run offline                  # point-in-time tests + consistency + retrieval
python -m evals.run consistency              # offline: decision scenarios + contract probes
python -m evals.run retrieval                # retrieval recall@5 / MRR
```

`python -m evals.run` exits 1 on a correctness failure and 2 on a usage or safety refusal.

Before completing a change:

- run the relevant unit tests;
- run the full suite when shared assistant, strategy, tool, or validator code changes (`tests/test_push_data_update.py` takes about a minute longer on Windows);
- run the offline consistency eval when assistant behavior changes;
- run the look-ahead tests when time-sensitive logic changes.

The offline agent eval (`--mode replay`) needs committed recordings. There are none, so it is skipped and is not a substitute.

## Live model evals

- **They cost money. Ask the user before every live run**, and state the expected cost. Live runs use the `DEEPSEEK_API_KEY` in `.env`, which `python -m evals.run` reads for live commands only.
- Run one for major agent-architecture milestones, after offline tests pass, and only when the user agrees.
- **Make every run fresh.** Use `python -m evals.run agent --provider deepseek --live --fresh` (same for `consistency`). Without `--fresh`, responses replay from `.cache/llm/`. Check that the summary shows `cached_responses: 0 / N`.
- **Run nothing else alongside it.** A concurrent full test suite once made the embedding model fail to load (`bad allocation`) and spoiled a run.
- **Report every run as-is** in the README run history and the PR. Changes made after seeing a run are disclosed and count as unverified until a later fresh run. Re-scoring the same answers is not evidence.
- Do not treat a live run as a reason to patch every wording issue it shows.

## Eval priorities

Report, in roughly this order (field names from `evals/agent_eval.py` and `evals/consistency_eval.py`):

- decision correctness: `decision_correct` / `decision_questions`; consistency `signal_mismatches`
- tool selection: `tool_selection_accuracy`
- `citation_precision`
- `numeric_faithfulness`
- look-ahead leakage: `leakage_rate`
- contract and fallback behavior: `contract_fallbacks`, `repair_turns`, `fallbacks`
- judge groundedness and contradictions (`--judge`): a trend metric, reviewed by hand, since the judge makes mistakes too

Do not optimize for the first-pass contract pass rate.

## Strategy math

Do not change thresholds, position-sizing rules, or backtest semantics unless the task requires it. A strategy-math change must include tests and state whether historical results change.

## Data and secrets

- `data/market_data.csv` is append-only. The daily GitHub Actions job appends to it, and stored values are never overwritten. Do not edit it by hand.
- Never commit `.env` or any API key.

## Pull requests

State the tests run and their results, what was not done or not verified, and any known limitation. Link the issue the PR closes.

## Stop condition for guardrail work

After the code-owned decision refactor (#35), do not patch new natural-language edge cases unless they create a genuine path for an incorrect authoritative decision fact to reach the user. If the code-rendered decision is correct and the remaining issue is prose quality, treat it as an eval / judge concern. Do not grow the runtime validator for it.
