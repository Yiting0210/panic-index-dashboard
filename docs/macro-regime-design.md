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

| Layer | Stage | Stores | Owner |
|---|---|---|---|
| FOMC RAG | exists | raw text, chunks, embeddings | retrieval |
| Policy state and statement diff | V1a | policy and balance-sheet facts parsed from statements; statement-to-statement changes | deterministic code |
| Narrative extraction | V1b | the Fed's description of inflation, growth, labor, rate-policy stance, and communication tone, with evidence spans | LLM, per document, frozen and versioned |
| Fed snapshots | V1c | per-meeting views assembled from the documents available on a date, and previous-vs-current comparison | deterministic code over V1a and V1b |
| Observed macro data | V2 | economic and market series by vintage, derived features, dimension states | deterministic code |
| Market expectations | V2.4 | pre-event rate-path probabilities / implied rates | deterministic code from market data |
| Macro regime | V3 | a classification of growth and inflation conditions | deterministic code |
| Investor interpretation | V1 page onward | explanation of transmission channels and asset sensitivity | deterministic mapping plus Research Assistant explanation |

RAG stays the evidence layer ("why did we reach that conclusion?"). Structured layers answer "what happened?", "how is it changing?", and, once market-pricing data is available, "was it already priced?".

The V1 architecture adds one branch beside the existing RAG path:

```text
fetch_fomc.py -> FOMC documents
      │
      ├── RAG corpus (exists): FAISS + BM25 -> Research Assistant
      │
      ├── deterministic parser (V1a)
      │      ├── policy facts
      │      ├── balance-sheet facts
      │      └── statement diff
      │
      └── LLM extractor (V1b)
             ├── inflation narrative
             ├── growth narrative
             ├── labor narrative
             ├── rate-policy stance and communication tone
             └── evidence spans
                    │
                    ▼
       versioned Fed snapshots (V1c, assembled by code)
                    │
                    ▼
              Fed Assessment page
```

A central design principle is to distinguish three different questions:

1. **Level:** what is the current state? For example, inflation is still elevated or policy is still restrictive.
2. **Direction:** how is that state changing? For example, inflation is falling or labor conditions are weakening.
3. **Surprise:** how did the outcome differ from what markets had priced immediately beforehand?

Level and direction are separate fields everywhere, with separate enums; a level value never describes a change, and a direction value never describes a state. Level and direction can be built from Fed documents and observed macro data. Surprise requires a separate point-in-time market-expectations source and must not be inferred from the tone of a document.

## 2. Constraints from AGENTS.md

These apply to every stage below.

- **Point in time.** Every record is indexed by the date it became public or, for market data, the timestamp/date at which it was observable. An `as_of` query sees only information available on or before `as_of`. Each stage adds look-ahead tests to `tests/test_point_in_time.py`.
- **Code owns facts.** Anything rendered as a structured fact (a table cell, an arrow, a regime name, a market-implied probability, or a surprise label) is computed by code, or is an LLM output that is extracted once, frozen, versioned, and labelled as a model interpretation. The Research Assistant receives these as read-only context and may explain them, not recompute them.
- **Deterministic before LLM.** Anything that can be parsed from the text with a testable rule (target range, decision, balance-sheet terms, sentence changes) is parsed by code in V1a and is never asked of the LLM.
- **No unruled aggregates.** No field summarizes several dimensions into one label (for example an overall Fed stance) until the rule that computes it is written in code and tested. Until then the field does not exist; the LLM never produces it.
- **Background layer until decided otherwise.** Nothing here changes the regime, the strategy path, or `plan_position`. Making macro state or market expectations an input to sizing is a separate decision (README Roadmap item 2), would be a strategy-math change, and needs its own backtest and a statement of how historical results change.
- **No uncalibrated confidence.** Do not show model self-reported confidence (`0.87`) or regime confidence percentages. V1c shows a `high / medium / low` support level computed by code from agreement between documents (section 7). Numeric confidence waits for a labelled evaluation set. Market-implied probabilities are allowed because they are derived from prices rather than model confidence, but they are risk-neutral probabilities produced by a fixed conversion method, not real-world probabilities; see *Probability method* in section 11.
- **No invented market expectations.** If a historical point-in-time expectations source is unavailable, the system may show a proxy such as the 2Y Treasury yield, but it must not convert that proxy into a fabricated probability of a hike or cut.

