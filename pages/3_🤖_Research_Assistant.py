import os

import streamlit as st

from utils import add_sidebar_nav, load_data
from utils.sidebar import render_sidebar_header

st.set_page_config(layout="wide", page_title="Research Assistant")

# ── Load data ─────────────────────────────────────────────────────────────────
df = load_data()
min_date = df['date_dt'].min().date()
max_date = df['date_dt'].max().date()

MARKET_LABELS = {
    "QQQ": "Nasdaq 100 (QQQ)",
    "SMH": "Semiconductors (SMH)",
    "SPX": "S&P 500 (SPX)",
    "DJI": "Dow Jones (DJI)",
}
EXAMPLES = [
    "What Panic Index regime is the market in, and what did the latest FOMC statement say?",
    "How did the threshold strategy compare with Buy & Hold, including drawdown?",
    "What were 1-month and 3-month forward returns after panic-regime days?",
    "Did the causal panic-reversal detector fire recently? On which dates?",
]


@st.cache_resource
def get_agent():
    from assistant import ResearchAgent, UsageLedger

    budget = float(os.getenv("PANIC_LLM_DAILY_BUDGET_USD", "1.00"))
    return ResearchAgent(ledger=UsageLedger(daily_budget_usd=budget))


# ── Header ────────────────────────────────────────────────────────────────────
st.title("🤖 Research Assistant")
st.markdown(
    "Ask questions about Panic Index regimes, backtests, forward returns, and "
    "FOMC communications. The assistant is a client of the project's **MCP "
    "server**: it calls the same tools Claude Code or Cursor would, and every "
    "tool call is pinned to the **as-of date** below, so answers only use data "
    "and Fed documents that were public on that day."
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
add_sidebar_nav()
st.sidebar.markdown("---")
render_sidebar_header("Research Context", "🗓️")
as_of = st.sidebar.date_input(
    "As-of date", value=max_date, min_value=min_date, max_value=max_date,
    help="Information cutoff. Tools never read market data or documents after this date.",
)
market = st.sidebar.selectbox(
    "Default market", list(MARKET_LABELS), format_func=MARKET_LABELS.get,
    help="Used for the rule-based fallback summary and as a hint in the question.",
)

agent = get_agent()
remaining = max(agent.ledger.daily_budget_usd - agent.ledger.spent_today(), 0.0)
st.sidebar.caption(f"Model: `{agent.model}` · daily budget remaining: ${remaining:.2f}")
if not os.getenv("ANTHROPIC_API_KEY"):
    st.sidebar.info(
        "No ANTHROPIC_API_KEY found. Answers fall back to the deterministic "
        "rule-based summary unless the response is already cached."
    )

# ── Question ──────────────────────────────────────────────────────────────────
example = st.selectbox("Example questions", ["(write your own)"] + EXAMPLES)
question = st.text_area(
    "Question",
    value="" if example == "(write your own)" else example,
    height=90,
)
ask = st.button("Ask", type="primary", disabled=not question.strip())

if ask:
    full_question = f"{question.strip()} (Market: {market})"
    with st.spinner("Calling tools…"):
        result = agent.run(full_question, as_of, market=market)
    st.session_state["last_result"] = result

result = st.session_state.get("last_result")
if result is not None:
    if result.used_fallback:
        st.warning(
            "LLM path unavailable, so this is the rule-based research summary. "
            f"Reason: {result.fallback_reason}"
        )
    st.subheader("Answer")
    st.markdown(result.answer)
    if result.caveats and result.caveats.lower() != "none":
        st.caption(f"Caveats: {result.caveats}")

    retrieved = {}
    for call in result.tool_calls:
        if call.name == "search_fed_docs" and call.result:
            for hit in call.result.get("results", []):
                retrieved[hit["doc_id"]] = hit
    if result.citations:
        st.subheader("Sources")
        for citation in result.citations:
            hit = retrieved.get(citation["doc_id"])
            if hit:
                st.markdown(
                    f"- [{hit['title']}]({hit['url']}) — released {hit['published_at']}"
                )
            else:
                st.markdown(f"- `{citation['doc_id']}` (not returned by a tool in this run)")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tool calls", len(result.tool_calls))
    c2.metric("Model steps", result.steps)
    c3.metric("Latency", f"{result.latency_ms / 1000:.1f}s")
    c4.metric("Cost", f"${result.cost_usd:.4f}",
              help=f"{result.input_tokens} input / {result.output_tokens} output tokens, "
                   f"{result.cache_read_tokens} cache-read tokens, "
                   f"{result.cached_responses} cached responses")

    if result.tool_calls:
        st.subheader("Tool calls")
        for call in result.tool_calls:
            label = f"{call.name}({', '.join(f'{k}={v}' for k, v in call.arguments.items())})"
            if call.as_of_clamped:
                label += "  · as_of clamped"
            with st.expander(label):
                st.json(call.result)

st.caption(
    "Historical research only, not investment advice. As of "
    f"{as_of:%b %d, %Y}; market data available through {max_date:%b %d, %Y}."
)
