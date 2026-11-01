# Test baseline

Baseline assessment of the current test and eval protection, written by the tester development agent (`agents/tester.md`). It records what is protected today and where the gaps are. It adds no tests and changes no code.

Labels: **FACT** (cites a file or test), **ASSUMPTION**, **RECOMMENDATION**. Unlabelled table rows are facts read from the cited tests.

## Baseline status

- Date: 2026-11-01
- Branch: `docs/macro-regime-design` (clean working tree, HEAD `620f0f0`)
- Platform: Windows 11, project `.venv` (Python 3.12)

| Command | Result |
|---|---|
| `.venv/Scripts/python.exe -m pytest -q` | **315 passed, 1 skipped**, 0 failed, 125.5 s. The skip is `tests/test_agent_eval_replay.py` (no committed recordings). |
| `.venv/Scripts/python.exe -m evals.run offline` | **PASSED**, exit 0. Point-in-time tests 15 passed. Consistency: 10 fixtures, `signal_mismatches` 0, 14 planted restatements, `detection_rate` 1, `false_positive_rate` 0, direction probes not blocked 10/10. Retrieval (30 questions, k=5): hybrid recall@5 0.9333, MRR 0.7528; dense 0.7333 / 0.4944; bm25 0.7333 / 0.5833; hybrid_no_date 0.6667 / 0.5333; `leakage_rate` 0 in every config. |

The two runs were sequential. No live eval was run. The offline run rewrote `evals/results/consistency_offline.json` and `evals/results/retrieval.json`; `git status` stayed clean afterwards.

FACT: CI (`.github/workflows/ci.yml`) runs `py_compile` on the four Streamlit entry points and `mcp_server.py`, then `pytest -q`, then `python -m evals.run ci` (same suites as `offline`). The daily job (`daily-update.yml`) runs `pytest -q` against freshly appended data before committing.

## Critical behaviors and their current coverage

Strength: **direct** = a test asserts this behavior; **partial** = asserted for some inputs or paths only; **incidental** = exercised but not asserted; **none**.

