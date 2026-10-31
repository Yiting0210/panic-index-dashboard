# System Overview

A descriptive map of the system as it exists in the repository today. It records
what the code does, with file references as evidence; it does not propose changes.
For the per-module breakdown and the import graph, see [module-map.md](module-map.md).

Sources read: `AGENTS.md`, `README.md`, and the modules cited inline.

---

## 1. User-facing features

The system has four surfaces: a Streamlit dashboard (three pages plus a home
page), an MCP server, a set of offline/live eval commands, and a scheduled data
job.

| Surface | Entry point | What the user gets |
|---|---|---|
| Home | `Home.py` | Panic Index overview, scatter plot, correlation evidence summary (`utils.analysis.build_correlation_evidence_summary`). |
| Threshold Strategy | `pages/1_💠_Threshold_Strategy.py` | Threshold backtest vs Buy & Hold, KPIs (return, max drawdown, Sharpe, Calmar), exposure diagnostics, exposure trade-off attribution, forward-return views, deterministic research summary (`utils.narrative`). Sets the session's strategy settings. |
| Peak Detection | `pages/2_🏔️_Peak_Detection.py` | Hindsight `scipy.find_peaks` labels vs the causal `RealTimePeakDetector` (`utils/peak_detection.py`), forward-return histograms/tables, strategy comparison table. |
| Research Assistant | `pages/3_🤖_Research_Assistant.py` | Question box, as-of date, market, optional *Your current exposure*; renders code-owned **Decision / Strategy Facts**, the model's **AI Analysis** (or the rule-based summary), and **Advanced / Debug**. Bring-your-own key for Claude or DeepSeek. |
| MCP server | `mcp_server.py`, `.mcp.json` | Six read-only tools (`get_regime`, `run_backtest`, `plan_position`, `get_forward_returns`, `detect_peaks`, `search_fed_docs`) for any MCP client, over stdio or Streamable HTTP. |
| Evals | `python -m evals.run …` (`evals/run.py`) | Offline point-in-time, consistency, and retrieval suites; opt-in paid live agent/consistency runs. |
| Daily data job | `.github/workflows/daily-update.yml` → `scripts/daily_update.py` | Append-only market data refresh, incremental FOMC fetch and index update, tests, bot commit to `main`. |

Strategy settings flow between pages through Streamlit session state:
`utils/sidebar.py` stores them under `utils.strategy.SESSION_KEY`, and the
Research Assistant reads them with `strategy_settings()` and builds a
`StrategyParams` (`pages/3_🤖_Research_Assistant.py`, sidebar section).

---

## 2. Major modules

| Package | Role | Key files |
|---|---|---|
| `utils/` | Analytics core shared by dashboard and tools: data loading, Panic Index, backtest, metrics, diagnostics, peak detection, narrative, charts, sidebar. | `data_loader.py`, `strategy.py`, `backtest.py`, `metrics.py`, `analysis.py`, `narrative.py`, `peak_detection.py`, `signals.py`, `charts.py`, `sidebar.py` |
| `tools/` | Point-in-time tool layer. Every tool takes `as_of`, returns a pydantic model. | `core.py` (tools), `market.py` (`MarketStore`, as-of truncation), `schemas.py` (result models) |
| `mcp_server.py` | Registers `tools.RESEARCH_TOOLS` on an `MCPServer` with read-only annotations and structured output. | — |
| `assistant/` | Research agent: tool loop over an in-process MCP client, code-owned decision facts, placeholder rendering, validator, response cache, usage ledger. | `agent.py`, `signal.py`, `presentation.py`, `render.py`, `validator.py`, `cache.py`, `ledger.py` |
| `rag/` | FOMC retrieval: chunking, local embeddings, FAISS + BM25 hybrid with as-of pre-filtering. | `chunking.py`, `index.py`, `lexical.py`, `query.py` |
| `ingest/` | Market-data ingestion: Yahoo / CNN sources, append-only merge, NYSE calendar. | `sources.py`, `market_update.py`, `trading_calendar.py` |
| `scripts/` | CLIs: daily update, market update, FOMC fetch, index build, data-commit push. | `daily_update.py`, `update_market_data.py`, `fetch_fomc.py`, `build_fomc_index.py`, `push_data_update.py` |
| `evals/` | Eval CLI, datasets, metrics, retrieval/agent/consistency suites, LLM judge. | `run.py`, `agent_eval.py`, `consistency_eval.py`, `retrieval_eval.py`, `judge.py`, `metrics.py`, `datasets/*.yaml` |
| `tests/` | pytest suite (unit, point-in-time, RAG, MCP, agent with scripted client, ingestion, git push). | see §8 |
| `data/` | `market_data_clean.csv` (market + sentiment rows), `fomc/documents.jsonl`, `fomc/index/` (FAISS, chunks, manifest). | — |