## 3. Current corpus

As of this proposal, `data/fomc/documents.jsonl` holds 139 documents (2021-01-27 to 2026-10-28): 48 statements, 46 minutes, 45 press conference transcripts. Fields: `doc_id`, `doc_type`, `meeting_date`, `published_at`, `speaker`, `title`, `text`, `url`.

Known issues that V1a must handle:

- The 2025-08-22 "statement" is the release announcing updates to the Statement on Longer-Run Goals and Monetary Policy Strategy, not a meeting statement. It matched the statement URL pattern. V1a must not parse it as a policy decision.
- Some press conference transcripts are missing (for example 2026-09-16); the daily job retries until the PDF is posted.
- The corpus starts in 2021: about 46 meetings, roughly one hiking cycle and one cutting cycle.

## 4. Availability per meeting

One meeting produces documents on different dates:

| Document | Public on |
|---|---|
| Statement | decision day D |
| Press conference | D (broadcast live; the transcript PDF may be posted later, but the content was public on D) |
| Minutes | about D + 21 days |

So the Fed's view "of the September meeting" is not one record. Between D and the minutes release, only the statement and press conference are visible. Any per-meeting view must be computed from the documents available on or before `as_of`, never from the full set. Minutes are never written back into the meeting-day view; they produce a later version (section 7).

### Two dates per document

Each document, and each record derived from it, carries two dates:

- **`available_date`**: when the content became public. This is the existing `published_at`: the release date for statements and minutes, and D for press conferences, because the conference was broadcast live. Historical `as_of` queries filter on this date.
- **`ingested_at`**: when this system stored the document. It is kept for audit and for live operation, not for historical filtering. For the 2021–2026 backfill every `ingested_at` is in 2026, so filtering history on it would hide everything.

The press conference case is a judgement call worth stating. Its content was public on D, but the transcript text the extractor reads may be posted days later. Using D matches what investors heard and matches the current corpus and `search_fed_docs`. The alternative, the PDF posting date, is stricter but needs a historical posting date the fetcher does not record. This design keeps D and lists the alternative as an open decision.

### Granularity

The system's market data is daily, so on day D the statement and the press conference are both visible and do not form a leakage boundary for daily research. The boundary that matters is the minutes release. No snapshot carries an intraday time, and none is invented. Statement and press conference snapshots are still kept separate (section 7), because the difference between them is a research question (what Powell added to the statement), not because of leakage.

Market expectations have an even stricter timing requirement. A policy surprise must compare the outcome with a snapshot from **before the decision was public**, not with end-of-day pricing after the announcement. If intraday historical snapshots are unavailable, the system must explicitly use the closest valid pre-event observation and label that limitation.

## 5. V1a: deterministic policy state and statement diff

This is README Roadmap item 1. Build it first. It needs no model, every field is testable, and it gives V1b a reliable baseline.

### Policy facts

Parsed by code from meeting statements, one record per statement, keyed by `available_date`:

- target range lower and upper bound
- decision: `hike / hold / cut`
- change in basis points
- dissents: count and names, with the stated alternative where given
- whether balance-sheet runoff continues
- Treasury and MBS runoff caps, where stated
- balance-sheet action at this meeting: `start / continue / slow / pause / end` of runoff
- latest meeting date and which document types are available for it as of a date

Each field cites the `doc_id` it came from.

### Statement diff

A sentence-level diff of each meeting statement against the previous meeting statement. FOMC statements are heavily templated, so the words that changed are the signal: a new sentence on employment risks, a dropped phrase about further firming, a changed description of inflation. The diff reports:

- sentences added, removed, and changed, with word-level changes inside changed sentences;
- changes in the policy facts above;
- the presence or absence of a fixed list of tracked phrases (for example "further policy firming", "extent and timing of additional adjustments"). The phrase list is a named constant with a test per phrase.

The diff does not label a change as hawkish or dovish. It shows what changed; interpretation belongs to V1b and is labelled as such.

### Query

