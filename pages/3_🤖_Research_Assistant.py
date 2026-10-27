import json
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
        "placeholder": "sk-ant-...",
        "keys_url": "https://console.anthropic.com/settings/keys",
    },
    "DeepSeek": {
        "id": "deepseek",
        "models": ["deepseek-flash", "deepseek-v4-pro"],
        "placeholder": "sk-...",
        "keys_url": "https://platform.deepseek.com/api_keys",
    },
}
EXAMPLES = [
    "What exposure does the strategy hold right now, and why?",
    "Given the exposure I entered, what does the rule do today, and why?",
    "Compare what the rule does today from 0%, 50%, and 100% starting exposure.",
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
    """MCP server and response cache, shared by all sessions."""
    from assistant import ResponseCache
    from mcp_server import server

    return server, ResponseCache()


class NoKeyClient:
    """Stands in for the provider client when no key was entered.

    The page never falls back to a key from the environment: any model call
    raises, and the agent answers with the rule-based summary (or a cached
    response to the identical request).
    """

    def __getattr__(self, name):
        raise RuntimeError("no API key entered")


def build_agent(provider, model, api_key):
    """A per-request agent; a visitor's key never leaves this session's memory.

    Only a key typed into the page is used. Calls are logged as
    billed_to="user".
    """
    import anthropic
    import openai

    from assistant import DeepSeekAgent, ResearchAgent, UsageLedger
    from assistant.agent import DEEPSEEK_BASE_URL

    server, cache = shared_resources()
    ledger = UsageLedger(daily_budget_usd=float("inf"), billed_to="user")
    if not api_key:
        client = NoKeyClient()
    elif provider == "deepseek":
        client = openai.OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
    else:
        client = anthropic.Anthropic(api_key=api_key)
    agent_cls = DeepSeekAgent if provider == "deepseek" else ResearchAgent
    return agent_cls(server=server, client=client, model=model, cache=cache, ledger=ledger)


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
if api_key:
    st.caption("Using your key. Spend is shown per answer and billed to your account.")
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

# ── Question ──────────────────────────────────────────────────────────────────
example = st.selectbox("Example questions", ["(write your own)"] + EXAMPLES)
question = st.text_area(
    "Question",
    value="" if example == "(write your own)" else example,
    height=90,
)
# The user's own exposure goes to code, never through the model: the
# your-position decision is plan_position applied to this number.
own_position = st.number_input(
    "Your current exposure (%), optional", value=None, placeholder="leave blank to skip",
    min_value=float(strategy.min_position_pct), max_value=float(strategy.max_position_pct),
    step=5.0,
    help="The rule's step from this position is computed by code and shown under "
         "Decision Facts. The model explains it but never reads or restates the number.")
ask = st.button("Ask", type="primary", disabled=not question.strip())

if ask:
    full_question = f"{question.strip()} (Market: {market})"
    agent = build_agent(provider["id"], model, api_key)
    with st.spinner("Calling tools…"):
        result = agent.run(full_question, as_of, market=market, strategy=strategy,
                           current_position_pct=own_position)
    st.session_state["last_result"] = result

result = st.session_state.get("last_result")
if result is not None:
    if result.violations:
        st.warning(
            "The model's explanation broke the answer contract, so it was replaced "
            "with the rule-based research summary. See Advanced / Debug."
        )
    elif result.used_fallback:
        st.warning(
            "LLM path unavailable, so this is the rule-based research summary. "
            f"Reason: {result.fallback_reason}"
        )

    # ── Decision facts: computed by code, never written by the model ─────────
    decision = result.decision
    if decision is not None:
        from assistant import boundary_state, position_plans

        signal = decision.signal
        st.subheader("Decision / Strategy Facts")
        st.caption("Computed by code from the tools. The AI analysis below explains "
                   "these facts and cannot change them.")
        regime_col, path_col, own_col = st.columns(3)
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
        with path_col.container(border=True):
            st.markdown("**Strategy Path**")
            st.metric(
                "Strategy path exposure", f"{signal.final_position_pct:g}%",
                help=(f"Exposure reached by following the standard rule continuously from "
                      f"{signal.backtest_start:%Y-%m-%d} through the latest observation "
                      "(run_backtest.final_position_pct). This is not a recommendation to "
                      "move directly to this exposure from a different starting position "
                      "today."))
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
        with own_col.container(border=True):
            st.markdown("**Your Position**")
            plan = decision.plan
            if plan is None:
                st.caption("No current exposure entered. Enter one above the Ask button "
                           "to see the rule's step from your own position.")
            else:
                st.metric("Position after today's rule", f"{plan.target_position_pct:g}%",
                          delta=f"{plan.applied_change_pct:+g}%" if plan.applied_change_pct else None,
                          help="tools.plan_position: the latest regime applied once to "
                               "the exposure you entered. No history is replayed.")
                boundary = f" ({decision.plan_boundary})" if decision.plan_boundary else ""
                st.markdown(f"From your **{plan.current_position_pct:g}%**: today's rule "
                            f"action is **{plan.action}**{boundary}.  \n"
                            f"Next: {plan.next_rule.split(': ', 1)[1]}.")
        st.caption(f"**Why this signal?** {signal.provenance}.")

        plan_strategy = result.strategy or DEFAULT_STRATEGY
        with st.expander("The rule's step from common starting positions"):
            st.dataframe(
                [{"Starting position": f"{p.current_position_pct:g}%",
                  "Today's rule action": p.action,
                  "Position after today's rule": f"{p.target_position_pct:g}%",
                  "Cap / floor": boundary_state(p) or "",
                  "Next trigger": p.next_rule.split(": ", 1)[1]}
                 for p in position_plans(signal.market, signal.as_of,
                                         strategy=plan_strategy)],
                hide_index=True, width="stretch")
            st.caption(f"{signal.regime.title()} regime on {signal.observation_date:%b %d, %Y}, "
                       "applied once to each starting position. The strategy path exposure "
                       "is not a target for these rows.")

    # ── AI analysis: written by the model ─────────────────────────────────────
    st.subheader("Rule-based summary" if result.used_fallback else "AI Analysis")
    st.markdown(result.answer)
    if result.key_reasons:
        st.markdown("**Why**")
        for reason in result.key_reasons:
            st.markdown(f"- {reason}")
    if result.historical_context:
        st.markdown("**Historical context**")
        st.markdown(result.historical_context)

    # Scenarios the model asked for, as code-rendered rows with their inputs.
    scenarios = [c.result for c in result.tool_calls
                 if c.name == "plan_position" and c.result and not c.is_error]
    if scenarios and not result.used_fallback:
        st.markdown("**Scenarios computed for this question** (by code)")
        st.dataframe(
            [{"Input position": f"{s['current_position_pct']:g}%",
              "Today's rule action": s["action"],
              "Position after today's rule": f"{s['target_position_pct']:g}%",
              "Next trigger": s["next_rule"].split(": ", 1)[1]}
             for s in scenarios],
            hide_index=True, width="stretch")
    risks = [r for r in result.risks if r.strip() and r.strip().lower() != "none"]
    if risks:
        st.markdown("**Risks / Caveats**")
        for risk in risks:
            st.markdown(f"- {risk}")

    retrieved = {}
    for call in result.tool_calls:
        if call.name == "search_fed_docs" and call.result:
            for hit in call.result.get("results", []):
                retrieved[hit["doc_id"]] = hit
    if result.citations:
        st.markdown("**Sources**")
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
        if decision is not None:
            st.markdown("**Decision facts given to the model (read-only)**")
            st.json(decision.as_dict(), expanded=False)
        if result.repairs and not result.violations:
            st.markdown("**Revised once** after the contract check found:")
            for violation in result.first_violations:
                st.markdown(f"- {violation}")
        for violation in result.violations:
            st.markdown(f"- Guardrail: {violation}")
        rendered = sum(r.count for r in result.rendered)
        if result.raw_answer:
            st.markdown(f"**What-if figures rendered by code:** {rendered}")
            st.caption("Model text before rendering: " + result.raw_answer["summary"])
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
