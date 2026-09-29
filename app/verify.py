"""Fault verification orchestration.

For each fault the directed path from the faulted branch up to the source
is walked; the nearest breaker on that path is the main protection and all
further breakers are upstream (backup) protections.  Every upstream breaker
is graded against the main one over the fault's whole current interval.
"""

from __future__ import annotations

from typing import Any

from .selectivity import Curve, analyze_pair


class CoverageError(Exception):
    """Raised when a breaker curve on the path to the source does not cover
    the fault's current interval (extrapolation is forbidden)."""

    def __init__(
        self,
        fault_id: str,
        required_range: list[float],
        insufficient: list[dict[str, Any]],
    ) -> None:
        self.fault_id = fault_id
        self.required_range = required_range
        self.insufficient = insufficient
        super().__init__(
            f"fault '{fault_id}': insufficient curve coverage on the path to the source"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "fault_id": self.fault_id,
            "required_current_range": self.required_range,
            "insufficient_breakers": self.insufficient,
        }


def upstream_branch_path(scenario: dict[str, Any], branch_id: str) -> list[dict[str, Any]]:
    """Branches from the faulted branch up to (and including) the branch
    leaving the source node, ordered from the fault toward the source."""
    by_id = {b["id"]: b for b in scenario["branches"]}
    parent_of = {b["to"]: b for b in scenario["branches"]}
    source = scenario["source"]

    path = [by_id[branch_id]]
    node = path[-1]["from"]
    while node != source:
        parent = parent_of[node]  # always exists: topology validated as a tree
        path.append(parent)
        node = parent["from"]
    return path


def verify_fault(scenario: dict[str, Any], fault: dict[str, Any]) -> dict[str, Any]:
    """Verify one fault of an (immutable, already validated) scenario."""
    lo = float(fault["current_min"])
    hi = float(fault["current_max"])
    required = float(fault["ratio"])

    path = upstream_branch_path(scenario, fault["branch"])
    breaker_by_branch = {b["branch"]: b for b in scenario["breakers"]}
    ordered = [breaker_by_branch[b["id"]] for b in path if b["id"] in breaker_by_branch]

    result: dict[str, Any] = {
        "fault_id": fault["id"],
        "branch": fault["branch"],
        "current_range": [lo, hi],
        "required_ratio": required,
    }

    # No breaker at all on the path to the source -> unprotected.
    if not ordered:
        result.update(status="unprotected", main_breaker=None, pairs=[])
        return result

    main = ordered[0]
    upstreams = ordered[1:]
    result["main_breaker"] = main["id"]

    # Main protection but no upstream breaker -> qualified by definition.
    if not upstreams:
        result.update(status="qualified", pairs=[])
        return result

    # Full coverage of the fault interval is mandatory (no extrapolation).
    curves: dict[str, Curve] = {}
    insufficient = []
    for breaker in ordered:
        curve = Curve.from_points(breaker["curve"])
        curves[breaker["id"]] = curve
        if not curve.covers(lo, hi):
            insufficient.append(
                {
                    "breaker_id": breaker["id"],
                    "curve_current_range": [curve.min_current, curve.max_current],
                }
            )
    if insufficient:
        raise CoverageError(fault["id"], [lo, hi], insufficient)

    main_curve = curves[main["id"]]
    pairs = []
    all_compliant = True
    for up in upstreams:
        analysis = analyze_pair(main_curve, curves[up["id"]], lo, hi, required)
        compliant = analysis.compliant
        all_compliant = all_compliant and compliant
        pairs.append(
            {
                "main_breaker": main["id"],
                "upstream_breaker": up["id"],
                "required_ratio": required,
                "min_ratio": analysis.min_ratio,
                "min_ratio_current": analysis.min_ratio_current,
                "compliant": compliant,
                "violations": [
                    {
                        "current_from": v.current_from,
                        "current_to": v.current_to,
                        "from_included": v.from_included,
                        "to_included": v.to_included,
                    }
                    for v in analysis.violations
                ],
            }
        )

    result.update(status="qualified" if all_compliant else "unqualified", pairs=pairs)
    return result
