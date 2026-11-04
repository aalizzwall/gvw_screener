import random
from datetime import datetime, timedelta
from typing import Optional

GLOBAL_COOLDOWN_MINUTES = 15.0
MAX_ESCALATED_COOLDOWN_MINUTES = 30.0
DEFAULT_BATCH_RATIO = 0.1
MIN_BATCH_RATIO = 0.02


def compute_backoff_seconds(attempt: int, base: float = 1.0, max_seconds: float = 30.0) -> float:
    """Exponential backoff with jitter: base * 2^attempt, capped, jittered +/-50%."""
    delay = min(base * (2 ** attempt), max_seconds)
    return delay * (1.0 + random.uniform(0.0, 0.5))


class AdaptiveBackoff:
    """
    Global external-API circuit breaker (429 / rate limit) with adaptive batch ratio.

    Trips a global cooldown on rate limits, escalates it on repeat trips, and exposes
    a dynamically reduced batch ratio while cooling down.
    """

    def __init__(
        self,
        cooldown_minutes: float = GLOBAL_COOLDOWN_MINUTES,
        max_cooldown_minutes: float = MAX_ESCALATED_COOLDOWN_MINUTES,
    ):
        self.cooldown_minutes = cooldown_minutes
        self.max_cooldown_minutes = max_cooldown_minutes
        self.consecutive_rate_limits = 0
        self.cooldown_until: Optional[datetime] = None

    def trip(self) -> datetime:
        self.consecutive_rate_limits += 1
        if self.consecutive_rate_limits > 1:
            minutes = min(
                self.cooldown_minutes * (2 ** (self.consecutive_rate_limits - 1)),
                self.max_cooldown_minutes,
            )
        else:
            minutes = self.cooldown_minutes
        self.cooldown_until = datetime.now() + timedelta(minutes=minutes)
        return self.cooldown_until

    def reset(self) -> None:
        self.consecutive_rate_limits = 0
        self.cooldown_until = None

    def _expire_if_needed(self) -> None:
        if self.cooldown_until is not None and datetime.now() >= self.cooldown_until:
            self.consecutive_rate_limits = 0
            self.cooldown_until = None

    def is_on_cooldown(self) -> bool:
        self._expire_if_needed()
        return self.cooldown_until is not None and datetime.now() < self.cooldown_until

    def retry_after(self) -> Optional[datetime]:
        self._expire_if_needed()
        return self.cooldown_until

    def get_batch_ratio(self, base_ratio: Optional[float] = None) -> float:
        effective_base = DEFAULT_BATCH_RATIO if base_ratio is None else base_ratio
        self._expire_if_needed()
        if self.cooldown_until is not None:
            return min(effective_base, MIN_BATCH_RATIO)
        return effective_base
