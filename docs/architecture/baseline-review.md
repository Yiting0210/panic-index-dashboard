# Baseline Review

An independent review of the system as it exists today, written by the reviewer
development agent (`agents/reviewer.md`). There is no feature spec, design, or diff
under review, so it records risks in the current code; it proposes no changes and
fixes nothing.

- Date: 2026-11-01
- Branch / commit: `docs/macro-regime-design` at `620f0f0` (production code as on `main`)
- Inputs: `AGENTS.md`, [system-overview.md](system-overview.md), [module-map.md](module-map.md),
  `tests/BASELINE.md` (315 passed, 1 skipped; offline eval passed), and the production code.
- Checks run: read-only Python scripts against the committed CSV through the tool layer
  (no tests, no evals, no model calls). The figures below come from those scripts.

Each finding is tagged **correctness risk**, **technical debt**, or **architectural
preference**, and each claim is marked **FACT** (read in code or measured) or
**ASSUMPTION**.

---

## Critical

None found.

Checked: the as-of path (`tools/market.MarketStore._build_frame` truncates before
`ffill` and `compute_panic_index`; `tools/market.py:46-48`); every market tool reading
through `_frame` (`tools/core.py:74-81`); RAG eligibility inside the search
(`rag/index.py:133-137`, `:163`); citation provenance and date (`assistant/validator.py:341-354`);
the as-of clamp (`assistant/agent.py:324-332`); the page position → `ResearchAgent.run` →
`plan_position` wiring (`pages/3_🤖_Research_Assistant.py:178-191`, `assistant/presentation.py:257-269`);
the answer schema has no decision field (`assistant/agent.py:118-141`); the page never
reads an environment key (`build_agent`, page `:81-102`); `.env` is ignored and not
tracked; append-only merge rules (`ingest/market_update.py:184-250`). FACT: for 47
sampled as-of dates (every 25th QQQ session from mid-2022), `run_backtest(...).final_position_pct`
of the standard backtest equalled the exposure a strictly per-day point-in-time path
would hold, so finding M1 below did not change the displayed strategy-path exposure in
that sample.

## Major

**M1. Tool-layer backtests, forward returns, and the "causal" peak detector use
within-window hindsight normalization.** *Correctness risk.*
- What: `compute_panic_index` normalizes VIX with the min/max of the whole frame it is
  given (`utils/data_loader.py:70-81`). The tools truncate to `as_of` and then normalize
  once (`tools/market.py:46-48`), so the Panic Index of every earlier session `t` in a
  backtest, forward-return mask, or peak scan depends on VIX between `t` and `as_of`.
  `run_backtest` (`tools/core.py:153-159`), `get_forward_returns` (`:292`), and
  `detect_peaks(method="realtime")` (`:351-354`) all consume that series.
- Evidence (FACT, measured): QQQ, as of 2026-10-28, standard window. 73 of 1,211 sessions
  since 2022-01-01 fall in a different regime than they had as of their own date (e.g.
  2022-01-25: Panic Index 63.4 in the 2026 frame, 89.6 as of that day: neutral vs.
  panic). The path exposure differs on 383 of 1,371 sessions, and the strategy return is
  89.98% vs. 79.55% for a per-day point-in-time path.
- Why it matters: the project goal says "causal backtesting"; README calls the threshold
  strategy "causal, backtestable" and says the tools "are not affected" by the
  normalization problem (README *Daily Data Updates*); the realtime
  detector's tool note says "using no later data" (`tools/core.py:354`). Those statements
  are false for any event or session before `as_of`. Users and models are told the
  backtest metrics and peak dates are free of hindsight.
- Rule: no information after `as_of` is used, so this does not break AGENTS.md §4 as
  written; it conflicts with the "causal backtesting" goal and the documented behaviour.
  Not Critical: no data after `as_of`, and the sampled authoritative exposure did not move.
- Note: a remedy changes historical results and the pinned consistency fixtures, so it is
  a strategy-math decision (AGENTS.md *Strategy math*), not a patch.

**M2. The rule-based fallback contradicts the code-rendered panel under non-default
position sizing.** *Correctness risk.*
- What: on fallback, `arun` passes only the thresholds to `rule_based_summary`
  (`assistant/agent.py:394-399`), which runs `_run_backtest` with default initial/add/reduce/cap/floor
  (`tools/core.py:408-419`). The Strategy Path panel above it uses the session settings
  (`assistant/signal.py:113-115`).
- Scenario (FACT, measured): settings add 10, reduce 20, initial 100; QQQ as of 2026-10-28;
  any fallback (no key, API error, guardrail). Panel: strategy +85.9%, strategy max drawdown
  -35.1%. Fallback text on the same page: "the strategy returned 90.0% … maximum drawdown moved
  from -35.1% to -29.6%", plus "average exposure was 62.5%", all for a different strategy.