---

## 3. Data flow

```text
 Yahoo Finance chart API ──┐
 CNN Fear & Greed endpoint ┴─► ingest/sources.fetch_all
                                   │
                                   ▼
                     ingest/market_update.merge  (append-only, validated)
                                   │
                                   ▼
                     data/market_data_clean.csv ◄──────────── (daily job commits)
                                   │
          ┌────────────────────────┴─────────────────────────┐
          ▼                                                  ▼
 utils.data_loader.load_data()                    tools.market.MarketStore.frame_as_of(as_of)
 (full sample, linear interpolation,             (truncate to as_of FIRST, then ffill,
  full-sample VIX normalization; st.cache_data)   then compute_panic_index; lru_cache)
          │                                                  │
          ▼                                                  ▼
 Dashboard pages (Home, 1, 2)                     tools.core.* ──► mcp_server ──► MCP clients
                                                                    │
                                                                    └─► assistant (in-process mcp.Client)

 federalreserve.gov ─► scripts/fetch_fomc.py ─► data/fomc/documents.jsonl (published_at)
                                                       │
                                                       ▼
                         rag.index (chunk, embed, FAISS + BM25) ─► data/fomc/index/
                                                       │
                                                       ▼
                                         tools.core.search_fed_docs
```

Two readers of the same CSV, deliberately different (`utils/data_loader.py`, `read_market_data` docstring):

- **Dashboard** — `read_market_data(fill_method='linear')` then `compute_panic_index` over the full sample. Retrospective display; documented in README *Methodology Design Choices*.
- **Tools** — `read_market_data(as_of=..., fill_method='ffill')` drops rows after `as_of` *before* filling and normalizing (`tools/market.py`, `MarketStore` docstring).

Cache invalidation is file-mtime based, so running processes pick up the daily update without restart:
`utils.data_loader.data_version()` (CSV mtime) keys `st.cache_data` and `tools.market.get_store()`;
`rag.index._manifest_version()` keys `rag.get_index()`.

Composite Panic Index (`utils/data_loader.compute_panic_index`):
`panic_index = 0.5 · (VIX − min)/(max − min)·100 + 0.5 · (100 − fear_greed_index)`,
with min/max taken over the rows passed in.

---

## 4. Research Assistant flow

Evidence: `pages/3_🤖_Research_Assistant.py`, `assistant/agent.py` (`ResearchAgent.arun`, `_steps`, `_execute_tool`), `assistant/presentation.py`, `assistant/signal.py`, `assistant/render.py`, `assistant/validator.py`.

```text
Page inputs: question, as_of, market, StrategyParams (session), own_position (optional)
   │
   │  build_agent(provider, model, api_key)   ← key typed in the page only; env keys ignored
   ▼
ResearchAgent.run(question, as_of, market, strategy, current_position_pct=own_position)
   │
   ├─ 1. build_decision(market, as_of, strategy, current_position_pct)        [code]
   │       ├─ build_strategy_signal → tools.run_backtest(start=2021-05-17) + tools.get_regime
   │       └─ if position given → tools.plan_position(current_position_pct, settings)
   │     → DecisionFacts(signal, plan)
   │
   ├─ 2. _loop: mcp.Client(server) in-process, list_tools, up to max_steps=8
   │       first message = as_of + strategy.describe() + DecisionFacts.prompt_block() + question
   │       for each tool_use block → _execute_tool:
   │           _apply_strategy  (fill session strategy params unless model overrode them)
   │           _clamp_as_of     (as_of > session cutoff or missing → cutoff; recorded)
   │           mcp.call_tool    → label run_backtest as bt1.., plan_position as pp1..
   │       each model call → _create: ResponseCache.get / UsageLedger.check_budget / record
   │       final answer → _parse_final → render placeholders → _needs_repair
   │           violations? → one repair message (max_repairs=1) → model rewrites
   │
   ├─ 3. _validate: still violating → GuardrailViolation
   │
   └─ 4. on API error / refusal / max_tokens / budget / guardrail / no key:
           answer = tools.rule_based_summary(market, as_of, thresholds); citations cleared
   ▼
Page renders:
   Decision / Strategy Facts  ← DecisionFacts (code), plus position_plans() table at 0/20/50/80/100%
   AI Analysis                ← summary, why, historical_context, risks, citations (model)
     or Rule-based summary    ← fallback
   Advanced / Debug           ← tool calls, clamps, violations, repairs, tokens, cost, cache hits
```

