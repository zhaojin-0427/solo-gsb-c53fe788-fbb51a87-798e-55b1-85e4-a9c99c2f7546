"""Core selectivity mathematics.

Breaker operating curves are piecewise-linear in log(current)/log(time)
space.  For a main/upstream breaker pair the ratio

    r(I) = t_upstream(I) / t_main(I)

is examined over a closed fault-current interval [lo, hi].  Because both
curves are log-log linear between their own breakpoints, log r(I) is linear
on every segment of the partition induced by the union of both curves'
breakpoints (plus the interval ends).  Therefore r(I) is monotonic on each
segment, its minimum over the interval is attained at a candidate point,
and the set {I : r(I) < R} is a union of intervals whose interior
boundaries are the exact roots of r(I) = R (solved analytically in log
space).  Equality r(I) == R counts as compliant, so interior roots are
*excluded* from violation ranges while fault-interval endpoints are
*included* whenever the ratio there is strictly below R.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# Relative tolerance when comparing a time ratio against the required
# ratio R.  Values within this band are treated as equality (compliant),
# which makes "equality counts as qualified" robust against float noise.
RATIO_TOL = 1e-9

# Relative tolerance for the curve-coverage check.
COVERAGE_TOL = 1e-9

# Relative tolerance for detecting ties of the minimum ratio.
TIE_TOL = 1e-12


def significant(x: float, digits: int = 12) -> float:
    """Round to ``digits`` significant digits to strip float noise."""
    if x == 0 or not math.isfinite(x):
        return x
    return float(f"{x:.{digits}g}")


def validate_curve_points(points: Sequence[Sequence[float]]) -> list[str]:
    """Return a list of validation errors for curve breakpoints (empty = ok)."""
    errors: list[str] = []
    if len(points) < 2:
        errors.append("curve must contain at least 2 breakpoints")
        return errors
    prev_current = None
    for idx, point in enumerate(points):
        current, time = point[0], point[1]
        if not (current > 0):
            errors.append(f"breakpoint {idx}: current must be positive, got {current}")
        if not (time > 0):
            errors.append(f"breakpoint {idx}: operating time must be positive, got {time}")
        if prev_current is not None and not (current > prev_current):
            errors.append(
                f"breakpoint {idx}: currents must be strictly increasing "
                f"({current} <= {prev_current})"
            )
        prev_current = current
    return errors


@dataclass(frozen=True)
class Curve:
    """A breaker time-current curve, interpolated linearly in log-log space."""

    currents: tuple[float, ...]
    times: tuple[float, ...]
    log_currents: tuple[float, ...] = field(repr=False)
    log_times: tuple[float, ...] = field(repr=False)

    @classmethod
    def from_points(cls, points: Iterable[Sequence[float]]) -> "Curve":
        pts = [(float(p[0]), float(p[1])) for p in points]
        errors = validate_curve_points(pts)
        if errors:
            raise ValueError("invalid curve: " + "; ".join(errors))
        return cls(
            currents=tuple(p[0] for p in pts),
            times=tuple(p[1] for p in pts),
            log_currents=tuple(math.log(p[0]) for p in pts),
            log_times=tuple(math.log(p[1]) for p in pts),
        )

    @property
    def min_current(self) -> float:
        return self.currents[0]

    @property
    def max_current(self) -> float:
        return self.currents[-1]

    def covers(self, lo: float, hi: float) -> bool:
        """True if the whole interval [lo, hi] lies inside the curve's current
        range (extrapolation is forbidden, so full coverage is required)."""
        return (
            self.min_current * (1.0 - COVERAGE_TOL) <= lo
            and hi <= self.max_current * (1.0 + COVERAGE_TOL)
        )

    def log_time(self, current: float) -> float:
        """log of the operating time at ``current`` via log-log linear
        interpolation.  No extrapolation: values are clamped to the nearest
        endpoint segment (callers must ensure :meth:`covers` first)."""
        x = math.log(current)
        xs, ys = self.log_currents, self.log_times
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        k = bisect_right(xs, x) - 1
        x0, x1 = xs[k], xs[k + 1]
        y0, y1 = ys[k], ys[k + 1]
        return y0 + (y1 - y0) * (x - x0) / (x1 - x0)

    def time(self, current: float) -> float:
        return math.exp(self.log_time(current))


def is_violation(ratio: float, required: float) -> bool:
    """True iff ratio < required beyond the equality tolerance band."""
    return ratio < required * (1.0 - RATIO_TOL)


@dataclass
class Violation:
    """A maximal current range where t_upstream / t_main < R."""

    current_from: float
    current_to: float
    from_included: bool
    to_included: bool


@dataclass
class PairAnalysis:
    min_ratio: float
    min_ratio_current: float
    violations: list[Violation]

    @property
    def compliant(self) -> bool:
        return not self.violations


def _append_violation(violations: list[Violation], seg: Violation) -> None:
    """Append ``seg`` merging it with the previous range when they touch at an
    included breakpoint (the ratio is continuous, so a shared endpoint that is
    a violation on both sides belongs to one maximal range)."""
    if violations:
        last = violations[-1]
        if (
            last.current_to == seg.current_from
            and last.to_included
            and seg.from_included
        ):
            last.current_to = seg.current_to
            last.to_included = seg.to_included
            return
    violations.append(seg)


def analyze_pair(main: Curve, upstream: Curve, lo: float, hi: float, required: float) -> PairAnalysis:
    """Analyse selectivity of one upstream breaker against the main breaker.

    Returns the minimum of t_upstream/t_main over [lo, hi] (ties resolved
    toward the smaller current) and every maximal current range where the
    ratio is strictly below ``required``, with boundary inclusion flags.
    """
    # Candidate currents: interval ends plus every interior breakpoint of
    # both curves.  log(ratio) is linear between adjacent candidates.
    points = {lo, hi}
    for c in main.currents:
        if lo < c < hi:
            points.add(c)
    for c in upstream.currents:
        if lo < c < hi:
            points.add(c)
    xs = sorted(points)

    def log_ratio(current: float) -> float:
        return upstream.log_time(current) - main.log_time(current)

    ratios = [math.exp(log_ratio(i)) for i in xs]

    # Minimum ratio; on ties keep the smaller current.
    best = 0
    for k in range(1, len(xs)):
        if ratios[k] < ratios[best] * (1.0 - TIE_TOL):
            best = k
    min_ratio = ratios[best]
    min_current = xs[best]

    log_required = math.log(required)
    violations: list[Violation] = []

    if len(xs) == 1:
        # Degenerate interval [lo, lo].
        if is_violation(ratios[0], required):
            violations.append(Violation(xs[0], xs[0], True, True))
    else:
        for k in range(len(xs) - 1):
            a, b = xs[k], xs[k + 1]
            ra, rb = ratios[k], ratios[k + 1]
            va, vb = is_violation(ra, required), is_violation(rb, required)
            if not va and not vb:
                continue
            if va and vb:
                seg = Violation(a, b, True, True)
            else:
                # Exactly one endpoint violates: solve log r(I) = log R for
                # the root inside (a, b) — linear in log space.
                la, lb = log_ratio(a), log_ratio(b)
                w = (log_required - la) / (lb - la)
                root = math.exp(math.log(a) + (math.log(b) - math.log(a)) * w)
                if va:
                    seg = Violation(a, root, True, False)
                else:
                    seg = Violation(root, b, False, True)
            _append_violation(violations, seg)

    return PairAnalysis(
        min_ratio=significant(min_ratio),
        min_ratio_current=significant(min_current),
        violations=[
            Violation(
                current_from=significant(v.current_from),
                current_to=significant(v.current_to),
                from_included=v.from_included,
                to_included=v.to_included,
            )
            for v in violations
        ],
    )
