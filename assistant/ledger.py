"""Token, latency, and cost accounting with a daily spend cap."""
import json
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parents[1] / "logs" / "llm_usage.jsonl"

# USD per million tokens (Claude API first-party rates, 2026-10).
# cache_write is the 5-minute TTL rate (1.25x input). Older models are listed
# because server-side refusal fallbacks can answer from them.
PRICING = {
    "claude-fable-5-1": {"input": 10.00, "output": 50.00, "cache_read": 0.25, "cache_write": 12.50},
    "claude-fable-5": {"input": 10.00, "output": 50.00, "cache_read": 1.00, "cache_write": 12.50},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00},
    "claude-opus-5": {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
    "claude-opus-4-8": {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
    "claude-opus-4-7": {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50},
    "claude-sonnet-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_read": 0.10, "cache_write": 1.25},
}
# Unknown models are billed at the highest listed rates so the budget never
# under-counts spend.
FALLBACK_RATES = {key: max(rates[key] for rates in PRICING.values())
                  for key in ("input", "output", "cache_read", "cache_write")}


class BudgetExceeded(RuntimeError):
    pass


def rates_for(model):
    """(rates, priced_as) for a model; unknown models use FALLBACK_RATES."""
    if model in PRICING:
        return PRICING[model], model
    return FALLBACK_RATES, "unlisted-model-max-rate"


def cost_usd(model, input_tokens=0, output_tokens=0,
             cache_read_tokens=0, cache_write_tokens=0):
    rates, _ = rates_for(model)
    return (
        input_tokens * rates["input"]
        + output_tokens * rates["output"]
        + cache_read_tokens * rates["cache_read"]
        + cache_write_tokens * rates["cache_write"]
    ) / 1_000_000


@dataclass
class UsageRecord:
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    latency_ms: float
    cost_usd: float
    cached_response: bool
    request_hash: str
    priced_as: str = ""
    session_id: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class UsageLedger:
    """Append-only JSONL ledger. Cached responses are logged at $0 cost.

    Daily totals are kept in memory and the file is read incrementally from
    the last offset seen, so budget checks stay cheap as the log grows while
    still counting records written by other processes.
    """

    def __init__(self, path=LOG_PATH, daily_budget_usd=1.00):
        self.path = Path(path)
        self.daily_budget_usd = daily_budget_usd
        self._lock = threading.Lock()
        self._offset = 0
        self._daily_totals = defaultdict(float)

    def record(self, usage, model, latency_ms, request_hash,
               cached_response=False, session_id=""):
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        _, priced_as = rates_for(model)
        record = UsageRecord(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            latency_ms=round(latency_ms, 1),
            cost_usd=0.0 if cached_response else round(
                cost_usd(model, input_tokens, output_tokens, cache_read, cache_write), 6
            ),
            cached_response=cached_response,
            request_hash=request_hash,
            priced_as=priced_as,
            session_id=session_id,
        )
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(record)) + "\n")
        return record

    def records(self):
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def _scan_new_records(self):
        if not self.path.exists():
            return
        with self.path.open("rb") as fh:
            if self.path.stat().st_size < self._offset:  # file was truncated or replaced
                self._offset = 0
                self._daily_totals.clear()
            fh.seek(self._offset)
            for raw in fh:
                if not raw.endswith(b"\n"):
                    break  # partially written line; read it next time
                self._offset += len(raw)
                if raw.strip():
                    entry = json.loads(raw)
                    self._daily_totals[entry["timestamp"][:10]] += entry["cost_usd"]

    def spent_today(self):
        today = datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            self._scan_new_records()
            return self._daily_totals.get(today, 0.0)

    def check_budget(self):
        spent = self.spent_today()
        if spent >= self.daily_budget_usd:
            raise BudgetExceeded(
                f"Daily LLM budget of ${self.daily_budget_usd:.2f} reached "
                f"(spent ${spent:.4f} today, UTC)."
            )
        return self.daily_budget_usd - spent
