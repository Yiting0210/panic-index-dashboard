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
# Provider label -> settings. Each provider has its own agent class.
PROVIDERS = {
    "Claude (Anthropic)": {
        "id": "anthropic",
        "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
        "env": "ANTHROPIC_API_KEY",
        "placeholder": "sk-ant-...",
        "keys_url": "https://console.anthropic.com/settings/keys",
    },
    "DeepSeek": {
        "id": "deepseek",
        "models": ["deepseek-flash", "deepseek-v4-pro"],
        "env": "DEEPSEEK_API_KEY",
        "placeholder": "sk-...",
        "keys_url": "https://platform.deepseek.com/api_keys",
    },
}
EXAMPLES = [
    "What Panic Index regime is the market in, and what did the latest FOMC statement say?",
    "How did the threshold strategy compare with Buy & Hold, including drawdown?",
    "What were 1-month and 3-month forward returns after panic-regime days?",
    "Did the causal panic-reversal detector fire recently? On which dates?",
]


@st.cache_resource
def shared_resources():
    """MCP server, response cache, and the owner-key ledger, shared by all sessions."""
    from assistant import ResponseCache, UsageLedger
    from mcp_server import server

    budget = float(os.getenv("PANIC_LLM_DAILY_BUDGET_USD", "1.00"))
    return server, ResponseCache(), UsageLedger(daily_budget_usd=budget)


def build_agent(provider, model, api_key):
    """A per-request agent; a visitor's key never leaves this session's memory.

    Visitor keys are logged as billed_to="user" and do not consume the app
    owner's daily budget. Without a visitor key the owner's key from the
    environment is used, subject to that budget.
    """
    import anthropic

    from assistant import DeepSeekAgent, ResearchAgent, UsageLedger

    server, cache, owner_ledger = shared_resources()
    ledger = (UsageLedger(daily_budget_usd=float("inf"), billed_to="user")
              if api_key else owner_ledger)
    if provider == "deepseek":
        return DeepSeekAgent(server=server, api_key=api_key or None, model=model,
                             cache=cache, ledger=ledger)
    client = anthropic.Anthropic(api_key=api_key) if api_key else None
    return ResearchAgent(server=server, client=client, model=model, cache=cache, ledger=ledger)


# ── Header ────────────────────────────────────────────────────────────────────
st.title("🤖 Research Assistant")
st.markdown(
    "Ask questions about Panic Index regimes, backtests, forward returns, and "
    "FOMC communications. The assistant is a client of the project's **MCP "
    "server**: it calls the same tools Claude Code or Cursor would, and every "
    "tool call is pinned to the **as-of date** below, so answers only use data "
    "and Fed documents that were public on that day."
)

# ── Model provider and API key ────────────────────────────────────────────────
provider_col, model_col = st.columns([2, 2])
provider_label = provider_col.selectbox("Model provider", list(PROVIDERS))
provider = PROVIDERS[provider_label]
model = model_col.selectbox("Model", provider["models"])
api_key = st.text_input(
    f"{provider_label} API key",
    type="password",
    placeholder=provider["placeholder"],
    help=(
        f"Create a key at {provider['keys_url']}. The key is used only for this "
        "browser session: it is kept in memory, never written to disk, cached, "
        "or logged, and requests are billed to your account."
    ),
).strip()
owner_key = bool(os.getenv(provider["env"]))
if api_key:
    st.caption("Using your key. Spend is shown per answer and billed to your account.")
elif owner_key:
    st.caption("No key entered: using the app's key, limited by its daily budget.")
else:
    st.caption(
        "No key entered: answers fall back to the deterministic rule-based summary "
        "unless the same question was answered before."
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

if not api_key and owner_key:
    owner_ledger = shared_resources()[2]
    remaining = max(owner_ledger.daily_budget_usd - owner_ledger.spent_today(), 0.0)
    st.sidebar.caption(f"App key daily budget remaining: ${remaining:.2f}")

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
    agent = build_agent(provider["id"], model, api_key)
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
