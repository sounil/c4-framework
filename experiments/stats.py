"""Small, dependency-free statistics helpers (Wilson CI, Fisher exact, odds ratio)."""
from __future__ import annotations

import math
from typing import Optional


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _hypergeom_pmf(a: int, r1: int, r2: int, c1: int) -> float:
    """P(X = a) for a 2x2 table with row sums r1, r2 and first column sum c1."""
    n = r1 + r2
    return math.comb(r1, a) * math.comb(r2, c1 - a) / math.comb(n, c1)


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p-value for table [[a, b], [c, d]] (sum of pmf <= observed)."""
    r1, r2, c1 = a + b, c + d, a + c
    if r1 == 0 or r2 == 0 or c1 == 0 or (b + d) == 0:
        return 1.0
    p_obs = _hypergeom_pmf(a, r1, r2, c1)
    lo, hi = max(0, c1 - r2), min(r1, c1)
    total = 0.0
    for x in range(lo, hi + 1):
        p = _hypergeom_pmf(x, r1, r2, c1)
        if p <= p_obs * (1 + 1e-9):
            total += p
    return min(1.0, total)


def odds_ratio(a: int, b: int, c: int, d: int) -> Optional[float]:
    """OR with Haldane-Anscombe correction when any cell is zero."""
    if min(a, b, c, d) == 0:
        a, b, c, d = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    try:
        return (a * d) / (b * c)
    except ZeroDivisionError:
        return None


def pct(x: float) -> str:
    return "n/a" if x != x else f"{100 * x:.0f}%"


def pct_ci(k: int, n: int) -> str:
    if n == 0:
        return "n/a"
    lo, hi = wilson_ci(k, n)
    return f"{100 * k / n:.0f}% [{100 * lo:.0f}–{100 * hi:.0f}]"
