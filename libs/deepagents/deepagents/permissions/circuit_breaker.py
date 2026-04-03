"""Circuit breaker for the permission pipeline.

Tracks consecutive and cumulative denials. When thresholds are exceeded,
degrades to manual-approve-everything mode to prevent runaway denial loops.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CircuitBreaker:
    """Tracks denial patterns and trips when thresholds are exceeded.

    When tripped, all tool calls should route to manual user approval
    regardless of other pipeline layers.

    Args:
        max_consecutive: Trip after this many consecutive denials.
        max_cumulative: Trip after this many total denials in a session.
    """

    max_consecutive: int = 3
    max_cumulative: int = 20
    consecutive_denials: int = field(default=0, init=False)
    cumulative_denials: int = field(default=0, init=False)
    tripped: bool = field(default=False, init=False)

    def record_denial(self) -> bool:
        """Record a denial and check if the breaker should trip.

        Returns:
            True if the circuit breaker just tripped.
        """
        self.consecutive_denials += 1
        self.cumulative_denials += 1

        if not self.tripped and (
            self.consecutive_denials >= self.max_consecutive
            or self.cumulative_denials >= self.max_cumulative
        ):
            self.tripped = True
            return True
        return False

    def record_approval(self) -> None:
        """Record an approval, resetting the consecutive denial counter."""
        self.consecutive_denials = 0

    def reset(self) -> None:
        """Fully reset the circuit breaker state."""
        self.consecutive_denials = 0
        self.cumulative_denials = 0
        self.tripped = False
