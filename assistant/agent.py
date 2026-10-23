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
import openai
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


# DeepSeek has JSON mode but no schema enforcement, so the shape is spelled out.
JSON_ANSWER_INSTRUCTIONS = """
Reply to finish with only this JSON object and no other text:
{"answer": "<string>", "citations": [{"doc_id": "<string>", "published_at": "YYYY-MM-DD"}], "caveats": "<string>"}
"""

API_ERRORS = (anthropic.AnthropicError, openai.OpenAIError)


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
        except (*API_ERRORS, BudgetExceeded, RuntimeError, ValueError) as exc:
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

    async def _execute_tool(self, mcp, name, arguments, as_of, result):
        """Run one MCP tool call with the as-of clamp; returns (text, is_error)."""
        arguments, clamped = _clamp_as_of(dict(arguments or {}), as_of)
        started = time.perf_counter()
        tool_result = await mcp.call_tool(name, arguments)
        payload = _tool_payload(tool_result)
        result.tool_calls.append(ToolCall(
            name=name,
            arguments=arguments,
            result=payload,
            is_error=tool_result.is_error,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            as_of_clamped=clamped,
        ))
        text = json.dumps(payload, default=str)
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
        if not isinstance(data.get("answer"), str):
            raise RuntimeError("final answer is missing the 'answer' string")
        citations = data.get("citations") or []
        result.answer = data["answer"]
        result.citations = [c for c in citations if isinstance(c, dict) and c.get("doc_id")]
        result.caveats = str(data.get("caveats") or "")

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
        messages = [{
            "role": "user",
            "content": f"as_of: {as_of.isoformat()}\n\nQuestion: {question}",
        }]
        for _ in range(self.max_steps):
            message = self._next_message(self._request_params(tools, messages), result)
            calls = message.get("tool_calls") or []
            if not calls:
                data = _extract_json(message.get("content"))
                if data is None:
                    # One retry in JSON mode, without tools, to get the final object.
                    messages.append({"role": "assistant", "content": message.get("content") or ""})
                    messages.append({"role": "user", "content": "Reply with only the final JSON object."})
                    retry = self._next_message(
                        self._request_params(tools, messages, final_json=True), result
                    )
                    data = _extract_json(retry.get("content"))
                    if data is None:
                        raise RuntimeError("model did not return the final JSON answer")
                self._apply_final(data, result)
                return

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
