"""Research agent: Claude tool use over the Panic Index MCP server.

The agent connects to the MCP server in-process with ``mcp.Client``, so it
sees exactly the tool names, descriptions, and JSON schemas any other MCP
client sees. Three guardrails sit around the model:

* as-of enforcement: every tool call's ``as_of`` is clamped to the session
  cutoff, so the model cannot read later data even if it asks for it;
* cost control: identical requests are served from a response cache, every
  call is logged to the usage ledger, and a daily budget stops new calls;
* graceful degradation: on API errors, refusals, or an exhausted budget the
  agent returns the deterministic rule-based summary instead.
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
from mcp import Client

from tools import rule_based_summary
from tools.market import to_date

from .cache import ResponseCache, request_hash
from .ledger import BudgetExceeded, UsageLedger

DEFAULT_MODEL = os.getenv("PANIC_LLM_MODEL", "claude-opus-5-5")
DEFAULT_EFFORT = os.getenv("PANIC_LLM_EFFORT", "medium")
# Models that accept server-side refusal fallbacks with the "default" routing.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """\
You are a market-regime research assistant for the Composite Panic Index
dashboard (VIX 50% + inverted CNN Fear & Greed 50%) covering QQQ, SMH, SPX,
and DJI, with a searchable library of FOMC statements and minutes.

Each question comes with an as_of date. Answer as an analyst standing on that
date:
- Get every fact from the tools. Do not use your own knowledge of markets or
  Federal Reserve decisions, because it may include events after as_of.
- Pass the session's as_of to every tool call (later dates are clamped).
- Copy numbers from tool results; round only to the precision shown.
- For any claim about Federal Reserve policy, call search_fed_docs and cite
  the doc_id of each passage you rely on. Cite only doc_ids that a tool
  returned in this conversation.
- If the tools cannot answer the question, say so instead of guessing.
- This is historical research, not investment advice; say so briefly when
  the user asks what to buy or sell.

When you have what you need, reply with the JSON object the response format
requires: a concise answer (at most about 200 words), the citations you used,
and caveats (data limitations, hindsight labels, or "none").
"""

FINAL_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
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
        "caveats": {"type": "string"},
    },
    "required": ["answer", "citations", "caveats"],
    "additionalProperties": False,
}


@dataclass
class ToolCall:
    name: str
    arguments: dict
    result: Optional[dict]
    is_error: bool
    latency_ms: float
    as_of_clamped: bool = False


@dataclass
class AgentResult:
    question: str
    as_of: date
    answer: str
    citations: list = field(default_factory=list)
    caveats: str = ""
    tool_calls: list = field(default_factory=list)
    steps: int = 0
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    cached_responses: int = 0
    fallback_reason: Optional[str] = None

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
    def __init__(self, server=None, client=None, model=DEFAULT_MODEL,
                 effort=DEFAULT_EFFORT, cache=None, ledger=None,
                 max_steps=8, max_tokens=16000, fallback_market="QQQ"):
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

    @property
    def client(self):
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def run(self, question, as_of, market=None):
        return asyncio.run(self.arun(question, as_of, market=market))

    async def arun(self, question, as_of, market=None):
        as_of = to_date(as_of)
        started = time.perf_counter()
        result = AgentResult(question=question, as_of=as_of, answer="", model=self.model)
        try:
            await self._loop(question, as_of, result)
        except (anthropic.AnthropicError, BudgetExceeded, RuntimeError, ValueError) as exc:
            result.fallback_reason = f"{type(exc).__name__}: {exc}"
            result.answer = rule_based_summary(market or self.fallback_market, as_of)
            result.citations = []
            result.caveats = (
                "LLM path unavailable; showing the deterministic rule-based "
                "summary for the selected market."
            )
        result.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        result.cost_usd = round(result.cost_usd, 6)
        return result

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

    def _create(self, params, result):
        key = request_hash(params)
        cached = self.cache.get(key)
        started = time.perf_counter()
        if cached is None:
            self.ledger.check_budget()
            try:
                response = self.client.beta.messages.create(**params).to_dict()
            except TypeError as exc:
                # The SDK raises TypeError when no API key / auth profile is set.
                if "authentication" not in str(exc):
                    raise
                raise RuntimeError("no Anthropic credentials configured") from exc
            self.cache.put(key, response)
        else:
            response = cached
            result.cached_responses += 1
        latency_ms = (time.perf_counter() - started) * 1000
        usage = SimpleNamespace(**response.get("usage", {}))
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
        messages = [{
            "role": "user",
            "content": f"as_of: {as_of.isoformat()}\n\nQuestion: {question}",
        }]
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
                return

            tool_results = []
            for block in content:
                if block.get("type") != "tool_use":
                    continue
                tool_results.append(
                    await self._call_tool(mcp, block, as_of, result)
                )
            messages.append({"role": "user", "content": tool_results})
        raise RuntimeError(f"no final answer after {self.max_steps} steps")

    async def _call_tool(self, mcp, block, as_of, result):
        arguments, clamped = _clamp_as_of(dict(block.get("input") or {}), as_of)
        started = time.perf_counter()
        tool_result = await mcp.call_tool(block["name"], arguments)
        payload = _tool_payload(tool_result)
        result.tool_calls.append(ToolCall(
            name=block["name"],
            arguments=arguments,
            result=payload,
            is_error=tool_result.is_error,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            as_of_clamped=clamped,
        ))
        text = json.dumps(payload, default=str)
        if clamped:
            text = f"[as_of set to session cutoff {as_of.isoformat()}]\n{text}"
        return {
            "type": "tool_result",
            "tool_use_id": block["id"],
            "content": text,
            "is_error": tool_result.is_error,
        }

    @staticmethod
    def _parse_final(content, result):
        text = next((b["text"] for b in content if b.get("type") == "text"), None)
        if text is None:
            raise RuntimeError("final response had no text block")
        data = json.loads(text)
        result.answer = data["answer"]
        result.citations = data.get("citations", [])
        result.caveats = data.get("caveats", "")
