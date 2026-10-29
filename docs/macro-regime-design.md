# Design: Fed Assessment and Macro Regime

Status: proposal. Nothing here is implemented. It expands README Roadmap item 1 (FOMC macro-state schema) and frames item 2 (whether macro state enters position sizing) without deciding it.

## 1. Goal

The Research Assistant can retrieve FOMC passages, but it cannot answer the question an investor asks most often: what macro environment is the market in, how is it changing, what was already priced in, and what does that mean for risk assets?

The system should separate four things that are often conflated:

- **what the Fed says** (FOMC statements, minutes, press conferences)
- **what the economic data shows** (CPI, PCE, payrolls, GDP, ...)
- **what markets are pricing** (Fed funds / SOFR expectations, Treasury yields, and later other market-implied paths)
- **what changed relative to expectations** (policy surprise: what happened versus what was priced immediately before the event)

Each layer has a different owner and a different failure mode, so each is stored and labelled separately.

| Layer | Stores | Owner |
|---|---|---|
| FOMC RAG (exists) | raw text, chunks, embeddings | retrieval |
| Fed state (V1a) | policy facts parsed from documents | deterministic code |
| Fed assessment (V1b) | interpretation of the Fed's view | LLM, frozen and versioned |
| Macro data (V2) | observed economic and market series, by vintage | deterministic code |
| Market expectations (V2.4) | pre-event rate-path probabilities / implied rates | deterministic code from market data |
| Macro regime (V2) | a classification of growth and inflation conditions | deterministic code |
| Investor interpretation | explanation of transmission channels and asset sensitivity | deterministic mapping plus Research Assistant explanation |

RAG stays the evidence layer ("why did we reach that conclusion?"). Structured layers answer "what happened?", "how is it changing?", and, once market-pricing data is available, "was it already priced?".

A central design principle is to distinguish three different questions:

1. **Level:** what is the current state? For example, inflation is still elevated or policy is still restrictive.
2. **Direction:** how is that state changing? For example, inflation is cooling or policy is becoming less restrictive.
3. **Surprise:** how did the outcome differ from what markets had priced immediately beforehand?

Level and direction can be built from Fed documents and observed macro data. Surprise requires a separate point-in-time market-expectations source and must not be inferred from the tone of a document.

## 2. Constraints from AGENTS.md

These apply to every stage below.

- **Point in time.** Every record is indexed by the date it became public or, for market data, the timestamp/date at which it was observable. An `as_of` query sees only information available on or before `as_of`. Each stage adds look-ahead tests to `tests/test_point_in_time.py`.
- **Code owns facts.** Anything rendered as a structured fact (a table cell, an arrow, a regime name, a market-implied probability, or a surprise label) is computed by code, or is an LLM output that is extracted once, frozen, versioned, and labelled as a model interpretation. The Research Assistant receives these as read-only context and may explain them, not recompute them.
- **Background layer until decided otherwise.** Nothing here changes the regime, the strategy path, or `plan_position`. Making macro state or market expectations an input to sizing is a separate decision (README Roadmap item 2), would be a strategy-math change, and needs its own backtest and a statement of how historical results change.
- **No uncalibrated confidence.** Do not show model self-reported confidence (`0.87`) or regime confidence percentages. Where support needs to be shown, use countable quantities, for example "2 of 3 documents for this meeting support this reading". Market-implied probabilities are allowed because they are derived from prices rather than model confidence, but they are risk-neutral probabilities produced by a fixed conversion method, not real-world probabilities; see *Probability method* in section 9.
- **No invented market expectations.** If a historical point-in-time expectations source is unavailable, the system may show a proxy such as the 2Y Treasury yield, but it must not convert that proxy into a fabricated probability of a hike or cut.

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

Market expectations have an even stricter timing requirement. A policy surprise must compare the outcome with a snapshot from **before the decision was public**, not with end-of-day pricing after the announcement. If intraday historical snapshots are unavailable, the system must explicitly use the closest valid pre-event observation and label that limitation.

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

**Surprise is intentionally not a V1b field.** The LLM must not infer whether the Fed was "more hawkish than expected" from the document alone. Surprise belongs to the market-expectations layer because it is a comparison against pre-event prices.

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
- **Investor interpretation:** a concise, explicitly derived explanation of how the current Fed reading would normally transmit to yields, discount rates, and rate-sensitive equity styles. This is explanatory context, not a trade signal.
- **History:** a meeting-by-meeting grid of direction arrows per dimension, built under the section 4 rule.

The grid is meant for questions such as "has the Fed become progressively more concerned about employment over the last four meetings?", which are easier to answer from structured history than by repeated retrieval.

### Investor interpretation

The page should not stop at labels such as `inflation = elevated, cooling`. Investors need the transmission channel. The structured display can expose a small number of descriptors, for example:

| Field | Example values | Derived from |
|---|---|---|
| Policy direction | tightening / steady / easing | V1a decision and target range |
| Inflation risk | rising / stable / falling | V1b inflation direction |
| Growth risk | improving / stable / deteriorating | V1b growth direction |
| Labor risk | tightening / balanced / weakening | V1b labor level and direction |
| Rate-pressure implication | upward / mixed / downward | the rows above |
| Long-duration valuation implication | restrictive / mixed / supportive | the rows above |

