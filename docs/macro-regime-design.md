# Design: Fed Assessment and Macro Regime

Status: proposal. Nothing here is implemented. It expands README Roadmap item 1 (FOMC macro-state schema) and frames item 2 (whether macro state enters position sizing) without deciding it.

## 1. Goal

The Research Assistant can retrieve FOMC passages, but it cannot answer the question an investor asks most often: what macro environment is the market in, how is it changing, and what does that mean for risk assets?

The system should separate three things that are often conflated:

- **what the Fed says** (FOMC statements, minutes, press conferences)
- **what the economic data shows** (CPI, PCE, payrolls, GDP, ...)
- **what markets are pricing** (Treasury yields, and later rate expectations)

Each layer has a different owner and a different failure mode, so each is stored and labelled separately.

| Layer | Stores | Owner |
|---|---|---|
| FOMC RAG (exists) | raw text, chunks, embeddings | retrieval |
| Fed state (V1a) | policy facts parsed from documents | deterministic code |
| Fed assessment (V1b) | interpretation of the Fed's view | LLM, frozen and versioned |
| Macro data (V2) | observed economic and market series, by vintage | deterministic code |
| Macro regime (V2) | a classification combining the above | deterministic code |

RAG stays the evidence layer ("why did we reach that conclusion?"). Structured layers answer "what happened?".

## 2. Constraints from AGENTS.md

These apply to every stage below.

- **Point in time.** Every record is indexed by the date it became public. An `as_of` query sees only records public on or before `as_of`. Each stage adds look-ahead tests to `tests/test_point_in_time.py`.
- **Code owns facts.** Anything rendered as a structured fact (a table cell, an arrow, a regime name) is computed by code, or is an LLM output that is extracted once, frozen, versioned, and labelled as a model interpretation. The Research Assistant receives these as read-only context and may explain them, not recompute them.
- **Background layer until decided otherwise.** Nothing here changes the regime, the strategy path, or `plan_position`. Making macro state an input to sizing is a separate decision (README Roadmap item 2), would be a strategy-math change, and needs its own backtest and a statement of how historical results change.
- **No uncalibrated confidence.** Do not show model self-reported confidence (`0.87`) or regime confidence percentages. Where support needs to be shown, use countable quantities, for example "2 of 3 documents for this meeting support this reading".

## 3. Current corpus

As of this proposal, `data/fomc/documents.jsonl` holds 139 documents (2021-01-27 to 2026-10-28): 48 statements, 46 minutes, 45 press conference transcripts. Fields: `doc_id`, `doc_type`, `meeting_date`, `published_at`, `speaker`, `title`, `text`, `url`.

Known issues that V1a must handle:

- The 2025-08-22 "statement" is the release announcing updates to the Statement on Longer-Run Goals and Monetary Policy Strategy, not a meeting statement. It matched the statement URL pattern. V1a must not parse it as a policy decision.
- Some press conference transcripts are missing (for example 2026-09-16); the daily job retries until the PDF is posted.
- The corpus starts in 2021: about 46 meetings, roughly one hiking cycle and one cutting cycle.

## 4. Release timing per meeting

One meeting produces documents on different dates:

| Document | Public on |
|---|---|
| Statement | decision day D |
| Press conference | D (broadcast live; the transcript PDF may be posted later, but the content was public on D) |
| Minutes | about D + 21 days |

So the Fed's view "of the September meeting" is not one record. Between D and the minutes release, only the statement and press conference are visible. Any per-meeting summary must be computed from the documents with `published_at <= as_of`, never from the full set. This applies to the history view too: a row for a meeting shown as of a date between D and D + 21 must not include the minutes.

## 5. V1a: deterministic Fed state

This is README Roadmap item 1. Build it first.

### Fields

Parsed by code from statements (and, where noted, other documents), one record per document, keyed by `published_at`:

- target range (lower, upper) and change in basis points
- decision: hike / cut / hold
- dissents: count and names, with the stated alternative where given
- balance-sheet decisions: start, change of pace, or end of runoff, with caps where stated
- latest meeting date and which document types exist for it as of the date
- statement diff: sentence-level changes against the previous meeting statement

Each field cites the `doc_id` it came from.

The statement diff is likely the most useful V1 feature. FOMC statements are heavily templated, so the words that changed between two statements are the signal (for example a change in how inflation is described, or a new sentence on labour risks). The diff is deterministic and needs no model.

### Query

A function `fed_state(as_of)` returns the latest state from documents published on or before `as_of`. It can later be exposed as an MCP tool alongside `search_fed_docs`.

### Tests

- Parser tests on fixed statement fixtures, including the 2025-08-22 release (must not produce a decision).
- Look-ahead: `fed_state(D - 1)` does not show meeting D; `fed_state(D)` shows the statement but no minutes of meeting D.
- The statement diff for a meeting uses only the previous published statement.

## 6. V1b: Fed assessment (model interpretation)

An LLM reads each document and records the Fed's view on four dimensions.

| Dimension | Question |
|---|---|
| Inflation | How does the Fed describe inflation pressure? |
| Growth | How does the Fed describe economic activity? |
| Labor | How does the Fed describe employment conditions? |
| Policy stance | Is the Fed signalling tighter or easier policy ahead? |

The draft also listed **liquidity**. FOMC documents say little about liquidity beyond balance-sheet decisions, which V1a already parses deterministically. Liquidity as a measured quantity (reserves, RRP, TGA) belongs to V2.

### Fields per dimension

```json
{
  "inflation": {
    "level": "elevated",
    "direction": "cooling",
    "summary": "Inflation remains above target but continues to moderate.",
    "evidence": [{"doc_id": "statement-20240918", "quote": "..."}]
  }
}
```

