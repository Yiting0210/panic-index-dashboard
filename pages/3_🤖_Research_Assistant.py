import json
import os
from datetime import date
from pathlib import Path

import streamlit as st

from utils import add_sidebar_nav, load_data
from utils.sidebar import EMOJI_FONTS, render_sidebar_header, strategy_settings
from utils.strategy import DEFAULT_STRATEGY, StrategyParams

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
    "What exposure does the strategy hold right now, and why?",
    "Is the current regime historically attractive for adding exposure?",
    "What would make the rule add or reduce exposure from here?",
    "How does current Fed policy context relate to the regime?",
    "How did the threshold strategy compare with Buy & Hold, including drawdown?",
]
FOMC_MANIFEST = Path(__file__).resolve().parent.parent / "data" / "fomc" / "index" / "manifest.json"


def latest_fomc_date():
    """Release date of the newest FOMC document in the search index, or None."""
    try:
        manifest = json.loads(FOMC_MANIFEST.read_text(encoding="utf-8"))
        return date.fromisoformat(manifest["last_published"])
    except (OSError, KeyError, ValueError):
        return None


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
# The heading font has its own monochrome 🤖 glyph; draw the icon in a colour
# emoji font so it matches the browser-tab icon.
st.markdown(
    f"# <span style=\"font-family: {EMOJI_FONTS}; font-weight: 400\">🤖</span> "
    "Research Assistant",
    unsafe_allow_html=True,
)
st.markdown(
    "Ask questions about Panic Index regimes, backtests, forward returns, and "
    "FOMC communications. The assistant is a client of the project's **MCP "
    "server**: it calls the same tools Claude Code or Cursor would, and every "
    "tool call is pinned to the **as-of date** below, so answers only use data "
    "and Fed documents that were public on that day."
)
fomc_through = latest_fomc_date()
st.caption(
    f"📅 Market data through **{max_date:%b %d, %Y}** · FOMC library through "
    + (f"**{fomc_through:%b %d, %Y}**" if fomc_through else "**unknown**")
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
strategy = StrategyParams(**strategy_settings())
st.sidebar.markdown("---")
render_sidebar_header("Strategy Settings", "⚙️")
st.sidebar.caption(
    ("Defaults" if strategy == DEFAULT_STRATEGY else "From the Threshold Strategy page")
    + f": {strategy.describe()}. Change them on the Threshold Strategy page."
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
        result = agent.run(full_question, as_of, market=market, strategy=strategy)
    st.session_state["last_result"] = result

result = st.session_state.get("last_result")
if result is not None:
    if result.violations:
        st.warning(
            "The model's explanation contradicted the strategy signal, so it was "
            "replaced with the rule-based research summary. See Advanced / Debug."
        )
    elif result.used_fallback:
        st.warning(
            "LLM path unavailable, so this is the rule-based research summary. "
            f"Reason: {result.fallback_reason}"
        )

    # Market Regime and Strategy Signal are filled by code from the standard
    # backtest; the model only writes the explanation below them.
    signal = result.signal
    if signal is not None:
        regime_col, signal_col = st.columns(2)
        with regime_col.container(border=True):
            st.markdown("**Market Regime**")
            st.metric(f"{signal.market} Panic Index", f"{signal.panic_index:.1f}",
                      help="Composite Panic Index on the latest observation.")
            st.markdown(
                f"**{signal.regime.title()}** regime (panic above {signal.buy_threshold:g}, "
                f"greed below {signal.sell_threshold:g}); "
                f"{signal.panic_index_percentile:g}th percentile of history to date."
            )
            st.caption(f"Latest observation {signal.observation_date:%b %d, %Y}")
        with signal_col.container(border=True):
            st.markdown("**Strategy Signal**")
            st.metric("Current exposure", f"{signal.final_position_pct:g}%",
                      help="final_position_pct of the standard backtest (0-100).")
            st.markdown(f"Latest action: **{signal.action}** — {signal.action_text}.  \n"
                        f"Next session: {signal.next_rule}.")
            st.caption(
                f"Standard backtest {signal.backtest_start:%b %d, %Y} – "
                f"{signal.observation_date:%b %d, %Y}: strategy "
                f"{signal.strategy_return_pct:+.1f}% vs. Buy & Hold "
                f"{signal.buyhold_return_pct:+.1f}%; max drawdown "
                f"{signal.strategy_max_drawdown_pct:.1f}% vs. "
                f"{signal.buyhold_max_drawdown_pct:.1f}%."
            )
        st.caption(f"**Why this signal?** {signal.provenance}.")

    st.subheader("Rule-based summary" if result.used_fallback else "Key Reasons")
    st.markdown(result.answer)
    for reason in result.key_reasons:
        st.markdown(f"- {reason}")
    if result.caveats and result.caveats.lower() != "none":
        st.subheader("Risks / Caveats")
        st.markdown(result.caveats)

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

    with st.expander("Advanced / Debug"):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Tool calls", len(result.tool_calls))
        c2.metric("Model steps", result.steps)
        c3.metric("Latency", f"{result.latency_ms / 1000:.1f}s")
        c4.metric("Cost", f"${result.cost_usd:.4f}",
                  help=f"{result.input_tokens} input / {result.output_tokens} output tokens, "
                       f"{result.cache_read_tokens} cache-read tokens, "
                       f"{result.cached_responses} cached responses")
        if result.fallback_reason:
            st.markdown(f"**Fallback reason:** {result.fallback_reason}")
        for violation in result.violations:
            st.markdown(f"- Guardrail: {violation}")
        for call in result.tool_calls:
            label = f"{call.name}({', '.join(f'{k}={v}' for k, v in call.arguments.items())})"
            if call.as_of_clamped:
                label += "  · as_of clamped"
            st.markdown(f"**{label}**")
            st.json(call.result, expanded=False)

st.caption(
    "Historical research only, not investment advice. As of "
    f"{as_of:%b %d, %Y}; market data available through {max_date:%b %d, %Y}."
)