| Behavior | AGENTS.md ref | Tests / evals | Strength |
|---|---|---|---|
| Market tools ignore rows after `as_of` (poisoned CSV) | §4 | `test_point_in_time.py::test_tool_outputs_ignore_data_after_as_of` (get_regime, run_backtest, plan_position, get_forward_returns, detect_peaks realtime + scipy; 4 dates) | direct |
| `data_through <= as_of` | §4 | `test_point_in_time.py::test_results_report_data_through_on_or_before_as_of` | direct |
| Forward fill, never interpolation, in tools | §4 | `test_point_in_time.py::test_forward_fill_never_borrows_future_prices`, `test_full_sample_normalization_would_leak` | direct |
| Forward returns use only completed windows | §4 | `test_point_in_time.py::test_forward_returns_only_use_completed_windows` | direct |
| RAG never returns docs published after `as_of`; minutes dated by release | §4 | `test_rag.py::test_search_never_returns_documents_after_as_of` (dense/bm25/hybrid), `test_minutes_are_dated_by_release_not_meeting`, `test_prefiltering_fills_k_...`; `test_retrieval_eval.py::test_leakage_sweep_across_queries` (real index) | direct |
| Citations come from this run and predate `as_of` | Validator rules | `test_guardrails.py::test_citations_must_come_from_this_run_and_predate_as_of`; `test_agent.py::test_citation_not_returned_this_run_is_a_violation` | direct |
| Model-requested later `as_of` is clamped | §4 | `test_agent.py::test_later_as_of_requested_by_model_is_clamped`, `test_deepseek_tool_loop_and_final_json` | direct (only for a later date; unparsable/missing `as_of` path not tested) |
| User position comes from the page input, not the model | §3 | `test_agent.py::test_page_position_input_reaches_plan_position_without_the_model`, `test_page_position_uses_session_settings` | direct at agent level; page → agent wiring not asserted |
| `plan_position` applies the rule once | §1, §3 | `test_position_plan.py::test_apply_rule_once`, `test_plan_position_applies_the_latest_regime_once`, `test_plan_rejects_positions_outside_the_rule_range` | direct |
| Cap / floor and `boundary_state` | §1 | `test_position_plan.py::test_boundary_state`, `test_apply_rule_once` (clip cases); `test_strategy.py::test_position_never_exceeds_max`, `test_position_never_below_min` | direct |
| Next deterministic trigger | §1 | `test_position_plan.py::test_next_rule_names_the_cap_and_floor` | direct (default settings only) |
| `DecisionFacts` content and prompt block | §1 | `test_position_plan.py::test_decision_from_the_page_input`, `test_decision_without_a_page_input_has_no_plan`, `test_decision_uses_session_settings`; `test_agent.py::test_decision_facts_are_computed_by_code_and_sent_to_the_model` | direct |
| Strategy path kept separate from user decision | §5 | `test_position_plan.py::test_plan_ignores_the_strategy_path_position`, `test_decision_from_the_page_input` | direct |
| `apply_rule_once` agrees with the inline rule in `utils/backtest.py` | Strategy math | `test_position_plan.py::test_plan_from_the_paths_previous_position_matches_the_backtest_step` (5 real QQQ dates, default params) | partial |
| Standard signal (fixed start, session settings) | §5 | `test_guardrails.py::test_signal_uses_the_fixed_start_and_session_strategy`, `test_action_text_uses_the_applied_step`; consistency eval `signal_mismatches` (10 fixtures) | direct |
| Latest transition reads the position change | §1 | `test_strategy.py::test_latest_transition_reads_the_position_change` | direct |
| Session strategy settings reach tool calls; explicit what-ifs kept | §1 | `test_agent.py::test_dashboard_strategy_settings_reach_tool_calls`, `test_without_strategy_settings_tool_defaults_apply`, `test_comparison_scenarios_get_call_ids_and_session_settings` | direct |
| Raw-figure format rule (position word clause, "N of M units") | Validator rules | `test_guardrails.py::test_raw_position_figures_are_rejected`, `test_strategy_state_rule_stays_narrow`, `test_placeholder_answers_pass`; `test_position_plan.py::test_restated_user_position_figures_are_rejected`; consistency eval `detection_rate` / `false_positive_rate` | direct |
| Rule stays narrow (no wording tables, direction not blocked) | §2, Stop condition | `test_guardrails.py::test_strategy_state_phrasings_are_not_blocked_at_runtime`, `test_direction_wording_is_not_checked_at_runtime`; `test_agent.py::test_contradictory_direction_is_not_blocked_at_runtime` | direct |
| Placeholders `{btN.*}`, `{ppN.*}`, standard-signal placeholders | Placeholder policy | `test_guardrails.py::test_signal_placeholders_render_from_the_signal`, `test_backtest_placeholders_render_from_the_named_call`, `test_bad_placeholders_are_errors`, `test_backtest_metric_placeholders_render_with_their_units`; `test_position_plan.py::test_plan_placeholders_render_from_the_named_call`, `test_bad_plan_placeholders_are_errors`; `test_agent.py::test_backtest_results_get_call_ids_for_placeholders`, `test_unknown_call_id_is_a_violation`, `test_backtest_and_plan_call_ids_are_counted_separately` | direct |
| One repair turn, then rule-based fallback | Validator rules | `test_agent.py::test_restated_position_is_sent_back_once_then_falls_back`, `test_repaired_answer_is_kept`, `test_malformed_sections_trigger_repair`, `test_deepseek_repair_turn` | direct |
| Fallback on budget / API error / missing key / refusal / truncation | LLM architecture | `test_agent.py::test_exhausted_budget_falls_back_to_rule_based_summary`, `test_api_error_falls_back`, `test_missing_credentials_fall_back`, `test_refusal_falls_back`, `test_deepseek_without_key_falls_back`, `test_deepseek_api_error_and_truncation_fall_back` | direct |
| Page uses only a typed key | — | `test_research_page.py::test_no_typed_key_builds_no_client_and_falls_back`, `test_typed_key_is_the_only_key_used` (Streamlit `AppTest`) | direct |
| Append-only ingestion, chain-link check, quality aborts | Data | `test_ingest.py::test_appends_new_sessions_and_fills_blank_cells_only`, `test_update_is_idempotent_and_round_trips_untouched_bytes`, `test_suspicious_chain_link_factor_aborts`, `test_quality_failures_abort_the_batch`, `test_unsettled_session_is_not_written` | direct (synthetic data) |
| Daily push never forces, rebases on concurrent merges | Data | `test_push_data_update.py` (7 tests) | direct |
| Dashboard pages do not import heavy deps | — | `test_import_footprint.py::test_dashboard_imports_skip_heavy_dependencies` | direct (imports `utils*` only, not the page files) |
| Eval CLI refuses live runs without consent/keys; offline reads no `.env` | Live model evals | `test_eval_cli.py` (refusal, exit-code, `--fresh`, dotenv tests) | direct |

