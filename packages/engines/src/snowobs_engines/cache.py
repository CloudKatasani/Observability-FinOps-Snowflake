"""Result cache keyed on the compiled SQL, dataset version, and RLS context.

Two users with different row-level security must never share a cache entry —
the key includes the RLS context precisely so that cannot happen (§22.3).
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass, replace

from snowobs_engines.base import QueryResult


def cache_key(*, sql_fingerprint: str, dataset_version: str, rls_context: str = "") -> str:
    digest = hashlib.sha256(
        f"{sql_fingerprint}|{dataset_version}|{rls_context}".encode()
    ).hexdigest()
    return digest[:32]


@dataclass
class _Entry:
    result: QueryResult
    expires_at: float


class ResultCache:
    """Bounded in-process LRU cache with a TTL.

    Deliberately simple: the Redis-backed shared cache is a drop-in behind the
    same interface, and correctness here is about the key, not the store.
    """

    def __init__(self, max_entries: int = 512, ttl_seconds: float = 300.0) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> QueryResult | None:
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        if entry.expires_at < time.monotonic():
            del self._entries[key]
            self.misses += 1
            return None
        self._entries.move_to_end(key)
        self.hits += 1
        return replace(entry.result, cache_hit=True)

    def put(self, key: str, result: QueryResult) -> None:
        self._entries[key] = _Entry(result=result, expires_at=time.monotonic() + self.ttl_seconds)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def invalidate(self, key: str) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        """Drop everything — called when a new upload changes the dataset version."""
        self._entries.clear()

    @property
    def size(self) -> int:
        return len(self._entries)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0
