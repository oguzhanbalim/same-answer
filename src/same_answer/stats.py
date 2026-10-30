"""Exact, dependency-free statistics, and the rule that decides when a claim may be made."""

from __future__ import annotations

import math
from collections.abc import Sequence
from fractions import Fraction

Z_95 = 1.959963984540054
ALPHA = 0.05

# With n discordant pairs all pointing one way, the exact two-sided p is 2 * 0.5**n. Five
# pairs give 0.0625 and six give 0.03125, so below six no result can reach 0.05 even before
# any correction.
MIN_DISCORDANT = 6
# When more than this share of pairs cannot be scored, the key is failing, not the agent.
MAX_EXCLUDED_SHARE = 0.20

GAP = "gap"
REVERSE_GAP = "reverse_gap"
NO_CLAIM = "no_claim"
TOO_FEW = "too_few"
TOO_UNSCORABLE = "too_unscorable"


def wilson(k: int, n: int, z: float = Z_95) -> tuple[float, float] | None:
    """Wilson score interval for k successes in n. None when n is zero."""
    if n == 0:
        return None
    if not 0 <= k <= n:
        raise ValueError(f"k must be between 0 and n, got k={k}, n={n}")
    p = k / n
    z2 = z * z
    denominator = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denominator
    # The closed forms at k=0 and k=n are exact, so floating-point dust is not kept.
    low = 0.0 if k == 0 else max(0.0, centre - margin)
    high = 1.0 if k == n else min(1.0, centre + margin)
    return low, high


def sign_test(plus: int, minus: int) -> float:
    """Exact two-sided sign test on discordant pairs. Concordant pairs do not enter."""
    if plus < 0 or minus < 0:
        raise ValueError("counts cannot be negative")
    n = plus + minus
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(plus, minus) + 1))
    return float(min(Fraction(1), Fraction(2 * tail, 2**n)))


def holm(pvalues: Sequence[float]) -> list[float]:
    """Holm step-down adjusted p-values, in the input order.

    Valid under any dependence between the tests, which matters here because every identity
    variant is compared with the same baseline.
    """
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[index]))
        adjusted[index] = running
    return adjusted


def verdict(
    worse: int, better: int, excluded: int, pairs: int, p_holm: float, alpha: float = ALPHA
) -> str:
    """What may be said about one paired comparison.

    ``worse`` counts pairs where the variant came off worse, ``better`` the opposite. Only
    ``gap`` and ``reverse_gap`` are claims. The other three say why no claim is made, and
    none of them means that no gap exists.
    """
    total = pairs + excluded
    if total and excluded / total > MAX_EXCLUDED_SHARE:
        return TOO_UNSCORABLE
    if worse + better < MIN_DISCORDANT:
        return TOO_FEW
    if p_holm >= alpha:
        return NO_CLAIM
    return GAP if worse > better else REVERSE_GAP
