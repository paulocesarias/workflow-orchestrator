"""Rate limiting utilities."""

import time
from collections import defaultdict
from dataclasses import dataclass, field

import structlog

from orchestrator.api.metrics import RATE_LIMIT_HITS

logger = structlog.get_logger()


@dataclass
class RateLimitEntry:
    """Track rate limit state for a user."""

    requests: list[float] = field(default_factory=list)


class RateLimiter:
    """In-memory rate limiter (per-process, not distributed)."""

    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._entries: dict[str, RateLimitEntry] = defaultdict(RateLimitEntry)

    def is_allowed(self, key: str, bot_name: str = "unknown") -> bool:
        """Check if a request is allowed for the given key."""
        now = time.time()
        entry = self._entries[key]

        # Remove requests outside the window
        cutoff = now - self.window_seconds
        entry.requests = [ts for ts in entry.requests if ts > cutoff]

        if len(entry.requests) >= self.max_requests:
            logger.warning(
                "Rate limit exceeded",
                key=key,
                requests=len(entry.requests),
                max_requests=self.max_requests,
            )
            RATE_LIMIT_HITS.labels(bot=bot_name, user=key).inc()
            return False

        entry.requests.append(now)
        return True

    def get_remaining(self, key: str) -> int:
        """Get remaining requests for the key."""
        now = time.time()
        entry = self._entries[key]
        cutoff = now - self.window_seconds
        current = len([ts for ts in entry.requests if ts > cutoff])
        return max(0, self.max_requests - current)

    def reset(self, key: str) -> None:
        """Reset rate limit for a key."""
        if key in self._entries:
            del self._entries[key]