## Existing strong coverage

- FACT: Point-in-time for the five market tools is enforced by a perturbation test (poisoned future rows) on real data at four dates, plus RAG eligibility in three retrieval modes on synthetic and real indices, plus an eval-level `leakage_rate` (`evals/retrieval_eval.py`).
- FACT: `apply_rule_once`, `plan_position`, `boundary_state`, and `build_decision` are pinned case by case on real dates, including cap, floor, clip, custom settings, and out-of-range input (`tests/test_position_plan.py`).
- FACT: The agent loop is tested end to end with scripted Claude and DeepSeek clients: tool loop, clamp, cache, budget, repair, fallback, call ids, session settings (`tests/test_agent.py`, 38 tests).
- FACT: The validator contract has positive and negative probes, and its narrowness is itself tested, so it cannot silently grow wording tables (`tests/test_guardrails.py`). The consistency eval pins 10 code-owned signals to real dates, so a data or rule change that moves one fails CI (`evals/datasets/consistency.yaml` header).
- FACT: Ingestion rules and the push script are tested on synthetic CSVs and a temporary git repo.

## Missing coverage

### Missing unit coverage

| Gap | Evidence |
|---|---|
| `max_steps` exhaustion fallback (`RuntimeError: no final answer after N steps`) | FACT: raised at `assistant/agent.py:544` and `:734`; no test names it (`grep max_steps tests/` finds nothing). |
| Anthropic final text that is not valid JSON | FACT: `_parse_final` calls `json.loads` (`assistant/agent.py:619`); the `JSONDecodeError` is a `ValueError` caught at `:390` and falls back with no repair turn. Only the DeepSeek retry is tested (`test_deepseek_unparseable_final_answer_gets_one_json_retry`). |
| Missing `summary` / no text block in the final answer | FACT: `assistant/agent.py:588`, `:618`; untested. |
| Fallback summary with non-default add/reduce/initial/cap | FACT: the fallback passes only thresholds to `rule_based_summary` (`assistant/agent.py:394-399`, `tools/core.py:408-413`); no test runs a fallback with custom position settings. |
| `_clamp_as_of` with a missing or unparsable `as_of` | FACT: branch at `assistant/agent.py:327-330`; only the later-date case is tested. |
| Regime at exactly the threshold (index == 64 or == 15) | FACT: strict `>` / `<` in both `tools/core.py:_classify` and `utils/backtest.py:15-17`; no equality case in any test. |
| `ingest.validate` file-level rules: duplicate dates, duplicate ids, row after the settled session, unknown ratings, VIX outside 5-150 | FACT: `ingest/market_update.py:294-340`; `test_ingest.py` covers the value checks via `merge` but has no test for these five messages. |
| `_next_rule` under custom settings (non-default add/reduce/cap) | FACT: `test_next_rule_names_the_cap_and_floor` uses defaults only. |
| Equity curve timing: `strategy_return` uses `position.shift(1)` | FACT: `utils/backtest.py:22`; no test asserts that today's return uses yesterday's position (`test_strategy.py` checks columns and bounds only). Attribution lag is tested separately (`test_analysis.py::test_exposure_attribution_uses_lagged_effective_exposure`). |
| `run_backtest_with_signals` | FACT: `utils/backtest.py:40`; not referenced by any test. |

### Missing regression coverage

