"""Research agent: Claude or DeepSeek tool use over the Panic Index MCP server.

The agent connects to the MCP server in-process with ``mcp.Client``, so it
sees exactly the tool names, descriptions, and JSON schemas any other MCP
client sees. Three guardrails sit around the model:

* as-of enforcement: every tool call's ``as_of`` is clamped to the session
  cutoff, so the model cannot read later data even if it asks for it;
* cost control: identical requests are served from a response cache, every
  call is logged to the usage ledger, and a daily budget stops new calls;
* graceful degradation: on API errors, refusals, or an exhausted budget the
  agent returns the deterministic rule-based summary instead.

Decisions are not left to the model: the decision facts (the strategy path
from one standard backtest, and the rule's step from the position the user
entered on the page) are computed in code (assistant/presentation.py),
rendered by the page, and given to the model as read-only context. The model
writes the explanation; a contract check (assistant/validator.py) blocks
restated position figures, placeholder errors, and bad citations, with one
repair turn and then the rule-based summary.
"""
import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace
from typing import Optional

import anthropic
import openai
from mcp import Client

from tools import rule_based_summary
from tools.market import ToolInputError, to_date
from utils.strategy import StrategyParams

from .cache import ResponseCache, request_hash
from .ledger import BudgetExceeded, UsageLedger
from .presentation import DecisionFacts, build_decision
from .render import render
from .signal import StrategySignal
from .validator import validate_answer

DEFAULT_MODEL = os.getenv("PANIC_LLM_MODEL", "claude-opus-5-5")
DEFAULT_EFFORT = os.getenv("PANIC_LLM_EFFORT", "medium")
# Models that accept server-side refusal fallbacks with the "default" routing.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-flash"

SYSTEM_PROMPT = """\
You are a market-regime research assistant for the Composite Panic Index
dashboard (VIX 50% + inverted CNN Fear & Greed 50%) covering QQQ, SMH, SPX,
and DJI, with a searchable library of FOMC statements, minutes, and Chair
press conference transcripts.

Each question comes with an as_of date. Answer as an analyst standing on that
date:
- Get every fact from the tools. Do not use your own knowledge of markets or
  Federal Reserve decisions, because it may include events after as_of.
- Pass the session's as_of to every tool call (later dates are clamped).
- Copy numbers from tool results; round only to the precision shown.
- For any claim about Federal Reserve policy, call search_fed_docs and cite
  the doc_id of each passage you rely on. Cite only doc_ids that a tool
  returned in this conversation.
- Pick the source that fits the claim: press conferences for how the Chair
  explained a decision, minutes for the range of views on the Committee,
  statements for the decision itself. Name the Chair from the passage's
  speaker field; the Chair changed during the period covered.
- If the tools cannot answer the question, say so instead of guessing.
- This is historical research, not investment advice; say so briefly when
  the user asks what to buy or sell.
- The question comes with decision facts computed by code: the strategy
  path (one standard backtest) and, when the user entered a position on the
  page, the rule's step from that position. The app shows them to the user
  above your answer. They are authoritative: explain why they are what they
  are, and do not recalculate, reinterpret, override, or restate them. Do
  not write the strategy path exposure, the user's position, the rule's
  action, the applied change, or the resulting position; the app shows them.
- The strategy path exposure is where the rule stands after following it
  since the standard backtest start. It is not a target for someone who
  holds a different position; never present it as the user's position.
- Research values from the tools are fine when useful: Panic Index, VIX,
  Fear & Greed, percentiles, forward returns, win rates, drawdowns, FOMC
  dates. Do not put a raw percentage in the same clause as the words
  exposure, position, allocation, or invested; give it its own clause.
- Do not invent sizing, scaling levels, or a mapping from percentile to
  position.
- For a what-if (other thresholds, start date, or step sizes), call
  run_backtest with those parameters, label the result as a what-if, and
  write its figures as placeholders with the call_id shown above its result;
  the app fills in the value and unit: {bt1.final_position_pct}
  {bt1.average_exposure_pct} {bt1.time_below_full_exposure_pct}
  {bt1.strategy_return_pct} {bt1.buyhold_return_pct}
  {bt1.strategy_max_drawdown_pct} {bt1.buyhold_max_drawdown_pct} (percent),
  {bt1.upside_drag_pp} {bt1.downside_cushion_pp} (percentage points),
  {bt1.strategy_sharpe} {bt1.buyhold_sharpe} {bt1.strategy_calmar}
  {bt1.buyhold_calmar} (ratios).
- To compare starting positions ("from 0%, 50%, and 100%"), call
  plan_position once per position. The app shows each call as a table row
  with its input position; explain the pattern instead of the rows' numbers.
  Do not use run_backtest's initial_position_pct for this: that is the
  position at the start of the whole backtest window.
- If the backtest shows Buy & Hold beating the strategy, say so.

When you have what you need, reply with the JSON object the response format
requires: summary (at most about 120 words), why (up to four sentences on
why the decision facts are what they are, each grounded in tool results),
historical_context (one short paragraph, or "" if not relevant), risks (a
list of risks, data limitations, or hindsight labels), and the citations you
used.
"""

