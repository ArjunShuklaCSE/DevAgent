"""Small-sample statistics for the report: Wilson intervals, means and medians."""

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

Z_95 = 1.959963984540054


@dataclass(frozen=True)
class Rate:
    k: int
    n: int
    low: float
    high: float

    @property
    def value(self) -> float | None:
        return self.k / self.n if self.n else None

    def __str__(self) -> str:
        if not self.n:
            return "n/a (n = 0)"
        return f"{self.value:.0%} ({self.k}/{self.n}; 95% CI {self.low:.0%} to {self.high:.0%})"


def wilson(k: int, n: int, z: float = Z_95) -> Rate:
    """Wilson score interval: well-behaved at 0/n and n/n, unlike the normal approximation."""
    if n == 0:
        return Rate(0, 0, 0.0, 1.0)
    if not 0 <= k <= n:
        raise ValueError("k must be between 0 and n")
    p = k / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return Rate(k, n, max(0.0, centre - margin), min(1.0, centre + margin))


def mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None