Key properties:

- **Output schema** is `FINAL_ANSWER_SCHEMA` / `ANSWER_FIELDS = ("summary", "why", "historical_context", "risks", "citations")` (`assistant/agent.py`). There is no action or position field.
- **Providers**: `ResearchAgent` (Anthropic Messages API) and `DeepSeekAgent(ResearchAgent)` (OpenAI-compatible client at `DEEPSEEK_BASE_URL`, JSON mode) share the loop, clamp, cache, ledger, and fallback.
- **Placeholders** (`assistant/render.py`): `{btN.<field>}` for what-if backtests and `{ppN.<field>}` for model-invoked `plan_position` calls; code fills values with explicit units. Unknown ids/fields are reported as placeholder errors.
- **Validator** (`assistant/validator.py`): placeholder errors; raw percentage in a clause containing a position word, or "N of M units"; citations must be in this run's `search_fed_docs` results and have `published_at <= as_of`.
- **Cost/observability**: `assistant/cache.py` (content-addressed `.cache/llm/`, modes `readwrite|replay|record|off`); `assistant/ledger.py` (`logs/llm_usage.jsonl`, pricing table, daily budget, `billed_to`).

---

## 5. Deterministic strategy flow

Evidence: `utils/strategy.py`, `utils/backtest.py`, `tools/core.py` (`run_backtest`, `plan_position`, `_next_rule`), `assistant/signal.py`, `assistant/presentation.py`.

**Parameters** — one frozen dataclass, `StrategyParams` (`utils/strategy.py`), with `DEFAULT_STRATEGY`:
buy threshold 64, sell threshold 15, initial 50%, add 20%, reduce 10%, cap 100%, floor 0%.
Dashboard, backtest helpers, and tools all default from it.

**Rule** (`apply_rule_once`, and the same step inside `utils.backtest.run_backtest`):

```text
panic_index > buy_threshold   → position = min(position + add_pct, max)
panic_index < sell_threshold  → position = max(position − reduce_pct, min)
otherwise                     → hold
action is read from the applied change (+ → add, − → reduce, 0 → hold),
so a step clipped to zero at the cap/floor is a hold.
```

**Backtest timing** — `strategy_return = daily_return × position.shift(1)`: today's
position earns tomorrow's return (`utils/backtest.py`). `latest_transition` derives the
last row's previous position, change, and action.

**Two distinct answers**, kept separate:

| Question | Code path | Depends on history? |
|---|---|---|
| "Where would the strategy be today?" — *strategy path exposure* | `assistant.signal.build_strategy_signal` → `tools.run_backtest(start_date=STANDARD_BACKTEST_START=2021-05-17, **session settings)` → `final_position_pct`, `previous_position_pct`, `latest_action` | Yes (path-dependent) |
| "I hold X% today — what does the rule do?" — *your position* | `tools.plan_position(current_position_pct=X)` → one `apply_rule_once` on the latest regime → `action`, `applied_change_pct`, `target_position_pct`, `next_rule` | No |

`assistant.presentation.boundary_state` labels cap/floor behaviour
("clipped at / reached / already at the cap|floor"). `DecisionFacts.as_dict()` is the
single structure used by the page, the debug view, and eval scoring.

---

## 6. RAG flow

Evidence: `scripts/fetch_fomc.py`, `rag/chunking.py`, `rag/index.py`, `rag/lexical.py`, `rag/query.py`, `tools/core.search_fed_docs`.

```text
fetch_fomc.update(start_year, end_year, incremental)
   parse federalreserve.gov calendar → statements / minutes / press conference PDFs
   each record keeps published_at (release date, not meeting date)
   → data/fomc/documents.jsonl (incremental: never rewrites stored docs)

FedDocIndex.build / add_documents
   chunk_documents: paragraph packing (MAX_CHARS 1200, MIN_CHARS 200);
     transcripts split per Q&A exchange with question context repeated
   FastEmbedEmbedder: BAAI/bge-small-en-v1.5, local ONNX, normalized vectors
   FAISS index + chunks.jsonl + manifest.json in data/fomc/index/

search(query, as_of, k, doc_type, mode="hybrid", date_aware=True)
   ids = eligible_ids(as_of, doc_type)        ← published_at <= as_of, inside the search
   dense:  FAISS search with IDSelectorBatch(ids)
   bm25:   BM25 scores restricted to ids
   hybrid: reciprocal_rank_fusion(dense top-50, bm25 top-50
           [+ dense restricted to a month named in the query, e.g. "June 2022"])
   → (hits[:k], eligible_count)

search_fed_docs tool → FedDocSearchResult(query, as_of, total_eligible_chunks, results)
```