A function `fed_state(as_of)` returns the latest policy facts and diff from statements available on or before `as_of`. It can later be exposed as an MCP tool alongside `search_fed_docs`.

### Tests

- Parser tests on fixed statement fixtures covering hikes, holds, cuts, runoff start, cap changes, and runoff end, plus the 2025-08-22 release (must not produce a decision).
- Look-ahead: `fed_state(D - 1)` does not show meeting D; `fed_state(D)` shows meeting D's statement.
- The diff for a meeting uses only the previous meeting statement.

## 6. V1b: LLM narrative extraction

An LLM reads **one document at a time** and records the Fed's description of the economy and of its own policy. Every value comes from a fixed enum defined in code, and every non-`unclear` value cites at least one evidence span.

### Taxonomy

| Dimension | Level field and values | Direction field and values |
|---|---|---|
| Inflation | `inflation_level`: `low / near_target / elevated / high` | `inflation_direction`: `rising / stable / falling / unclear` |
| Growth | `growth_level`: `strong / moderate / weak / contracting` | `growth_direction`: `strengthening / stable / weakening / unclear` |
| Labor | `labor_level`: `tight / balanced / weak` | `labor_direction`: `strengthening / stable / weakening / unclear` |
| Rate policy | `rate_policy_stance`: `restrictive / neutral / accommodative` | — (the decision itself is a V1a fact) |
| Communication | `communication_tone`: `hawkish / mildly_hawkish / neutral / mildly_dovish / dovish` | — |

Every field also accepts `unclear` (level fields included) when the document does not address the dimension. Minutes, for example, rarely restate the rate-policy stance; `unclear` is correct there, not a failure.

Notes on the taxonomy:

- **Level and direction never mix.** "Cooling" is not a labor level; it is `labor_level = balanced` with `labor_direction = weakening`.
- **`rate_policy_stance` and `communication_tone` are different questions.** Stance is where the Fed says policy sits relative to neutral (the Fed itself describes policy as "restrictive" or "modestly restrictive"). Tone is whether the communication leans toward tightening or easing ahead. Policy can be restrictive while the tone is dovish. Stance is a V1b field and not a V1a field because the target range alone cannot say whether policy is restrictive without an estimate of the neutral rate; V1b records the Fed's own description, with evidence.
- **Balance-sheet policy is not a V1b field.** V1a parses it from the statement. Liquidity and financial conditions as measured quantities (reserves, RRP, TGA, credit conditions) belong to V2; the V1 page never says that liquidity is improving or worsening.
- **Surprise is not a V1b field.** The LLM must not infer whether the Fed was "more hawkish than expected" from the document alone. Surprise belongs to the market-expectations layer because it is a comparison against pre-event prices.

Each dimension also has a short free-text `summary`, shown as the Fed's interpretation in the UI. The summary is never parsed or compared; trends and comparisons use only the enum fields.

### Evidence

Evidence is stored as rows, not as a string inside the assessment (schema in section 8). Each quote must be found verbatim in the cited document, after the same whitespace and dash normalization the fetcher applies; a quote that is not found fails the record. This is a machine-checkable contract, consistent with the validator rules.

### Storage rules

- Extract per document. Per-meeting views are assembled by code in V1c.
- Extract once and freeze. Store the model, prompt version (`analyzer_version`), and extraction time with every record. Do not regenerate silently; a new prompt version is a new set of records.
- Label the output in the UI as the model's reading of the Fed's language, not as a fact about the economy or about the Fed.

### Evaluation

Hand-label 15 to 20 meetings (every enum field, per document) as a gold set under `evals/datasets/`, and report per-field agreement in `evals/`. Choose the labelled meetings before looking at model output, and keep a held-out part, since the retrieval set had no held-out split.

## 7. V1c: versioned Fed snapshots

A snapshot is the Fed's view of one meeting as of the moment a new document for that meeting became available. Snapshots are assembled by code from V1a and V1b records; no model reads several documents together to produce one.

### Snapshot types

| `snapshot_type` | `source_set` | `available_date` |
|---|---|---|
| `statement` | statement | D |
| `post_press_conference` | statement, press conference | D |
| `post_minutes` | statement, press conference, minutes | minutes release date |