The mapping from inputs to descriptors is a lookup table of named constants in code, with a test per row, so the same inputs always give the same descriptor. The mapping is deterministic, but in V1 most of its inputs are V1b model readings, so every descriptor except policy direction inherits that status and is labelled in the UI as derived from model interpretation. Once V2 exists, the same descriptors can be computed from observed data and shown beside the Fed-derived ones.

These are not buy/sell labels. "Long-duration valuation implication" in particular describes the direction of one channel (discount rates), not an expected return or a view on any asset. They describe sensitivity under an explicit chain such as:

```text
inflation cooling -> expected policy path falls -> Treasury yields fall
-> discount rates fall -> support for long-duration equity valuations
```

The explanation must also surface the main failure case:

```text
if rates are falling because growth or employment is deteriorating sharply,
lower discount rates may be offset by weaker earnings expectations.
```

This prevents the system from collapsing into the false rule "rate cuts are always bullish for stocks".

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

**V2.4** — market expectations and policy surprise (section 9). Starts only after open decisions 4 and 5 (section 12) settle a point-in-time source and the pre-FOMC snapshot rule. Until then the 2Y yield from V2.1 is the labelled proxy, and no surprise label is shown.

### Derived features

Computed by code from vintage data available at `as_of`: year-over-year rates, 3- and 6-month annualized rates, payroll 3-month average, trend in unemployment and claims, yield-curve slope. Every threshold that maps a number to a label ("high", "cooling") is a named constant with a test.

## 9. Market expectations and policy surprise

This is a separate layer from both Fed communication and observed macro data.

### Why it exists

Markets react to the difference between an outcome and what was already priced, not simply to whether the Fed hiked, cut, or held.

A 25 bp cut can be:

- **roughly expected** if the market had already assigned most probability to a 25 bp cut;
- a **dovish surprise** if the market mostly expected a hold;
- a **hawkish disappointment** if the market had heavily priced a 50 bp cut.

Therefore the system must not equate `decision = cut` with `market reaction = bullish`.

### Preferred sources

For meeting-specific policy expectations, the preferred inputs are market prices tied directly to the expected policy path:

1. **Fed funds futures / CME FedWatch-style probabilities** for discrete target-rate outcomes by FOMC meeting.
2. **SOFR futures or OIS** for a richer expected path across meetings, if a reliable point-in-time historical source is available.
3. **2Y Treasury yield** only as a broad proxy for the expected policy path when the direct sources above are unavailable.

The 2Y yield must remain labelled as a proxy. It reflects policy expectations together with other components and cannot be translated directly into a meeting probability.

### Probability method

Meeting probabilities are not quoted by the market; they are computed from futures prices under assumptions. The common FedWatch-style method infers the expected rate after a meeting from the futures contract for that month and assumes only two adjacent outcomes per meeting and no risk premium. Consequences for this design:

- The result is a risk-neutral probability, not the market's real-world belief. A term premium or hedging demand shifts it.
- The conversion is implemented once in code, with the method, contract choice, and day-count handling documented, and tested against worked examples. It is not delegated to a model.
- The UI calls these "market-implied probabilities" and names the source and method.
- If the source already publishes probabilities, store the source's method as given and do not mix methods within one history.

### Storage

A future point-in-time expectations record should preserve the full pre-event distribution rather than only a single expected value. The values below are illustrative, not historical prices:

```json
{
  "meeting_date": "YYYY-MM-DD",
  "observed_at": "YYYY-MM-DDT13:55:00-04:00",
  "source": "fed_funds_futures",
  "outcomes": [
    {"move_bp": 0, "probability": 0.05},
    {"move_bp": -25, "probability": 0.30},
    {"move_bp": -50, "probability": 0.65}
  ]
}
```

If only daily snapshots are available, store the closest pre-meeting observation and label the timing limitation.

### Derived fields

Code may derive:

- modal expected move
- probability-weighted expected move
- actual move
- difference between actual and probability-weighted expected move
- categorical surprise label from named, tested thresholds
- change in the expected path before versus after the event, if point-in-time data supports it

For example (illustrative values):

```text
pre-meeting distribution:
  hold      10%
  cut 25bp  80%
  cut 50bp  10%

actual: cut 25bp
assessment: broadly expected
```

The probability distribution should remain visible because a single expected-basis-point number can hide an important split in market beliefs.

### Fed vs market gap

The page should distinguish **Fed expectations** from **market expectations**.

- The Fed's own projected policy path comes from the Summary of Economic Projections / dot plot when available.
- The market-implied path comes from futures / OIS or, temporarily, the stated 2Y proxy.

A useful derived view is the **Fed vs Market Gap**:

```text
Fed projected year-end policy rate
vs
market-implied year-end policy rate
```

This is not itself a trading signal. It identifies where market pricing is more dovish or more hawkish than the Fed's published path and therefore where repricing risk may be larger.