FOMC content reaches the assistant **only** through `search_fed_docs`; nothing
converts it into a regime, threshold, or position (README, *FOMC documents are a
background layer*). `rag` is imported lazily inside `search_fed_docs` so dashboard
imports stay light.

---

## 7. Ingestion flow

Evidence: `.github/workflows/daily-update.yml`, `scripts/daily_update.py`, `scripts/update_market_data.py`, `ingest/*.py`, `scripts/push_data_update.py`.

```text
cron "0 5 * * 2-6" (05:00 UTC Tue–Sat) or manual dispatch
   │
   ▼
scripts/daily_update.py
   1. update_market_data.run()
        read_raw(CSV) → last_settled_session() (NY close + 16:30)
        start = last_complete_date − lookback_days(10)
        fetch_all(start, settled): Yahoo closes (adjusted for ETFs) + CNN F&G history/live
        merge(rows, fetched):
          never overwrite stored values (revisions reported, not applied)
          fill blanks, append new dates; non-trading days get F&G only
          skip incomplete trading days with a warning (retry next run)
          chain-link adjusted ETF prices to the last stored row
          validate: duplicates, F&G 0–100, VIX 5–150, prices > 0,
                    moves > 20% (VIX 4×), link factor > ±5% → DataQualityError aborts batch
        write_raw only if changed
   2. fetch_fomc.update(2021, this_year, incremental=True)
   3. if new docs → rag.update_default_index() (embed only new docs)
   → JSON summary (logs/daily_update.json)
   │
   ▼
python -m pytest -q   (fails → nothing committed)
   │
   ▼
git add data/market_data_clean.csv data/fomc ; commit "data: daily update …"
scripts/push_data_update.py: fetch main, rebase, push; retry ≤3; never --force;
                             conflict on data files → fail without pushing
```

---

## 8. Testing / eval architecture

### pytest (`tests/`)

Run with `.venv/Scripts/python.exe -m pytest -q`. CI (`.github/workflows/ci.yml`) also `py_compile`s every Streamlit entry point and `mcp_server.py`.

| Area | Files |
|---|---|
| Analytics / strategy | `test_strategy.py`, `test_analysis.py`, `test_narrative.py`, `test_peak_detection.py` |
| Point-in-time | `test_point_in_time.py` — perturbation (rows after `as_of` replaced with extreme values must not change outputs), `data_through <= as_of`, completed forward windows only, ffill never borrows future prices, full-sample normalization shown to leak |
| Tools / MCP | `test_mcp_server.py`, `test_position_plan.py` (plan_position and `DecisionFacts`) |
| Assistant | `test_agent.py` (scripted client: loop, clamp, cache, ledger, fallback), `test_guardrails.py` (signal, placeholders, format rule), `test_research_page.py` (page ignores env keys) |
| RAG | `test_rag.py` (fake embedder, as-of filtering), `test_retrieval_eval.py` (real index), `test_fetch_fomc.py` |
| Ingestion / ops | `test_ingest.py` (synthetic append-only rules), `test_push_data_update.py` (real git repos in temp dirs) |
| Evals | `test_eval_cli.py`, `test_eval_metrics.py`, `test_judge.py`, `test_agent_eval_replay.py` |
| Import hygiene | `test_import_footprint.py` — Home / Threshold / Peak pages must not import LLM SDKs, MCP, embeddings, or FAISS |

### Eval suites (`evals/`)

| Command (`evals/run.py`) | Calls a model? | What it does |
|---|---|---|
| `offline` / `ci` | No | `tests/test_point_in_time.py` + consistency (offline) + retrieval |
| `consistency` | No (unless `--live`) | `evals/consistency_eval.py` over `datasets/consistency.yaml` (10 fixtures): expected scenario reproduced, planted restatements rejected, direction probes reported only |
| `retrieval` | No | `evals/retrieval_eval.py` over `datasets/retrieval.yaml` (30 questions): recall@k, MRR, leakage; ablation of dense/bm25/hybrid |
| `agent --provider … --live --fresh [--judge]` | Yes, paid | `evals/agent_eval.py` over `datasets/agent.yaml` (15 questions): decision correctness, tool selection, citation precision, numeric faithfulness, leakage, fallbacks; optional `evals/judge.py` groundedness |

