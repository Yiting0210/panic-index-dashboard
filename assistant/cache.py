"""Content-addressed cache of Messages API responses.

Identical requests (same model, system prompt, tools, and message history)
return the stored response instead of calling the API. In ``replay`` mode a
cache miss raises, which lets CI run the agent eval from committed
recordings without an API key.
"""
import hashlib
import json
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parents[1] / ".cache" / "llm"
MODES = ("readwrite", "replay", "record", "off")


class CacheMiss(LookupError):
    pass


def request_hash(params):
    canonical = json.dumps(params, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ResponseCache:
    """mode: readwrite (default), replay (read only, miss raises),
    record (always call and overwrite), off (never read or write)."""

    def __init__(self, directory=CACHE_DIR, mode="readwrite"):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.directory = Path(directory)
        self.mode = mode

    def _path(self, key):
        return self.directory / f"{key}.json"

    def get(self, key):
        if self.mode in ("off", "record"):
            return None
        path = self._path(key)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        if self.mode == "replay":
            raise CacheMiss(f"No recorded response for request {key[:12]}")
        return None

    def put(self, key, response_dict):
        if self.mode in ("off", "replay"):
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(key).write_text(
            json.dumps(response_dict, indent=1, ensure_ascii=False), encoding="utf-8"
        )
