"""prompt_cache.py — Content-hash based AI response cache.

Owner: ai segment.

Problem:
    Several agents (IntelligenceVerdictAgent, WatchdogAgent,
    SignalCredibilityAgent) are called on every engine cycle or every
    scheduler tick. If the input prompt hasn't changed materially,
    calling the AI again wastes tokens and adds latency.

Solution:
    Hash the (system_prompt + user_prompt) before every AI call.
    If the same hash was seen within TTL seconds → return cached result.
    Otherwise call AI, store result, update cache.

Usage (in agent run() method)::

    from src.ai.prompt_cache import PromptCache

    _cache = PromptCache[VerdictOutput](ttl_seconds=300)

    async def run(self, ...) -> VerdictOutput:
        hit = _cache.get(system_prompt, user_prompt, VerdictOutput)
        if hit is not None:
            return hit
        result = await self._client.chat(...)
        _cache.set(system_prompt, user_prompt, result)
        return result

Design:
    - In-process only (no Redis / no DB). Survives for lifetime of process.
    - TTL is per-entry: each entry has its own expiry timestamp.
    - Max size: 64 entries per cache instance (LRU eviction).
    - Thread-safe via simple dict operations (GIL is sufficient for asyncio).
    - Generic[T] so type checkers stay happy.
    - Zero dependencies beyond stdlib.

Boundary:
    - MUST NOT be used for user-triggered on-demand agents (pretrade, replay,
      why, thesis_debate) — those require fresh analysis every time.
    - ONLY for high-frequency scheduled/engine-loop agents where input
      stability within a short window is expected.
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from typing import Generic, TypeVar

from pydantic import BaseModel

from src.platform.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

_DEFAULT_TTL = 300       # 5 minutes
_DEFAULT_MAX_SIZE = 64   # LRU eviction after this many entries


class PromptCache(Generic[T]):
    """LRU in-process cache keyed by prompt content hash.

    Generic over Pydantic output type T.
    TTL and max_size are configurable per instance.
    """

    def __init__(
        self,
        ttl_seconds: int = _DEFAULT_TTL,
        max_size: int = _DEFAULT_MAX_SIZE,
        agent_name: str = "unknown",
    ) -> None:
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._agent_name = agent_name
        # OrderedDict for LRU: most recent at end
        self._store: OrderedDict[str, tuple[T, float]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, system_prompt: str, user_prompt: str, schema: type[T]) -> T | None:
        """Return cached result if hash matches and TTL not expired.

        Returns None on cache miss or expired entry.
        """
        key = _hash_prompts(system_prompt, user_prompt)
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None

        result, expires_at = entry
        if time.monotonic() > expires_at:
            # Expired — evict and treat as miss
            del self._store[key]
            self._misses += 1
            logger.debug(
                "prompt_cache.expired",
                agent=self._agent_name,
                key_prefix=key[:8],
            )
            return None

        # Cache hit — move to end (LRU)
        self._store.move_to_end(key)
        self._hits += 1
        logger.info(
            "prompt_cache.hit",
            agent=self._agent_name,
            key_prefix=key[:8],
            ttl_remaining=round(expires_at - time.monotonic()),
            total_hits=self._hits,
        )
        return result  # type: ignore[return-value]

    def set(self, system_prompt: str, user_prompt: str, result: T) -> None:
        """Store result with TTL. Evicts LRU entry if at capacity."""
        key = _hash_prompts(system_prompt, user_prompt)

        # LRU eviction
        if len(self._store) >= self._max_size and key not in self._store:
            evicted_key, _ = self._store.popitem(last=False)
            logger.debug(
                "prompt_cache.evict_lru",
                agent=self._agent_name,
                evicted_key_prefix=evicted_key[:8],
            )

        expires_at = time.monotonic() + self._ttl
        self._store[key] = (result, expires_at)
        self._store.move_to_end(key)

    def invalidate(self) -> int:
        """Clear all entries. Returns number evicted."""
        n = len(self._store)
        self._store.clear()
        return n

    @property
    def stats(self) -> dict[str, int]:
        """Cache hit/miss stats for monitoring."""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate_pct": round(self._hits / total * 100) if total else 0,
            "size": len(self._store),
        }


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _hash_prompts(system_prompt: str, user_prompt: str) -> str:
    """SHA-256 of concatenated prompts, hex-encoded (64 chars)."""
    combined = f"{system_prompt}\n\n---USER---\n\n{user_prompt}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