- Why it matters: the fallback is the safe path the architecture relies on ("one repair turn,
  then the deterministic fallback"); here it puts a second, conflicting set of strategy
  figures, including an exposure figure, next to the code-owned facts (AGENTS.md §1 intent:
  one code source for strategy state). `tests/BASELINE.md` lists this as an ASSUMPTION; it
  is reproducible.

**M3. The `run_backtest` MCP description tells clients to use path exposure for any
position-size question.** *Correctness risk.*
- What: the docstring, which MCP publishes as the tool description, says
  `final_position_pct` is "the exposure the rule holds now" and "Use final_position_pct and
  position_rule for any position-size question" (`tools/core.py:137-140`). `plan_position`'s
  description says the opposite (`:226-229`).
- Scenario (ASSUMPTION on model behaviour; FACT on the text): an MCP client (Claude Code,
  Cursor) is asked "I hold 0% QQQ; what should my position be?" and, following the
  description, answers with the standard path's 100%. Outside the Streamlit page there is
  no code-rendered Decision panel to correct it.
- Rule: AGENTS.md §5 (path exposure is never the user's position or target). The same text
  reaches the assistant's model, where the system prompt and the panel mitigate it.

## Minor

**m1. Strategy-path "Next session" text ignores the cap/floor.** *Correctness risk (mitigated).*
`assistant/signal.py:118-123` renders "the strategy path holds 100%; adds 20% if the Panic
Index closes above 64 … within 0%-100%" when the path is at the cap; `tools/core._next_rule`
(`:195-205`) says "already at the 100% cap". Shown on the page (`pages/3_…:236-237`) and in
the prompt. The "within" range keeps it from being wrong outright. AGENTS.md §1 (next
deterministic trigger is code-owned). FACT.

**m2. Initial exposure outside the floor–cap range, and buy ≤ sell, are not validated.**
*Correctness risk (settings-induced).* The sidebar allows initial 0-100 with cap 50-100 and
floor 0-50 (`utils/sidebar.py:108-112`); `run_backtest` checks only floor ≤ cap
(`tools/core.py:142-143`); `StrategyParams` validates nothing. Scenario (FACT from code, not
run): initial 100, cap 60. Until the first panic session the path shows 100% above a 60% cap;
on that session `min(1.0 + 0.2, 0.6)` lowers exposure, so `latest_action` is "reduce" in a
panic regime and `_action_text` says "panic regime: reduced exposure by 40%". MCP callers can
also pass `buy_threshold < sell_threshold`, where panic silently wins (`_classify`, `:60-71`).

**m3. The MCP server's data-range instruction is frozen at import.** *Correctness risk (low).*
`mcp_server.py:38-40` embeds `store.last_date` once; a long-running HTTP server keeps the old
range after the daily update, though the store itself reloads. `last_date` also counts
Fear & Greed-only weekend rows (`tools/market.py:38-43`). FACT.

**m4. The page footer shows the widget's as-of, not the result's.** *Correctness risk (low).*
After an answer, changing the sidebar date re-renders the old result with a footer "As of
<new date>" (`pages/3_…:194`, `:354-356`). The panels show their own observation date, which
limits confusion. FACT.

**m5. `.gitignore` un-ignores the wrong CSV.** *Technical debt (ops).* `data/*` is ignored and
`!data/market_data.csv` re-included (`.gitignore:18-21`), but the file is
`market_data_clean.csv`. It works only because the file is already tracked; a re-created or
renamed file would be silently ignored by the daily job's `git add`. FACT.

**m6. `build_decision` failures other than `ToolInputError` escape `run()`.** *Correctness
risk (low).* `assistant/agent.py:381-386` catches only `ToolInputError`; anything else raises
out of `run` and the page shows a traceback instead of the fallback, while the loop below
catches broad `RuntimeError`/`ValueError` (`:390`). The two policies disagree. FACT.

## Technical debt

- **The regime rule exists in five places**: `tools/core._classify` (`:60-71`), the inline
  step in `utils/backtest.run_backtest` (`utils/backtest.py:13-17`), `apply_rule_once`
  (`utils/strategy.py:38-55`), `utils/narrative._classify_regime` (`:8-24`), and the forward-return
  mask (`tools/core.py:292`); `utils/signals.build_threshold_masks` is a sixth, unused. A
  threshold-semantics change (e.g. `>=`) must touch all of them.
- **Two next-trigger texts** (m1), and the page parses `_next_rule` prose with
  `split(": ", 1)[1]` (`pages/3_…:260`, `:270`, `:298`), coupling UI layout to a sentence format.
- **Hard-coded coupling of the standard start to the data start**: `STANDARD_BACKTEST_START`
  is 2021-05-17 (`assistant/signal.py:21`) while `rule_based_summary` uses `start_date=None`.
  They agree only because the CSV starts that day.
- **Duplicated constants**: market labels (`tools/market.MARKETS` vs page `MARKET_LABELS`,
  `pages/3_…:18-23`); `CSV_PATH` (noted in BASELINE).
- **Tool layer depends on the UI stack**: `tools` → `utils/__init__` loads Streamlit, Plotly,
  and the sidebar; `tools/market.py` imports the MCP `ToolError`. Documented in
  system-overview; it makes the tools heavier to reuse and test in isolation. *Preference/debt.*
- **Dead code**: `run_backtest_with_signals` (`utils/backtest.py:41`) and
  `build_threshold_masks` are exported but unused.
- **`assistant/agent.py` (~750 lines)** carries two near-duplicate step loops (`_steps` for
  Anthropic and DeepSeek). Changes to repair or clamp behaviour must be made twice. *Preference.*
- **Process-global singletons** (`set_store`, `set_index`) with manual reset; noted in BASELINE.

## Testing gaps

`tests/BASELINE.md` is accurate on the items I checked. Additions and disagreements:

- **No within-window look-ahead test.** `test_tool_outputs_ignore_data_after_as_of` poisons
  rows after `as_of` only, so M1 is invisible to it. Nothing asserts that a backtest step or
  peak event for session `t` is independent of rows in `(t, as_of]`. FACT.
- **Fallback vs. panel consistency** (M2): BASELINE marks the divergence as an ASSUMPTION; it is
  a FACT (figures above). No test runs a fallback with custom sizing.
- **Tool descriptions are prompts and are untested.** Nothing checks MCP descriptions against
  AGENTS.md §5 (M3).
- **Settings validation** (m2): no test for initial outside floor–cap or buy ≤ sell.
- **Signal next-rule at the cap/floor** (m1): only `_next_rule` is tested.
- **`decision_correct` is a code check.** `evals/agent_eval.py:51-75` (`decision_problems`) scores the code-built
  plan, so the paid metric re-measures deterministic code; it could run offline. Not a defect,
  but it should not be read as model evidence.
- **Disagreement on priority:** BASELINE ranks the two rule implementations as the top risk.
  They differ only in 1-dp rounding (`utils/strategy.py:52-53` vs. unrounded fractions),
  which the sidebar's 5- and 10-point steps cannot reach; only odd MCP inputs could. I rate
  it debt, below M1–M3.

## Areas future changes should treat carefully

- **`compute_panic_index` and anything that feeds it.** Normalization choices change every
  regime, the pinned consistency fixtures, and historical results; the dashboard choice is
  intentional (AGENTS.md §4) and must not leak into tools, and any tool change is
  strategy math.
- **Tool docstrings.** They are the MCP descriptions and the assistant's tool prompts; wording
  about `final_position_pct` is an AGENTS.md §5 surface, not documentation.
- **The fallback path** (`arun` exception list, `rule_based_summary` arguments). What reaches
  the fallback, and with which settings, decides what the user sees when the model fails.
- **New date-like tool parameters.** `_clamp_as_of` rewrites only `as_of`; anything else relies
  on `_frame` truncation and needs a perturbation test entry.
- **`StrategyParams` / sidebar ranges.** There is no validation layer; widening a slider can
  create out-of-range states (m2).
- **Signal placeholders** (`{current_exposure}`, etc.) bypass the raw-figure rule by design;
  adding fields there widens what the model can attach to arbitrary prose.
- **The shared response cache on the page** (`shared_resources`): a keyless visitor is served
  any cached answer for an identical request; prompt or decision-block changes alter cache
  keys and replay behaviour.
- **Ingestion and `.gitignore`.** `merge`/`validate` guard the append-only CSV; the ignore rule
  (m5) is one rename away from silently dropping daily data commits.

## Overall assessment

The system is in a sound state to build on. The code-owned decision architecture holds:
the user's position goes from the page to `plan_position` without the model, decision facts
are rendered by code, nothing after `as_of` reaches a tool, retrieval, or citation, and no
Critical issue was found. The biggest risks are a methodology gap rather than a leak (M1:
tool backtests, forward returns, and "causal" peaks normalize with information from later in
the window while the docs call them causal), a fallback that can contradict the code-rendered
panel (M2), and a tool description that steers MCP clients toward the path exposure that
§5 forbids (M3). The scattered copies of the regime rule are the main maintainability
hazard for future strategy changes.