A meeting without a press conference has no `post_press_conference` snapshot, and its `post_minutes` source set omits it. A snapshot is never edited after it is written; a later document creates the next snapshot.

A query for meeting M as of a date returns the latest snapshot of M with `available_date <= as_of`. Because statement and press conference snapshots share D, a daily query on D returns `post_press_conference`; the `statement` snapshot remains available for narrative research.

### Combining documents

For each enum field, the snapshot value follows a fixed rule over the documents in its source set. A starting rule, to be confirmed in tests:

- if all documents that address the field agree, use that value;
- if they disagree, use the value from the most recent document and record the disagreement;
- documents with `unclear` do not count.

### Support level

Each dimension in a snapshot gets a `support_level` computed by code, not reported by the model:

| Level | Rule |
|---|---|
| `high` | at least two documents in the source set address the dimension and agree |
| `medium` | one document addresses it, or the documents differ by one adjacent enum step |
| `low` | no document addresses it, or documents disagree by more than one step or in direction |

The exact rules are named constants with tests. Numeric confidence waits for the gold set.

### Previous-vs-current comparison

For each dimension, compare the current snapshot with the latest snapshot of the previous meeting available at the same `as_of`. Report the change in level and direction, and show the evidence for both sides. For example:

```text
Labor
  previous (statement): labor_level = tight,    direction = stable
                        "job gains have remained strong"
  current  (statement): labor_level = balanced, direction = weakening
                        "downside risks to employment have risen"
  change: level down one step; direction stable -> weakening
```

The comparison reports the enum changes. Whether a change counts as "more dovish" is either a rule in code over those changes or is left to the explanation; the comparison does not ask the model.

### Tests

- Look-ahead: a snapshot of meeting M as of a date before its minutes release never includes the minutes, in any field or evidence row.
- Writing a `post_minutes` snapshot leaves the earlier snapshots of M unchanged.
- The combining and support-level rules give the expected values on fixed fixtures.

## 8. Storage schema

Three tables (or JSONL files beside the corpus; see open decisions).

`fomc_extraction`, one row per document and analyzer version:

```text
doc_id, meeting_date, doc_type, available_date, ingested_at,
inflation_level, inflation_direction,
growth_level, growth_direction,
labor_level, labor_direction,
rate_policy_stance, communication_tone,
inflation_summary, growth_summary, labor_summary, policy_summary,
analyzer_version, model, created_at
```

`fomc_evidence`, one row per quote:

```text
extraction_id, dimension, doc_id, quote, speaker, source_type
```

`speaker` is set for press conferences (Chair or reporter turn) and empty for statements and minutes.

`fomc_snapshot`, one row per meeting and snapshot type:

```text
meeting_date, snapshot_type, available_date, source_set (doc_ids),
<the enum fields above, combined>, support_level per dimension,
policy facts from V1a (target range, decision, change_bp, balance-sheet action),
analyzer_version, created_at
```

There is no `overall_fed_stance` column. If one is added later, it is computed by a documented rule from `rate_policy_stance`, `communication_tone`, and the inflation and labor fields, and the rule ships with its tests.

## 9. V1 page: Fed Assessment

A dedicated page, titled **Fed Assessment**. It is not called "Macro Assessment" or "Macro Economy" until V2 adds observed data (CPI, PCE, payrolls, GDP, financial conditions), because in V1 it shows only the Fed's interpretation, not the economy itself.

Top to bottom:

- **Current policy state (V1a):** target range, last decision and change, dissents, balance-sheet action and caps, which documents are available for the latest meeting.
- **Changes since previous FOMC:** the V1a statement diff, then the V1c previous-vs-current comparison per dimension with evidence on both sides. This section comes before any market interpretation.
- **Fed's view (V1c):** one row per dimension (inflation, growth, labor, rate-policy stance, communication tone) with level, direction, support level, and summary, expandable to the cited quotes. Marked as model interpretation.
- **Investor interpretation:** a concise, explicitly derived explanation of how the current Fed reading would normally transmit to yields, discount rates, and rate-sensitive equity styles. This is explanatory context, not a trade signal.
- **History:** a meeting-by-meeting grid of direction arrows per dimension, built from the snapshots available at the page's `as_of`, with the snapshot type shown for each cell. A **within-meeting** view shows statement -> press conference -> minutes for one meeting.