FINAL_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "why": {"type": "array", "items": {"type": "string"}},
        "historical_context": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string"},
                    "published_at": {"type": "string"},
                },
                "required": ["doc_id", "published_at"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "why", "historical_context", "risks", "citations"],
    "additionalProperties": False,
}
ANSWER_FIELDS = ("summary", "why", "historical_context", "risks", "citations")


# DeepSeek has JSON mode but no schema enforcement, so the shape is spelled out.
JSON_ANSWER_INSTRUCTIONS = """
Reply to finish with only this JSON object and no other text:
{"summary": "<string>", "why": ["<string>"], "historical_context": "<string>", "risks": ["<string>"], "citations": [{"doc_id": "<string>", "published_at": "YYYY-MM-DD"}]}
"""


def _repair_message(violations):
    """Ask the model to rewrite a final answer that broke the contract."""
    listed = "\n".join(f"- {v}" for v in violations)
    return (
        "Your final answer breaks the answer contract:\n"
        f"{listed}\n"
        "Rewrite the final JSON. Leave position and action facts to the app (do not "
        "restate them), write what-if figures as {btN...} placeholders, keep other "
        "percentages out of clauses that mention exposure or position, and cite only "
        "doc_ids that search_fed_docs returned. Only fix these problems; keep the rest "
        "of the answer."
    )


def _schema_errors(data):
    """Type errors in the final JSON, reported as violations so a repair fires."""
    errors = []
    for name in ("summary", "historical_context"):
        if not isinstance(data.get(name, ""), str):
            errors.append(f"invalid structured output: {name} must be a string")
    for name in ("why", "risks"):
        value = data.get(name, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            errors.append(f"invalid structured output: {name} must be a list of strings")
    citations = data.get("citations", [])
    if not isinstance(citations, list) or not all(
            isinstance(c, dict) and isinstance(c.get("doc_id"), str) for c in citations):
        errors.append("invalid structured output: citations must be a list of "
                      "{doc_id, published_at} objects")
    return errors


API_ERRORS = (anthropic.AnthropicError, openai.OpenAIError)


class GuardrailViolation(RuntimeError):
    """The explanation still broke the answer contract after the repair turn."""

    def __init__(self, violations):
        super().__init__("; ".join(violations))
        self.violations = violations


@dataclass
class ToolCall:
    name: str
    arguments: dict
    result: Optional[dict]
    is_error: bool
    latency_ms: float
    as_of_clamped: bool = False
    # bt1, bt2, ... for run_backtest and pp1, pp2, ... for plan_position
    # results, so placeholders can name the call.
    call_id: Optional[str] = None


@dataclass
class AgentResult:
    question: str
    as_of: date
    # The model's explanation, rendered: answer is its summary, key_reasons
    # its "why" sentences.
    answer: str
    key_reasons: list = field(default_factory=list)
    historical_context: str = ""
    risks: list = field(default_factory=list)
    citations: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    steps: int = 0
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    # Model responses this answer used, and how many came from the cache.
    model_responses: int = 0
    cached_responses: int = 0
    fallback_reason: Optional[str] = None
    strategy: Optional[StrategyParams] = None
    # Code-owned decision facts; signal is decision.signal.
    decision: Optional[DecisionFacts] = None
    signal: Optional[StrategySignal] = None
    violations: list = field(default_factory=list)
    # Type errors in the final JSON, reported as violations.
    structured_errors: list = field(default_factory=list)
    # The model's text with placeholders, and the rendered answer fields
    # (summary, each why sentence, historical_context, each risk) with their
    # rendered spans.
    raw_answer: dict = field(default_factory=dict)
    rendered: list = field(default_factory=list)
    # Repair turns used, and the violations that triggered the first one.
    repairs: int = 0
    first_violations: list = field(default_factory=list)

    @property
    def used_fallback(self):
        return self.fallback_reason is not None

    @property
    def tool_names(self):
        return [call.name for call in self.tool_calls]


def _mcp_tool_to_anthropic(tool):
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.input_schema,
    }