### Tests

- No post-announcement price may enter a pre-announcement expectation record.
- Historical `as_of` queries must return only expectations observed by that time.
- Probability distributions sum to approximately 1 within a defined tolerance.
- Surprise labels are computed from named constants, not free-form LLM judgment.
- If only a Treasury proxy exists, no meeting probability or discrete expected move is fabricated.

## 10. Macro regime

The first classifier stays simple and deterministic: growth direction by inflation direction.

| | Inflation falling | Inflation rising |
|---|---|---|
| Growth rising | Disinflationary expansion | Reflation |
| Growth falling | Disinflationary slowdown | Stagflation risk |

`Disinflationary expansion` is the more neutral system label for the environment commonly described in market commentary as a **Goldilocks / soft-landing** setup. The system should avoid using "soft landing" as the primary regime name because that phrase makes a stronger claim about future outcomes than the observed growth/inflation directions alone justify.

Labor, liquidity, and policy modify the description but not the quadrant.

The regime has two sources that can disagree, and the page shows both:

- a **Fed-derived** regime from V1b (what the Fed says), available in V1;
- a **data-derived** regime from V2 (what the data shows).

A divergence, for example the Fed describing inflation as easing while 3-month core inflation re-accelerates, is a result to show, not an error to reconcile.

Once market-expectations data exists, it is shown alongside the regime rather than folded into the quadrant. This keeps three distinct concepts separate:

```text
Fed view       = what policymakers say
Data regime    = what observed data shows
Market pricing = what investors already expect
```

Sample size limits what can be claimed. Market data starts in 2021-05 and covers roughly one tightening and one easing cycle. The regime is descriptive. Do not report forward returns by regime without the sample size beside every figure, and do not use it for sizing without the decision in README Roadmap item 2.

## 11. Research Assistant

The assistant receives `fed_state`, the Fed assessment, observed macro state, and, when available, market expectations as read-only context. Retrieval remains the evidence layer.

Its job is to explain, not to silently generate a new policy score or trading rule.

For investor-facing questions it should answer in this order when the inputs are available:

1. **Level:** what is the current Fed / macro state?
2. **Direction:** what has changed versus the previous meeting or release?
3. **Expectation:** what had markets priced before the event?
4. **Surprise:** was the outcome more hawkish, more dovish, or broadly in line with pricing?
5. **Transmission:** through which channels could this affect yields, discount rates, earnings expectations, and rate-sensitive assets?
6. **Risks / counter-case:** what could make the simple transmission story fail?

Example transmission:

```text
inflation falling -> expected policy rates fall -> yields fall -> discount rates fall
-> support for long-duration equity valuations
```

Required counter-case:

```text
if expected rates fall because growth is deteriorating sharply,
weaker earnings expectations can offset or dominate the valuation benefit.
```

The assistant should also make expectation gaps explicit. For example:

```text
The Fed is still restrictive but becoming less restrictive.
Markets had already priced a 25 bp cut, so the decision itself was not a major surprise.
The larger risk is the gap between the market-implied year-end path and the Fed's published path.
```

How these requirements are enforced follows AGENTS.md:

- The six-step order, the transmission example, and the counter-case are **prompt guidance**, measured in evals and by the judge. They are not runtime validator rules; an answer that skips the counter-case is a prose-quality issue, not a contract failure.
- Steps 3 and 4 restate code-owned facts. The assistant quotes the code-computed expectations and surprise label from its context and does not compute or infer a surprise itself. When no expectations source exists, it says the surprise is unknown rather than reasoning from the 2Y proxy or the document's tone.

It does not produce buy or sell instructions from the regime, and the macro context does not change any code-owned decision fact. The output schema (`summary`, `why`, `historical_context`, `risks`, `citations`) does not change.

## 12. Open decisions

1. Does V1b ship at all, or is V1a plus the statement diff enough for the page? Decide after V1a is in use.
2. Which model and prompt for V1b, and who labels the gold set.
3. Whether to store V1a records in `data/fomc/` beside the corpus (committed, like the index) or regenerate them on load.
4. Which point-in-time source can provide historical Fed funds futures / SOFR / OIS expectations with sufficient pre-event timing fidelity.
5. If only daily expectations snapshots are available, what exact observation rule defines the pre-FOMC snapshot.
6. Whether to ingest the Fed SEP / dot plot as a structured Fed-projected rate path for the Fed vs Market Gap.
7. Which named thresholds map an actual-versus-expected rate difference to `dovish`, `roughly expected`, or `hawkish` surprise without over-interpreting small differences.
8. README Roadmap item 2: whether macro state or market expectations ever become an input to position sizing.

## 13. Non-goals

- Replacing RAG with structured extraction.
- Buy/sell signals or position changes from the macro layer.
- Inferring market expectations from LLM tone analysis.
- Converting the 2Y Treasury proxy into fabricated meeting probabilities.
- Treating rate cuts as automatically bullish or hikes as automatically bearish for equities.
- Nowcasting or forecasting economic data.
- Changing the Panic Index, thresholds, or strategy math.