The grid is meant for questions such as "has the Fed become progressively more concerned about employment over the last four meetings?", which are easier to answer from structured history than by repeated retrieval. The within-meeting view answers "how did the narrative change from the statement to the press conference to the minutes?".

### Investor interpretation

The page should not stop at labels such as `inflation = elevated, falling`. Investors need the transmission channel. The structured display can expose a small number of descriptors, for example:

| Field | Example values | Derived from |
|---|---|---|
| Policy direction | tightening / steady / easing | V1a decision and target range |
| Inflation risk | rising / stable / falling | V1c inflation direction |
| Growth risk | improving / stable / deteriorating | V1c growth direction |
| Labor risk | tightening / balanced / weakening | V1c labor level and direction |
| Rate-pressure implication | upward / mixed / downward | the rows above |
| Long-duration valuation implication | restrictive / mixed / supportive | the rows above |

The mapping from inputs to descriptors is a lookup table of named constants in code, with a test per row, so the same inputs always give the same descriptor. The mapping is deterministic, but in V1 most of its inputs are V1b model readings, so every descriptor except policy direction inherits that status and is labelled in the UI as derived from model interpretation. Once V2 exists, the same descriptors can be computed from observed data and shown beside the Fed-derived ones.

These are not buy/sell labels. "Long-duration valuation implication" in particular describes the direction of one channel (discount rates), not an expected return or a view on any asset. They describe sensitivity under an explicit chain such as:

```text
inflation falling -> expected policy path falls -> Treasury yields fall
-> discount rates fall -> support for long-duration equity valuations
```

The explanation must also surface the main failure case:

```text
if rates are falling because growth or employment is deteriorating sharply,
lower discount rates may be offset by weaker earnings expectations.
```

This prevents the system from collapsing into the false rule "rate cuts are always bullish for stocks".

## 10. V2: observed data

V2 is where "Fed says" meets "data says". It adds objective series and keeps the same layering at every step:

```text
raw observations -> derived features -> dimension states -> (V3) macro regime
```

All three steps after the raw data are deterministic code. The LLM explains the results; it does not compute features, assign dimension states, or classify regimes. This continues the rule the project already follows for the strategy signal.

### Revisions and vintages

Economic data is revised; CPI, payrolls, and especially GDP change after first release. The FRED API returns the latest revised values by default, which would leak later information into historical `as_of` queries. V2 must use ALFRED vintages (the `realtime_start` / `realtime_end` parameters) and store:

```text
series_id, observation_date, vintage_date (release date), value, source, frequency, retrieved_at
```

An `as_of` query reads, for each observation, the latest vintage with `vintage_date <= as_of`.

Series that are not revised (daily Treasury yields) still need the publication date. H.4.1 is released weekly on Thursday for the week ending Wednesday and must be aligned to the release date, not the reference week.

### Derived features

Computed from vintage data available at `as_of`. Raw index levels (for example a CPI index value) are never used as features directly. For inflation: core CPI year-over-year and month-over-month, 3- and 6-month annualized rates, and their momentum. For labor: payroll 3-month average, unemployment trend, claims trend, job openings trend. For financial conditions: yield levels and curve slope, real yields, credit spreads, and balance-sheet, reserve, RRP, and TGA changes.

### Dimension states

Features map to the same level and direction enums as V1b where the concept is the same (inflation, growth, labor), so "Fed says" and "data says" can be shown side by side. Every threshold is a named constant with a test. V2 adds a **liquidity / financial conditions** dimension that V1 deliberately does not have, with values `tightening / neutral / easing` (and `strong_` variants if the thresholds support them).

The page then shows, per dimension:

```text
Labor        Fed says: balanced, weakening     Data says: balanced, weakening
Inflation    Fed says: elevated, falling       Data says: elevated, stable
```

A disagreement is a result to show, not an error to reconcile.

### Stages

**V2.1 — start with series that are cheap and safe.**

1. Treasury 2Y and 10Y yields and the 10Y - 2Y spread (daily, not revised).
2. Effective federal funds rate.
3. CPI, core CPI, unemployment rate, nonfarm payrolls (monthly, by vintage).
4. Core PCE and real GDP (by vintage; GDP is quarterly and heavily revised).
5. Fed total assets (H.4.1, weekly).