`level` and `direction` are separate because "high and falling" and "high and rising" have different policy implications. Both take values from fixed enums defined in code. `evidence` quotes must be found verbatim in the cited document (a machine-checkable contract, consistent with the validator rules).

### Storage rules

- Extract **per document**, not per meeting, so the point-in-time rule in section 4 holds. A per-meeting view as of a date is assembled by code from the documents visible on that date.
- Extract once and freeze. Store the model, prompt version, and extraction time with every record. Do not regenerate silently; a new prompt version is a new set of records.
- Label the output in the UI as the model's reading of the Fed's language, not as a fact about the economy or about the Fed.

### Evaluation

Hand-label 15 to 20 meetings (level and direction per dimension, per document) as a gold set under `evals/datasets/`, and report agreement in `evals/`. Choose the labelled meetings before looking at model output, and keep a held-out part, since the retrieval set had no held-out split.

## 7. V1 page: Fed Assessment

A dedicated page, titled **Fed Assessment**, not "Macro Economy", because it shows the Fed's interpretation, not the economy itself.

- **Current state (V1a):** target range, last decision, dissents, balance-sheet stance, document availability for the latest meeting.
- **What changed (V1a):** the statement diff against the previous meeting.
- **Fed's view (V1b):** one row per dimension with level, direction, and a short summary, expandable to the cited quotes. Marked as model interpretation.
- **History:** a meeting-by-meeting grid of direction arrows per dimension, built under the section 4 rule.

The grid is meant for questions such as "has the Fed become progressively more concerned about employment over the last four meetings?", which are easier to answer from structured history than by repeated retrieval.

## 8. V2: observed data

### Revisions and vintages

Economic data is revised; CPI, payrolls, and especially GDP change after first release. The FRED API returns the latest revised values by default, which would leak later information into historical `as_of` queries. V2 must use ALFRED vintages (the `realtime_start` / `realtime_end` parameters) and store:

```text
series_id, observation_date, vintage_date (release date), value, source, frequency, retrieved_at
```

An `as_of` query reads, for each observation, the latest vintage with `vintage_date <= as_of`.

Series that are not revised (daily Treasury yields) still need the publication date. H.4.1 is released weekly on Thursday for the week ending Wednesday and must be aligned to the release date, not the reference week.

### Stages

**V2.1 — start with series that are cheap and safe.**

1. Treasury 2Y and 10Y yields and the 10Y - 2Y spread (daily, not revised).
2. Effective federal funds rate.
3. CPI, core CPI, unemployment rate, nonfarm payrolls (monthly, by vintage).
4. Core PCE and real GDP (by vintage; GDP is quarterly and heavily revised).
5. Fed total assets (H.4.1, weekly).

**V2.2** — initial jobless claims, retail sales, industrial production, JOLTS, real yields, credit spreads, RRP, reserve balances.

**V2.3** — replace FRED with the original publishers (BLS, BEA, Treasury, Federal Reserve, New York Fed) only where FRED is insufficient.

### Derived features

Computed by code from vintage data available at `as_of`: year-over-year rates, 3- and 6-month annualized rates, payroll 3-month average, trend in unemployment and claims, yield-curve slope. Every threshold that maps a number to a label ("high", "cooling") is a named constant with a test.

### Market pricing

"What markets are pricing" needs a rate-expectations source. Fed funds futures and OIS are not available on FRED for free. Until a point-in-time source is chosen, use the 2Y Treasury yield as a stated proxy, and say so in the UI.

## 9. Macro regime

The first classifier stays simple and deterministic: growth direction by inflation direction.

| | Inflation falling | Inflation rising |
|---|---|---|
| Growth rising | Soft landing / Goldilocks | Reflation |
| Growth falling | Disinflationary slowdown | Stagflation |

Labor, liquidity, and policy modify the description but not the quadrant.

The regime has two sources that can disagree, and the page shows both:

- a **Fed-derived** regime from V1b (what the Fed says), available in V1;
- a **data-derived** regime from V2 (what the data shows).

A divergence, for example the Fed describing inflation as easing while 3-month core inflation re-accelerates, is a result to show, not an error to reconcile.

Sample size limits what can be claimed. Market data starts in 2021-05 and covers roughly one tightening and one easing cycle. The regime is descriptive. Do not report forward returns by regime without the sample size beside every figure, and do not use it for sizing without the decision in README Roadmap item 2.

## 10. Research Assistant

The assistant receives `fed_state`, the Fed assessment, and (in V2) the macro regime as read-only context, and keeps retrieval for evidence. It explains transmission channels, for example:

```text
inflation falling -> expected policy rates fall -> yields fall -> discount rates fall
-> support for long-duration equity valuations
```

It does not produce buy or sell instructions from the regime, and the macro context does not change any code-owned decision fact. The output schema (`summary`, `why`, `historical_context`, `risks`, `citations`) does not change.

## 11. Open decisions

1. Does V1b ship at all, or is V1a plus the statement diff enough for the page? Decide after V1a is in use.
2. Which model and prompt for V1b, and who labels the gold set.
3. Whether to store V1a records in `data/fomc/` beside the corpus (committed, like the index) or regenerate them on load.
4. A point-in-time source for rate expectations.
5. README Roadmap item 2: whether macro state ever becomes an input to position sizing.

## 12. Non-goals

- Replacing RAG with structured extraction.
- Buy/sell signals or position changes from the macro layer.
- Nowcasting or forecasting economic data.
- Changing the Panic Index, thresholds, or strategy math.