def _tool_payload(result):
    """Structured content when present, otherwise the text blocks."""
    if result.structured_content is not None:
        return result.structured_content
    texts = [block.text for block in result.content if getattr(block, "type", "") == "text"]
    return {"text": "\n".join(texts)}


def _extract_json(text):
    """The first JSON object in ``text`` (tolerates code fences), or None."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


# Tool parameters filled from the dashboard's strategy settings when the
# model leaves them out; StrategyParams fields share the tool parameter names.
STRATEGY_ARGUMENTS = {
    "run_backtest": ("buy_threshold", "sell_threshold", "initial_position_pct", "add_pct",
                     "reduce_pct", "max_position_pct", "min_position_pct"),
    "get_regime": ("buy_threshold", "sell_threshold"),
    "plan_position": ("buy_threshold", "sell_threshold", "add_pct", "reduce_pct",
                      "max_position_pct", "min_position_pct"),
}

# Call-id prefix for tools whose results placeholders can name.
CALL_ID_PREFIXES = {"run_backtest": "bt", "plan_position": "pp"}


def _apply_strategy(name, arguments, strategy):
    """Default a tool call's strategy parameters to the session's settings.

    Values the model passes explicitly (e.g. for a what-if) are kept.
    """
    if strategy is None:
        return arguments
    settings = strategy.as_dict()
    filled = {field: settings[field] for field in STRATEGY_ARGUMENTS.get(name, ())}
    if name == "get_forward_returns":
        signal = arguments.get("signal", "panic")
        filled["threshold"] = strategy.buy_threshold if signal == "panic" else strategy.sell_threshold
    return {**filled, **arguments}


def _first_message(question, as_of, strategy, decision=None):
    content = f"as_of: {as_of.isoformat()}\n\n"
    if strategy is not None:
        content += f"Strategy settings: {strategy.describe()}.\n\n"
    if decision is not None:
        content += decision.prompt_block() + "\n\n"
    return {"role": "user", "content": content + f"Question: {question}"}


def _clamp_as_of(arguments, as_of):
    requested = arguments.get("as_of")
    try:
        requested_date = to_date(requested) if requested else None
    except ValueError:
        requested_date = None
    if requested_date is None or requested_date > as_of:
        return {**arguments, "as_of": as_of.isoformat()}, True
    return arguments, False


class ResearchAgent:
    """Claude (Anthropic Messages API) research agent."""

    provider = "anthropic"

    def __init__(self, server=None, client=None, model=DEFAULT_MODEL,
                 effort=DEFAULT_EFFORT, cache=None, ledger=None,
                 max_steps=8, max_tokens=16000, fallback_market="QQQ", max_repairs=1):
        if server is None:
            from mcp_server import server as default_server
            server = default_server
        self.server = server
        self._client = client
        self.model = model
        self.effort = effort
        self.cache = cache if cache is not None else ResponseCache()
        self.ledger = ledger if ledger is not None else UsageLedger()
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.fallback_market = fallback_market
        self.max_repairs = max_repairs

    @property
    def client(self):
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def run(self, question, as_of, market=None, strategy=None, current_position_pct=None):
        return asyncio.run(self.arun(question, as_of, market=market, strategy=strategy,
                                     current_position_pct=current_position_pct))

    async def arun(self, question, as_of, market=None, strategy=None,
                   current_position_pct=None):
        """Answer ``question`` as of ``as_of``.

        ``strategy`` (StrategyParams) carries the dashboard's threshold and
        position-sizing settings; tool calls that omit them use these values.
        ``current_position_pct`` is the user's own exposure from the page
        input; code turns it into the your-position decision, so the model
        never supplies it.
        """
        as_of = to_date(as_of)
        started = time.perf_counter()
        result = AgentResult(question=question, as_of=as_of, answer="", model=self.model,
                             strategy=strategy)
        try:
            result.decision = build_decision(market or self.fallback_market, as_of,
                                             strategy, current_position_pct)
            result.signal = result.decision.signal
        except ToolInputError:
            result.decision = result.signal = None
        try:
            await self._loop(question, as_of, result)
            self._validate(result)
        except (*API_ERRORS, BudgetExceeded, RuntimeError, ValueError) as exc:
            if isinstance(exc, GuardrailViolation):
                result.violations = exc.violations
            result.fallback_reason = f"{type(exc).__name__}: {exc}"
            thresholds = {} if strategy is None else {
                "buy_threshold": strategy.buy_threshold,
                "sell_threshold": strategy.sell_threshold,
            }
            result.answer = rule_based_summary(market or self.fallback_market, as_of,
                                               **thresholds)
            result.key_reasons = []
            result.historical_context = ""
            result.citations = []
            result.risks = [
                "LLM path unavailable; showing the deterministic rule-based "
                "summary for the selected market."
            ]
        result.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        result.cost_usd = round(result.cost_usd, 6)
        return result

    @staticmethod
    def _violations(result):
        """How the final answer breaks the answer contract."""
        retrieved = {}
        for call in result.tool_calls:
            if call.name == "search_fed_docs" and call.result and not call.is_error:
                for hit in call.result.get("results", []):
                    retrieved[hit["doc_id"]] = hit["published_at"]
        return result.structured_errors + validate_answer(
            result.rendered, citations=result.citations, retrieved=retrieved,
            as_of=result.as_of)

    def _needs_repair(self, result):
        """Whether to send the violations back for one more try."""
        violations = self._violations(result)
        if not violations or result.repairs >= self.max_repairs:
            return None
        if not result.first_violations:
            result.first_violations = violations
        result.repairs += 1
        return _repair_message(violations)

    def _validate(self, result):
        """Reject a final answer that still breaks the contract."""
        violations = self._violations(result)
        if violations:
            raise GuardrailViolation(violations)

    def _request_params(self, tools, messages):
        params = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": SYSTEM_PROMPT,
            "tools": tools,
            "messages": messages,
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": FINAL_ANSWER_SCHEMA},
            },
            # Caches the stable tools + system prefix (and history) across steps.
            "cache_control": {"type": "ephemeral"},
        }
        if self.model in FALLBACK_MODELS:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def _send(self, params):
        try:
            return self.client.beta.messages.create(**params).to_dict()
        except TypeError as exc:
            # The SDK raises TypeError when no API key / auth profile is set.
            if "authentication" not in str(exc):
                raise
            raise RuntimeError("no Anthropic credentials configured") from exc

    @staticmethod
    def _usage(response):
        """Token counts in the ledger's (Anthropic) field names."""
        return SimpleNamespace(**response.get("usage", {}))

    def _create(self, params, result):
        key = request_hash(params)
        cached = self.cache.get(key)
        started = time.perf_counter()
        if cached is None:
            self.ledger.check_budget()
            response = self._send(params)
            self.cache.put(key, response)
        else:
            response = cached
            result.cached_responses += 1
        result.model_responses += 1
        latency_ms = (time.perf_counter() - started) * 1000
        usage = self._usage(response)
        record = self.ledger.record(
            usage, response.get("model", self.model), latency_ms, key,
            cached_response=cached is not None,
        )
        result.model = response.get("model", self.model)
        result.input_tokens += record.input_tokens
        result.output_tokens += record.output_tokens
        result.cache_read_tokens += record.cache_read_tokens
        result.cost_usd += record.cost_usd
        return response

    async def _loop(self, question, as_of, result):
        # anyio wraps exceptions raised inside the client's task group in an
        # ExceptionGroup; capture them here so callers see the original error.
        error = None
        async with Client(self.server) as mcp:
            try:
                await self._steps(mcp, question, as_of, result)
            except Exception as exc:
                error = exc
        if error is not None:
            raise error

    async def _steps(self, mcp, question, as_of, result):
        listed = await mcp.list_tools()
        tools = sorted((_mcp_tool_to_anthropic(t) for t in listed.tools),
                       key=lambda t: t["name"])
        messages = [_first_message(question, as_of, result.strategy, result.decision)]
        for _ in range(self.max_steps):
            response = self._create(self._request_params(tools, messages), result)
            result.steps += 1
            stop_reason = response.get("stop_reason")
            content = response.get("content", [])

            if stop_reason == "refusal":
                raise RuntimeError("model declined the request")
            if stop_reason == "max_tokens":
                raise RuntimeError("response hit max_tokens before finishing")

            messages.append({"role": "assistant", "content": content})
            if stop_reason == "pause_turn":
                continue
            if stop_reason != "tool_use":
                self._parse_final(content, result)
                repair = self._needs_repair(result)
                if repair is None:
                    return
                messages.append({"role": "user", "content": repair})
                continue

            tool_results = []
            for block in content:
                if block.get("type") != "tool_use":
                    continue
                tool_results.append(
                    await self._call_tool(mcp, block, as_of, result)
                )
            messages.append({"role": "user", "content": tool_results})
        raise RuntimeError(f"no final answer after {self.max_steps} steps")

    async def _execute_tool(self, mcp, name, arguments, as_of, result):
        """Run one MCP tool call with the as-of clamp; returns (text, is_error)."""
        arguments = _apply_strategy(name, dict(arguments or {}), result.strategy)
        arguments, clamped = _clamp_as_of(arguments, as_of)
        started = time.perf_counter()
        tool_result = await mcp.call_tool(name, arguments)
        payload = _tool_payload(tool_result)
        call_id = None
        prefix = CALL_ID_PREFIXES.get(name)
        if prefix and not tool_result.is_error:
            count = sum(c.name == name and c.call_id is not None for c in result.tool_calls)
            call_id = f"{prefix}{count + 1}"
        result.tool_calls.append(ToolCall(
            name=name,
            arguments=arguments,
            result=payload,
            is_error=tool_result.is_error,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            as_of_clamped=clamped,
            call_id=call_id,
        ))
        text = json.dumps(payload, default=str)
        if call_id:
            text = f"[call_id: {call_id}]\n{text}"
        if clamped:
            text = f"[as_of set to session cutoff {as_of.isoformat()}]\n{text}"
        return text, tool_result.is_error

    async def _call_tool(self, mcp, block, as_of, result):
        text, is_error = await self._execute_tool(
            mcp, block["name"], block.get("input"), as_of, result
        )
        return {
            "type": "tool_result",
            "tool_use_id": block["id"],
            "content": text,
            "is_error": is_error,
        }

    @staticmethod
    def _apply_final(data, result):
        if not isinstance(data.get("summary"), str):
            raise RuntimeError("final answer is missing the 'summary' string")
        result.structured_errors = _schema_errors(data)
        citations = data.get("citations")
        result.citations = [c for c in citations if isinstance(c, dict) and c.get("doc_id")] \
            if isinstance(citations, list) else []

        def strings(value):
            return [str(v) for v in value if v] if isinstance(value, list) else []

        raw = {
            "summary": data["summary"],
            "why": strings(data.get("why")),
            "historical_context": str(data.get("historical_context") or ""),
            "risks": strings(data.get("risks")),
        }
        result.raw_answer = raw
        # What-if figures are placeholders; code fills in the values.
        calls = {c.call_id: c.result for c in result.tool_calls if c.call_id}
        texts = [raw["summary"], *raw["why"], raw["historical_context"], *raw["risks"]]
        result.rendered = [render(text, result.signal, calls) for text in texts]
        n_why = len(raw["why"])
        result.answer = result.rendered[0].text
        result.key_reasons = [r.text for r in result.rendered[1:1 + n_why]]
        result.historical_context = result.rendered[1 + n_why].text
        result.risks = [r.text for r in result.rendered[2 + n_why:]]

    @classmethod
    def _parse_final(cls, content, result):
        text = next((b["text"] for b in content if b.get("type") == "text"), None)
        if text is None:
            raise RuntimeError("final response had no text block")
        cls._apply_final(json.loads(text), result)