Exit codes: 0 pass, 1 correctness failure, 2 usage/safety refusal. `--fresh` uses a new
cache dir under `.cache/llm-runs/`. Results land in `evals/results/*.json`.
Replay mode needs `evals/recordings/`, which is not committed, so
`test_agent_eval_replay.py` is skipped.

CI's optional `live-agent-eval` job (manual dispatch) uses `ANTHROPIC_API_KEY` and
`python -m evals.agent_eval --mode record`; AGENTS.md's documented live procedure uses
DeepSeek via `evals.run`.

---

## 9. Critical invariants (from `AGENTS.md`)

| # | Invariant | Where it is enforced in code | Where it is tested |
|---|---|---|---|
| 1 | Code owns decision facts (position, path exposure, action, applied change, target, cap/floor, next trigger). | `assistant/presentation.py` (`DecisionFacts`, `build_decision`); answer schema has no decision fields (`assistant/agent.py`). | `tests/test_position_plan.py`, `evals/consistency_eval.py` |
| 2 | Simplify architecture, don't validate prose; validator stays shallow. | `assistant/validator.py` (one format rule + placeholders + citations). | `tests/test_guardrails.py`; prose measured by `evals/judge.py` |
| 3 | The user's position is authoritative UI state, never extracted by the LLM. | Page `number_input` → `ResearchAgent.run(current_position_pct=…)` → `tools.plan_position`. | `tests/test_position_plan.py`, agent eval a13–a14 |
| 4 | Point-in-time semantics on every `as_of` path. | `tools/market.MarketStore` (truncate → ffill → normalize); `rag.index.eligible_ids` (by `published_at`, inside search); `assistant.agent._clamp_as_of`; `validator.citation_violations`. | `tests/test_point_in_time.py`, `tests/test_rag.py`, retrieval eval leakage |
| 4a | Dashboard keeps full-sample normalization + linear interpolation; don't copy into tools. | `utils.data_loader.load_data` vs `tools.market` | `test_full_sample_normalization_would_leak` |
| 5 | Strategy path exposure ≠ user's position. | `assistant/signal.py` (standard backtest, fixed start) vs `tools.plan_position`; prompt text in `StrategySignal.prompt_block`. | `tests/test_position_plan.py`, `tests/test_guardrails.py` |
| — | Placeholders only for model-invoked what-ifs (`{btN.*}`), not code-rendered facts. | `assistant/render.py` | `tests/test_guardrails.py` |
| — | One repair turn, then deterministic fallback. | `ResearchAgent.max_repairs=1`, `_needs_repair`, `_validate`, fallback in `arun`. | `tests/test_agent.py` |
| — | `market_data` CSV is append-only; never edited by hand. | `ingest/market_update.merge` + `validate`. | `tests/test_ingest.py` |
| — | No secrets committed; page never reads env API keys. | page `build_agent`; `evals/run.load_dotenv` for live evals only. | `tests/test_research_page.py` |
| — | Live evals cost money: ask first, run `--fresh`, nothing concurrent. | `evals/run.confirm`, `check_args` (exit 2). | `tests/test_eval_cli.py` |

---

## 10. Module dependencies (summary)

Layering, lowest first (full graph in [module-map.md](module-map.md#dependency-graph)):

```text
utils  ──►  tools  ──►  mcp_server  ──►  assistant  ──►  pages/3 (Research Assistant)
  ▲           │                              │
  │           └─(lazy)─► rag                 └──► evals
  └── Home.py, pages/1, pages/2 (utils only)
ingest ──► scripts/update_market_data ─┐
rag    ──► scripts/build_fomc_index, scripts/daily_update ◄┘
```

---

## Observations (factual, no changes proposed)

- `tools/` depends on Streamlit transitively: `tools/market.py` imports `utils.data_loader`, which imports `streamlit`, and importing any `utils` submodule runs `utils/__init__.py` (which pulls in `charts`/plotly and `sidebar`). README notes the MCP server imports Streamlit at startup.
- `tools/market.py` imports `ToolError` from the MCP SDK, so the tool layer itself depends on `mcp`.
- CI's optional `live-agent-eval` job records with Anthropic (`python -m evals.agent_eval --mode record`), while `AGENTS.md` documents live runs as DeepSeek through `python -m evals.run … --live --fresh`.
- `evals/recordings/` does not exist, so replay-mode agent evals are skipped (as `AGENTS.md` states).
