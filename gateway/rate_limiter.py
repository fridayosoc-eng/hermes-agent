"""Token-bucket rate limiter for per-user request throttling.

Prevents a single user from flooding the bot and consuming all
concurrent agent slots, denying service to others.
"""

import time
import threading
from typing import Dict


class RateLimiter:
    """Thread-safe token bucket rate limiter.

    Each unique key (e.g. chat_id) gets its own bucket. Tokens refill
    over time at a steady rate up to a maximum capacity.
    """

    def __init__(self, max_tokens: int = 10, refill_rate: float = 1.0):
        """
        Args:
            max_tokens: Maximum tokens per bucket (burst capacity).
            refill_rate: Tokens refilled per second.
        """
        self._max = max_tokens
        self._rate = refill_rate
        self._buckets: Dict[str, list] = {}  # key → [tokens, last_refill_time]
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """Check if a request is allowed for the given key.

        Returns True and consumes one token if allowed, False if rate-limited.
        """
        with self._lock:
            now = time.monotonic()
            if key not in self._buckets:
                self._buckets[key] = [self._max - 1.0, now]
                return True

            tokens, last = self._buckets[key]
            # Refill tokens based on elapsed time
            elapsed = now - last
            tokens = min(self._max, tokens + elapsed * self._rate)

            if tokens >= 1.0:
                self._buckets[key] = [tokens - 1.0, now]
                return True

            self._buckets[key] = [tokens, now]
            return False

    def reset(self, key: str) -> None:
        """Remove rate limit state for a key."""
        with self._lock:
            self._buckets.pop(key, None)

    def prune_stale(self, max_age: float = 3600.0) -> int:
        """Remove buckets that haven't been used in max_age seconds.

        Returns the number of pruned entries.
        """
        cutoff = time.monotonic() - max_age
        pruned = 0
        with self._lock:
            stale = [k for k, (_, last) in self._buckets.items() if last < cutoff]
            for k in stale:
                del self._buckets[k]
                pruned += 1
        return pruned