class DeepSeekAgent(ResearchAgent):
    """DeepSeek (OpenAI-compatible Chat Completions) research agent.

    Shares the MCP tools, as-of clamp, response cache, usage ledger, and
    fallback with the Claude agent. Thinking mode is disabled: DeepSeek's API
    reference does not say how ``reasoning_content`` must be replayed across
    tool calls. The final answer uses JSON mode, which (unlike Claude's
    structured outputs) does not enforce a schema, so it is validated here and
    re-requested once if it does not parse.
    """

    provider = "deepseek"

    def __init__(self, api_key=None, model=DEEPSEEK_DEFAULT_MODEL, max_tokens=8000, **kwargs):
        super().__init__(model=model, max_tokens=max_tokens, **kwargs)
        self._api_key = api_key

    @property
    def client(self):
        if self._client is None:
            api_key = self._api_key or os.getenv("DEEPSEEK_API_KEY")
            if not api_key:
                raise RuntimeError("no DeepSeek API key configured")
            self._client = openai.OpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)
        return self._client

    def _send(self, params):
        return self.client.chat.completions.create(**params).model_dump()

    @staticmethod
    def _usage(response):
        usage = response.get("usage") or {}
        hits = usage.get("prompt_cache_hit_tokens") or 0
        misses = usage.get("prompt_cache_miss_tokens")
        if misses is None:
            misses = (usage.get("prompt_tokens") or 0) - hits
        return SimpleNamespace(
            input_tokens=misses,
            output_tokens=usage.get("completion_tokens") or 0,
            cache_read_input_tokens=hits,
            cache_creation_input_tokens=0,
        )

    def _request_params(self, tools, messages, final_json=False):
        params = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT + JSON_ANSWER_INSTRUCTIONS}]
                        + messages,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        if final_json:
            params["response_format"] = {"type": "json_object"}
        else:
            params["tools"] = tools
        return params

    async def _steps(self, mcp, question, as_of, result):
        listed = await mcp.list_tools()
        tools = sorted(
            ({"type": "function",
              "function": {"name": t.name, "description": t.description or "",
                           "parameters": t.input_schema}}
             for t in listed.tools),
            key=lambda t: t["function"]["name"],
        )
        messages = [_first_message(question, as_of, result.strategy, result.decision)]
        for _ in range(self.max_steps):
            message = self._next_message(self._request_params(tools, messages), result)
            calls = message.get("tool_calls") or []
            if not calls:
                final_text = message.get("content") or ""
                data = _extract_json(final_text)
                if data is None:
                    # One retry in JSON mode, without tools, to get the final object.
                    messages.append({"role": "assistant", "content": final_text})
                    messages.append({"role": "user", "content": "Reply with only the final JSON object."})
                    retry = self._next_message(
                        self._request_params(tools, messages, final_json=True), result
                    )
                    final_text = retry.get("content") or ""
                    data = _extract_json(final_text)
                    if data is None:
                        raise RuntimeError("model did not return the final JSON answer")
                self._apply_final(data, result)
                repair = self._needs_repair(result)
                if repair is None:
                    return
                messages.append({"role": "assistant", "content": final_text})
                messages.append({"role": "user", "content": repair})
                continue

            messages.append({
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["function"]["name"],
                                  "arguments": c["function"]["arguments"]}}
                    for c in calls
                ],
            })
            for call in calls:
                try:
                    arguments = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    text = json.dumps({"error": "tool arguments were not valid JSON"})
                else:
                    text, _ = await self._execute_tool(
                        mcp, call["function"]["name"], arguments, as_of, result
                    )
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": text})
        raise RuntimeError(f"no final answer after {self.max_steps} steps")

    def _next_message(self, params, result):
        response = self._create(params, result)
        result.steps += 1
        choice = (response.get("choices") or [{}])[0]
        finish = choice.get("finish_reason")
        if finish == "content_filter":
            raise RuntimeError("model declined the request")
        if finish == "length":
            raise RuntimeError("response hit max_tokens before finishing")
        if finish in ("insufficient_system_resource", "aborted"):
            raise RuntimeError(f"DeepSeek stopped the response ({finish})")
        return choice.get("message") or {}
