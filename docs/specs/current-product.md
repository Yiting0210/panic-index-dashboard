# Current product baseline

Status: baseline (describes what exists; written 2026-11-03, after the features were built)

This is not a spec for new work. It summarizes the requirements the existing product already
meets, so new specs can cite it under "Unchanged behavior". Sources: README *Overview*,
*Features*, *Product Case Study*, *Limitations*; `AGENTS.md`; `docs/architecture/system-overview.md`.
When this file and those sources disagree, the sources win and this file is updated.

## Problem

Market sentiment data is fragmented and easy to overinterpret. VIX and CNN Fear & Greed each
capture part of the picture; neither is a decision framework on its own. The product separates
regime identification from short-term prediction, causal rules from hindsight validation, and
historical relationships from guaranteed performance.

## Users

- ETF investors studying extreme sentiment regimes
- Market research analysts comparing sentiment with subsequent market behavior
- Risk-aware portfolio builders evaluating accumulation rules, exposure, and drawdown trade-offs
- Quantitative researchers comparing hindsight validation with causal signal logic

## Surfaces

| Surface | What the user gets |
|---|---|
| Home | Panic Index overview and correlation evidence |
| Threshold Strategy page | Threshold backtest vs Buy & Hold, KPIs, exposure diagnostics, trade-off attribution, forward returns, deterministic research summary |
| Peak Detection page | Hindsight `scipy.find_peaks` labels vs the causal real-time detector, forward-return views |
| Research Assistant page | Question, as-of date, market, optional current position; code-rendered decision facts, model explanation with citations, rule-based fallback |
| MCP server | Six read-only, `as_of`-pinned tools for any MCP client |
| Evals | Offline point-in-time, consistency, and retrieval suites; opt-in paid live runs |
| Daily data job | Append-only market data and incremental FOMC updates |

Markets: QQQ, SMH, SPX, DJI. Data from 2021-05-17.

## Functional requirements (as built)

1. The Composite Panic Index combines normalized VIX and inverted Fear & Greed with equal weight.
2. The threshold strategy adds exposure in the panic regime and reduces it in the greed regime, within a cap and floor, applied from the next observation.
3. Strategy parameters are shared across pages through the session.
4. Every MCP tool takes `as_of`, reads nothing published after it, and reports `data_through`.
5. FOMC retrieval returns only documents published on or before `as_of`, with citations.
6. On the Research Assistant, decision facts (current position, strategy path exposure, action, applied change, target, cap/floor, next trigger) are computed by code; the model only explains them.
7. The user's position comes from the page input, never from the question text.
8. Strategy path exposure is never presented as the user's position or target.
9. When the model path fails, the page shows a deterministic rule-based summary.
10. The model is called only with an API key typed into the page; environment keys are ignored.
11. Market data is appended daily and stored values are never overwritten.

## Non-goals

- Live trading recommendations or investment advice
- Order execution or brokerage integration
- Treating hindsight peak labels as a deployable signal
- Letting FOMC content change a regime, threshold, or position (it is a background layer)

## Success metrics

- Forward-return distributions after panic and greed regimes
- Maximum drawdown relative to Buy & Hold
- Signal frequency and stability across periods; consistency across the four markets
- Interpretability; clear separation between causal rules, hindsight validation, diagnostics, and attribution
- Assistant: decision correctness, tool selection, citation precision, numeric faithfulness, zero look-ahead leakage (`AGENTS.md` *Eval priorities*)

## Unchanged behavior for future work

New specs must keep requirements 4–11 and the `AGENTS.md` principles unless the user explicitly
changes them. The dashboard pages' full-sample normalization and linear interpolation are
intentional for retrospective display (`AGENTS.md` §4).

## Known limitations

See README *Limitations* and `docs/architecture/baseline-review.md`. Not repeated here.