| Gap | Evidence |
|---|---|
| Agreement between the two rule implementations under non-default settings | FACT: `test_plan_from_the_paths_previous_position_matches_the_backtest_step` uses default params on 5 dates. `utils/backtest.py` keeps fractions unrounded, `utils/strategy.py:apply_rule_once` rounds to 1 dp. ASSUMPTION: settings such as `add_pct=33.3` could make them diverge on the last step. |
| Perturbation test for `search_fed_docs` via the tool, `rule_based_summary`, `build_strategy_signal`, `position_plans`, and `run_backtest(start_date=...)` | FACT: `MARKET_TOOLS` in `test_point_in_time.py` lists only the five market tools. These functions read through `_frame` / `get_index`, so protection is incidental. |
| Page Decision Facts panel content | FACT: `test_research_page.py` asks with no position entered, so the Your Position panel, the starting-positions table, and the scenarios table (`pages/3_…:247-300`) never render with a plan, and the `own_position` → `agent.run(current_position_pct=...)` wiring (`:190-191`) is not asserted. |
| Threshold Strategy and Peak Detection pages, Home | FACT: only `py_compile` in CI and the `utils` import-footprint test; no `AppTest` runs them. |
| Process-global singletons (`set_store`, `set_index`) leaking between tests | FACT: `test_injected_store_is_pinned_until_reset` covers the store; nothing covers `rag.set_index`. |
| `tools/` import footprint | FACT: `test_import_footprint.py` checks `utils` only; architect report notes `tools/` pulls in streamlit, plotly, mcp. Apparently intentional (the test's docstring scopes it to dashboard pages). |
| `scripts/daily_update.py` orchestration and `scripts/update_market_data.py` | FACT: no test imports either; `CSV_PATH` is duplicated (`scripts/update_market_data.py:22`, `utils/data_loader.py:5`). |
| Broad `ValueError` catch hides programming errors | FACT: `assistant/agent.py:390`; no test checks that a non-guardrail `ValueError` (e.g. a bug) is distinguishable from an intended fallback. |

### Missing eval coverage

| Gap | Evidence |
|---|---|
| No committed agent recordings, so the replay test and `--mode replay` never run offline | FACT: `test_agent_eval_replay.py` skipped; AGENTS.md *Testing*. `tool_selection_accuracy`, `citation_precision`, `numeric_faithfulness`, `decision_correct` are measured only in paid live runs. |
| Prose that presents strategy-path exposure as the user's position via `{current_exposure}` | FACT: signal placeholders render and are exempt from the raw-figure rule (`assistant/validator.py:217-229`); `test_placeholder_answers_pass` accepts "now holds {current_exposure} exposure". Only the judge measures misuse. |
| Neutral-regime forward returns, "latest FOMC" recency | FACT: documented in README *Limitations*; no eval item targets them. |
| Fallback rate under ordinary questions | FACT: `fallbacks` is reported only by live agent evals. |

### Intentionally not tested

| Item | Where documented |
|---|---|
| Dashboard full-sample VIX normalization and linear interpolation | AGENTS.md §4; README *Methodology Design Choices*. `test_full_sample_normalization_would_leak` documents the difference rather than forbidding it. |
| Direction / contradiction wording in prose | AGENTS.md §2 and *Stop condition*; `test_direction_wording_is_not_checked_at_runtime`. |
| Pronoun restatements ("It holds 64%") | `test_guardrails.py::test_strategy_state_rule_stays_narrow` ("pronouns are not covered (documented)"); README *Limitations*. |
| Live model behavior in CI | AGENTS.md *Live model evals*; CI live job is `workflow_dispatch` only. |
| Fed policy backdrop text | README *Limitations* (no schema, not checked against computed state). |
| `scipy.find_peaks` as a deployable signal | README *Limitations* (hindsight label); only point-in-time of the tool is tested. |

## High-risk weakly protected areas

1. **Two rule implementations.** FACT: `utils/backtest.py:13-18` (inline, fractional) and `utils/strategy.py:apply_rule_once` (rounded) must agree for the Strategy Path and Your Position panels to be consistent. Agreement is tested only with default params. RECOMMENDATION: a parametrized agreement test over custom settings before any strategy-math change.
2. **Fallback summary ignores session position settings.** FACT: `assistant/agent.py:394-399`. When the LLM path fails, the rule-based text is built with default add/reduce/initial/cap while the Decision Facts panel uses session settings. ASSUMPTION: the summary can then describe a different exposure than the panel. No test.
3. **Broad `ValueError` catch.** FACT: `assistant/agent.py:390` turns `JSONDecodeError`, `DataQualityError`-like errors, and genuine bugs into a silent fallback; `max_steps` and unparsable Anthropic JSON are untested.
4. **Signal placeholders bypass the raw-figure rule.** FACT: see eval gap above. The value is correct (code-rendered), but the framing (path vs user) relies on the prompt and the judge.
5. **Research page decision rendering.** FACT: no test renders the Your Position panel or asserts the page passes the input position to the agent. A regression here puts a wrong authoritative fact on screen without failing CI.
6. **Point-in-time for new date parameters.** FACT: `_clamp_as_of` rewrites only `as_of`; safety for other date inputs (`start_date`) depends on `_frame` truncation and is not perturbation-tested.
7. **Ingest file-level validation.** FACT: the guards that protect the append-only CSV against duplicate dates / ids and post-settled rows have no direct test.

## Relevant test and eval commands

```bash
.venv/Scripts/python.exe -m pytest -q                                  # full suite, ~2 min on Windows
.venv/Scripts/python.exe -m pytest -q tests/test_point_in_time.py      # look-ahead (15 tests)
.venv/Scripts/python.exe -m pytest -q tests/test_position_plan.py tests/test_strategy.py
.venv/Scripts/python.exe -m pytest -q tests/test_agent.py tests/test_guardrails.py
.venv/Scripts/python.exe -m pytest -q tests/test_rag.py tests/test_retrieval_eval.py
.venv/Scripts/python.exe -m pytest -q tests/test_ingest.py tests/test_push_data_update.py
.venv/Scripts/python.exe -m pytest -q tests/test_research_page.py      # Streamlit AppTest
.venv/Scripts/python.exe -m evals.run offline                          # point-in-time + consistency + retrieval
.venv/Scripts/python.exe -m evals.run consistency                      # offline consistency only
.venv/Scripts/python.exe -m evals.run retrieval                        # recall@5 / MRR / leakage
```

Live evals (`--live --fresh`) cost money and need the user's approval each time (AGENTS.md *Live model evals*).

## Verify before changing specific modules

| Module | Run / check first |
|---|---|
| `utils/strategy.py`, `utils/backtest.py` | `test_position_plan.py`, `test_strategy.py`, `test_analysis.py`, `evals.run consistency` (pinned signals). Check both rule implementations change together; state whether historical results change. |
| `tools/core.py`, `tools/market.py` | `test_point_in_time.py`, `test_position_plan.py`, `test_mcp_server.py`, then full suite. A new tool or date parameter needs an entry in `MARKET_TOOLS` (RECOMMENDATION). |
| `utils/data_loader.py` | `test_point_in_time.py` (ffill, closed days), `test_import_footprint.py`. Do not change the dashboard's linear interpolation. |
| `assistant/presentation.py`, `assistant/signal.py` | `test_position_plan.py`, `test_guardrails.py`, `test_agent.py`, `evals.run consistency`. Manually check the page panels (no automated coverage). |
| `assistant/agent.py` | `test_agent.py`, `test_research_page.py`, `evals.run consistency`. Check which exceptions now reach the fallback. |
| `assistant/validator.py`, `assistant/render.py` | `test_guardrails.py`, `test_position_plan.py` placeholder tests, `test_agent.py` repair tests, `evals.run consistency` (`detection_rate` 1, `false_positive_rate` 0). Keep `test_strategy_state_rule_stays_narrow` passing. |
| `rag/` | `test_rag.py`, `test_retrieval_eval.py`, `evals.run retrieval` (hybrid recall@5 baseline 0.9333, leakage 0). |
| `ingest/`, `scripts/push_data_update.py` | `test_ingest.py`, `test_push_data_update.py`. Never edit `data/market_data_clean.csv` by hand. |
| `scripts/fetch_fomc.py` | `test_fetch_fomc.py`, `test_rag.py::test_incremental_add_matches_full_build`. |
| `mcp_server.py` | `test_mcp_server.py`, `test_agent.py` (builds the server). |
| `evals/` | `test_eval_cli.py`, `test_eval_metrics.py`, `test_judge.py`, `test_retrieval_eval.py`. |
| `pages/` | `py_compile` (as CI), `test_research_page.py`, `test_import_footprint.py`; run the app by hand for panel content. |