**V2.2** — initial jobless claims, retail sales, industrial production, JOLTS, real yields, credit spreads, RRP, reserve balances.

**V2.3** — replace FRED with the original publishers (BLS, BEA, Treasury, Federal Reserve, New York Fed) only where FRED is insufficient.

**V2.4** — market expectations and policy surprise (section 11). Starts only after open decisions 4 and 5 (section 14) settle a point-in-time source and the pre-FOMC snapshot rule. Until then the 2Y yield from V2.1 is the labelled proxy, and no surprise label is shown.

## 11. V2.4: market expectations and policy surprise

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

## 12. V3: macro regime and portfolio research

The first classifier stays simple and deterministic: growth direction by inflation direction.

| | Inflation falling | Inflation rising |
|---|---|---|
| Growth rising | Disinflationary expansion | Reflation |
| Growth falling | Disinflationary slowdown | Stagflation risk |

`Disinflationary expansion` is the more neutral system label for the environment commonly described in market commentary as a **Goldilocks / soft-landing** setup. The system should avoid using "soft landing" as the primary regime name because that phrase makes a stronger claim about future outcomes than the observed growth/inflation directions alone justify.

Labor, liquidity, and policy modify the description but not the quadrant.

The regime has two sources that can disagree, and the page shows both:

- a **Fed-derived** regime from the V1c snapshots (what the Fed says);
- a **data-derived** regime from the V2 dimension states (what the data shows).

Whether the regime ever becomes a portfolio input is the research question of V3, under README Roadmap item 2; it is not assumed here.

A divergence, for example the Fed describing inflation as easing while 3-month core inflation re-accelerates, is a result to show, not an error to reconcile.

Once market-expectations data exists, it is shown alongside the regime rather than folded into the quadrant. This keeps three distinct concepts separate:

```text
Fed view       = what policymakers say
Data regime    = what observed data shows
Market pricing = what investors already expect
```

Sample size limits what can be claimed. Market data starts in 2021-05 and covers roughly one tightening and one easing cycle. The regime is descriptive. Do not report forward returns by regime without the sample size beside every figure, and do not use it for sizing without the decision in README Roadmap item 2.

## 13. Research Assistant

The assistant receives `fed_state`, the Fed snapshots, observed macro state, and, when available, market expectations as read-only context. Retrieval remains the evidence layer.

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

## 14. Open decisions

1. Does V1b ship at all, or is V1a plus the statement diff enough for the page? Decide after V1a is in use.
2. Which model and prompt for V1b, and who labels the gold set.
3. Whether to store V1a, V1b, and V1c records in `data/fomc/` beside the corpus (committed, like the index) or in a database, and whether V1a is regenerated on load.
4. Which point-in-time source can provide historical Fed funds futures / SOFR / OIS expectations with sufficient pre-event timing fidelity.
5. If only daily expectations snapshots are available, what exact observation rule defines the pre-FOMC snapshot.
6. Whether to ingest the Fed SEP / dot plot as a structured Fed-projected rate path for the Fed vs Market Gap.
7. Which named thresholds map an actual-versus-expected rate difference to `dovish`, `roughly expected`, or `hawkish` surprise without over-interpreting small differences.
8. README Roadmap item 2: whether macro state or market expectations ever become an input to position sizing.
9. The V1c combining rule and support-level thresholds (section 7), confirmed against the gold set.
10. Whether a press conference's `available_date` stays D (content broadcast live) or becomes the transcript posting date, which would need the fetcher to record that date (section 4).
11. Whether an overall Fed stance is worth defining, and if so its rule over `rate_policy_stance`, `communication_tone`, and the inflation and labor fields (section 8).

## 15. Non-goals

- Replacing RAG with structured extraction.
- Buy/sell signals or position changes from the macro layer.
- Inferring market expectations from LLM tone analysis.
- Converting the 2Y Treasury proxy into fabricated meeting probabilities.
- Treating rate cuts as automatically bullish or hikes as automatically bearish for equities.
- Nowcasting or forecasting economic data.
- Changing the Panic Index, thresholds, or strategy math.
