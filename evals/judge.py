"""LLM-judge groundedness: is each statement supported by this turn's evidence?

Position figures are guaranteed by code (placeholders), but the prose around
them (reasons, Fed context, caveats) can still drift from the tool results.
This judge splits an answer into statements and asks a model to label each
one against the evidence the assistant had: the strategy signal and every
tool result of the turn.

It is a measurement, not a gate: the score is reported by the live evals and
never blocks an answer. Labels:
    supported     - the evidence states or directly implies it
    unsupported   - the evidence does not cover it (may still be true)
    contradicted  - the evidence says otherwise
"""
import json
import os
import re

from assistant.agent import DEEPSEEK_BASE_URL, _extract_json
from assistant.ledger import cost_usd

JUDGE_PROMPT = """\
You are checking a research assistant's answer against the evidence it had.
Label each numbered statement with exactly one verdict:
- supported: the evidence states it or it follows directly from the evidence
- unsupported: the evidence does not cover it
- contradicted: the evidence says otherwise (wrong number, date, direction, or source)
Judge only against the evidence below, not your own knowledge. Generic
disclaimers ("this is not investment advice") are supported.

Evidence:
{evidence}

Statements:
{statements}

Reply with only this JSON object:
{{"verdicts": [{{"id": <statement number>, "verdict": "supported|unsupported|contradicted", "reason": "<short>"}}]}}
"""
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
VERDICTS = ("supported", "unsupported", "contradicted")
EVIDENCE_LIMIT = 6000  # characters per tool result


def statements(result):
    """The answer's sentences, key reasons, and caveats, one statement each."""
    items = [s.strip() for s in SENTENCE_RE.split(result.answer) if s.strip()]
    items += [r for r in result.key_reasons if r.strip()]
    if result.caveats and result.caveats.strip().lower() != "none":
        items += [s.strip() for s in SENTENCE_RE.split(result.caveats) if s.strip()]
    return items


def evidence(result):
    parts = []
    if result.signal is not None:
        parts.append(result.signal.prompt_block())
    for call in result.tool_calls:
        payload = json.dumps(call.result, default=str)[:EVIDENCE_LIMIT]
        label = f"{call.name}({json.dumps(call.arguments, default=str)})"
        parts.append(f"[{call.call_id or 'tool'}] {label}:\n{payload}")
    return "\n\n".join(parts) or "(no evidence)"


class DeepSeekJudge:
    """Judge model behind the OpenAI-compatible DeepSeek API."""

    def __init__(self, model="deepseek-flash", api_key=None):
        import openai

        self.model = model
        self.client = openai.OpenAI(api_key=api_key or os.environ["DEEPSEEK_API_KEY"],
                                    base_url=DEEPSEEK_BASE_URL)

    def complete(self, prompt):
        response = self.client.chat.completions.create(
            model=self.model, max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
        )
        usage = response.usage
        cost = cost_usd(self.model, usage.prompt_tokens, usage.completion_tokens)
        return response.choices[0].message.content, cost


def judge_result(result, judge):
    """Verdict counts for one AgentResult; None when there is nothing to judge."""
    items = statements(result)
    if result.used_fallback or not items:
        return None
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(items, start=1))
    text, cost = judge.complete(JUDGE_PROMPT.format(evidence=evidence(result),
                                                    statements=numbered))
    data = _extract_json(text) or {}
    labels = {v.get("id"): v for v in data.get("verdicts", []) if isinstance(v, dict)}
    rows = []
    for i, statement in enumerate(items, start=1):
        verdict = labels.get(i, {}).get("verdict")
        rows.append({"statement": statement,
                     "verdict": verdict if verdict in VERDICTS else "unjudged",
                     "reason": labels.get(i, {}).get("reason", "")})
    counts = {v: sum(r["verdict"] == v for r in rows) for v in (*VERDICTS, "unjudged")}
    judged = len(rows) - counts["unjudged"]
    return {
        "groundedness": round(counts["supported"] / judged, 3) if judged else None,
        **counts,
        "cost_usd": round(cost, 6),
        "statements": rows,
    }


def summarize_judgements(judgements):
    """Statement-weighted groundedness across answers."""
    judged = [j for j in judgements if j]
    totals = {v: sum(j[v] for j in judged) for v in (*VERDICTS, "unjudged")}
    labelled = sum(totals[v] for v in VERDICTS)
    return {
        "judge_answers": len(judged),
        "judge_groundedness": round(totals["supported"] / labelled, 3) if labelled else None,
        "judge_statements": labelled,
        "judge_unsupported": totals["unsupported"],
        "judge_contradicted": totals["contradicted"],
        "judge_unjudged": totals["unjudged"],
        "judge_cost_usd": round(sum(j["cost_usd"] for j in judged), 4),
    }
