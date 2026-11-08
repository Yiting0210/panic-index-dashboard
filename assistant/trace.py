"""Per-call traces of what the agent sent to the model, and where the context went.

Each model call in a run gets one ``ModelCallTrace``: the provider's real token
counts (input, output, cache read), whether the response came from the cache,
latency, and the size of each part of the request (``context_sections``).

Section sizes are characters, converted to an estimated token count with
``CHARS_PER_TOKEN``. They show where the context goes (tool schemas vs. tool
results vs. the decision facts); the provider's token counts stay the
authoritative totals. Estimating locally avoids a token-counting API call per
request and works the same for every provider.
"""
import json
from dataclasses import asdict, dataclass, field

CHARS_PER_TOKEN = 4

# Report order; every request is split into exactly these parts.
SECTIONS = (
    "system",          # system prompt (DeepSeek: plus its JSON answer instructions)
    "tool_schemas",    # tool names, descriptions, and input schemas
    "output_schema",   # Claude's structured-output JSON schema
    "decision_facts",  # the code-owned DecisionFacts block in the first message
    "question",        # the rest of the first message: as_of, strategy settings, question
    "tool_results",    # tool results returned to the model
    "assistant_turns", # the model's earlier turns: text, tool calls, thinking blocks
    "follow_ups",      # later user messages: repair turns, DeepSeek's JSON retry
)


def _size(value):
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))


def _is_tool_result_message(message):
    content = message.get("content")
    return (message.get("role") == "tool"
            or (isinstance(content, list) and content
                and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)))


def context_sections(params, decision_block=""):
    """Character size of each part of one Messages / Chat Completions request."""
    sizes = dict.fromkeys(SECTIONS, 0)
    messages = list(params.get("messages", []))
    if "system" in params:  # Anthropic
        sizes["system"] += _size(params["system"])
    while messages and messages[0].get("role") == "system":  # DeepSeek / OpenAI format
        sizes["system"] += _size(messages.pop(0).get("content"))
    sizes["tool_schemas"] = _size(params.get("tools"))
    output_format = (params.get("output_config") or {}).get("format")
    sizes["output_schema"] = _size(output_format)

    first_user = True
    for message in messages:
        role, content = message.get("role"), message.get("content")
        if role == "assistant":
            sizes["assistant_turns"] += _size(content) + _size(message.get("tool_calls"))
        elif _is_tool_result_message(message):
            sizes["tool_results"] += _size(content)
        elif role == "user" and first_user:
            first_user = False
            text = content if isinstance(content, str) else json.dumps(content, default=str)
            facts = len(decision_block) if decision_block and decision_block in text else 0
            sizes["decision_facts"] += facts
            sizes["question"] += len(text) - facts
        else:
            sizes["follow_ups"] += _size(content)
    return sizes


@dataclass
class ModelCallTrace:
    step: int
    request_hash: str
    cached: bool
    latency_ms: float
    # Provider-reported tokens for this call. A cached response keeps the
    # counts it was recorded with, so replayed runs still show context size.
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    # Characters per context section (see SECTIONS).
    sections: dict = field(default_factory=dict)

    @property
    def estimated_tokens(self):
        return {name: round(chars / CHARS_PER_TOKEN) for name, chars in self.sections.items()}

    def as_dict(self):
        return {**asdict(self), "estimated_tokens": self.estimated_tokens}


def context_budget(traces):
    """Context budget over model calls: mean estimated tokens and share per section.

    ``traces`` is any iterable of ``ModelCallTrace`` or their ``as_dict()``
    output, e.g. all calls of an eval run. Shares are of the estimated request
    size; ``mean_input_tokens`` is the provider's average uncached plus
    cache-read input tokens per call, for comparison with the estimate.
    """
    calls = [t.as_dict() if isinstance(t, ModelCallTrace) else t for t in traces]
    if not calls:
        return {"model_calls": 0, "sections": {}}
    totals = {name: sum(c["sections"].get(name, 0) for c in calls) for name in SECTIONS}
    all_chars = sum(totals.values()) or 1
    return {
        "model_calls": len(calls),
        "mean_input_tokens": round(sum(c["input_tokens"] + c["cache_read_tokens"]
                                       for c in calls) / len(calls)),
        "mean_estimated_request_tokens": round(all_chars / CHARS_PER_TOKEN / len(calls)),
        "sections": {
            name: {"mean_estimated_tokens": round(totals[name] / CHARS_PER_TOKEN / len(calls)),
                   "share": round(totals[name] / all_chars, 3)}
            for name in SECTIONS
        },
    }
