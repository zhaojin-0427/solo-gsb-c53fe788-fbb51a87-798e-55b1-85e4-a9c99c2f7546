"""Request/response schemas and scenario (topology) validation.

A scenario is an immutable single-source directed tree:

* every branch is directed ``from`` the source side ``to`` the load side;
* every node except the source has exactly one incoming branch, the source
  has none, and all nodes are reachable from the source (connected,
  acyclic arborescence);
* each branch carries at most one breaker;
* curves have >= 2 breakpoints with positive strictly-increasing currents
  and positive operating times;
* each fault references a branch, a positive closed current interval and a
  required ratio R > 1.

Anything else is rejected with HTTP 422.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .selectivity import validate_curve_points


# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #
class BranchIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(min_length=1)
    from_node: str = Field(alias="from", min_length=1)
    to_node: str = Field(alias="to", min_length=1)


class BreakerIn(BaseModel):
    id: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    curve: list[tuple[float, float]] = Field(min_length=2)

    @field_validator("curve")
    @classmethod
    def _check_curve(cls, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        errors = validate_curve_points(points)
        if errors:
            raise ValueError("; ".join(errors))
        return points


class FaultIn(BaseModel):
    id: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    current_min: float = Field(gt=0)
    current_max: float = Field(gt=0)
    ratio: float = Field(gt=1)

    @model_validator(mode="after")
    def _check_interval(self) -> "FaultIn":
        if self.current_min > self.current_max:
            raise ValueError(
                f"fault '{self.id}': current_min ({self.current_min}) must be "
                f"<= current_max ({self.current_max})"
            )
        return self


class ScenarioIn(BaseModel):
    name: Optional[str] = None
    source: str = Field(min_length=1)
    nodes: list[str] = Field(min_length=1)
    branches: list[BranchIn] = Field(default_factory=list)
    breakers: list[BreakerIn] = Field(default_factory=list)
    faults: list[FaultIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_topology(self) -> "ScenarioIn":
        errors = validate_scenario_topology(self)
        if errors:
            raise ValueError("; ".join(errors))
        return self


def validate_scenario_topology(sc: ScenarioIn) -> list[str]:
    """Validate the single-source directed-tree structure. Returns errors."""
    errors: list[str] = []

    # --- nodes -------------------------------------------------------------
    if len(set(sc.nodes)) != len(sc.nodes):
        errors.append("node ids must be unique")
    node_set = set(sc.nodes)
    if sc.source not in node_set:
        errors.append(f"source node '{sc.source}' is not declared in nodes")

    # --- branches ------------------------------------------------------------
    branch_ids = [b.id for b in sc.branches]
    if len(set(branch_ids)) != len(branch_ids):
        errors.append("branch ids must be unique")
    for b in sc.branches:
        if b.from_node not in node_set:
            errors.append(f"branch '{b.id}': from-node '{b.from_node}' is not declared")
        if b.to_node not in node_set:
            errors.append(f"branch '{b.id}': to-node '{b.to_node}' is not declared")
        if b.from_node == b.to_node:
            errors.append(f"branch '{b.id}': self-loops are not allowed")

    if not errors:
        # --- tree shape ------------------------------------------------------
        if len(sc.branches) != len(sc.nodes) - 1:
            errors.append(
                f"not a tree: {len(sc.nodes)} nodes require exactly "
                f"{len(sc.nodes) - 1} branches, got {len(sc.branches)}"
            )
        else:
            incoming: dict[str, list[str]] = {n: [] for n in sc.nodes}
            children: dict[str, list[str]] = {n: [] for n in sc.nodes}
            for b in sc.branches:
                incoming[b.to_node].append(b.from_node)
                children[b.from_node].append(b.to_node)
            for n in sc.nodes:
                expected = 0 if n == sc.source else 1
                if len(incoming[n]) != expected:
                    label = "source node" if n == sc.source else "non-source node"
                    errors.append(
                        f"not a tree: {label} '{n}' must have in-degree "
                        f"{expected}, got {len(incoming[n])}"
                    )
            if not errors:
                # reachability from the source (connected + acyclic)
                seen = set()
                stack = [sc.source]
                while stack:
                    node = stack.pop()
                    if node in seen:
                        continue
                    seen.add(node)
                    stack.extend(children[node])
                unreachable = node_set - seen
                if unreachable:
                    errors.append(
                        "not a tree: node(s) "
                        + ", ".join(f"'{n}'" for n in sorted(unreachable))
                        + " are not reachable from the source "
                        f"'{sc.source}' (disconnected or cyclic)"
                    )

    # --- breakers ------------------------------------------------------------
    breaker_ids = [b.id for b in sc.breakers]
    if len(set(breaker_ids)) != len(breaker_ids):
        errors.append("breaker ids must be unique")
    branch_set = set(branch_ids)
    protected_branches: set[str] = set()
    for br in sc.breakers:
        if br.branch not in branch_set:
            errors.append(f"breaker '{br.id}': branch '{br.branch}' does not exist")
        elif br.branch in protected_branches:
            errors.append(
                f"breaker '{br.id}': branch '{br.branch}' already has a breaker "
                "(at most one breaker per branch)"
            )
        else:
            protected_branches.add(br.branch)

    # --- faults --------------------------------------------------------------
    fault_ids = [f.id for f in sc.faults]
    if len(set(fault_ids)) != len(fault_ids):
        errors.append("fault ids must be unique")
    for f in sc.faults:
        if f.branch not in branch_set:
            errors.append(f"fault '{f.id}': branch '{f.branch}' does not exist")

    return errors


# --------------------------------------------------------------------------- #
# Output models
# --------------------------------------------------------------------------- #
class ScenarioOut(ScenarioIn):
    id: str
    created_at: str


class ScenarioSummary(BaseModel):
    id: str
    name: Optional[str]
    created_at: str
    node_count: int
    branch_count: int
    breaker_count: int
    fault_count: int


class ViolationOut(BaseModel):
    current_from: float
    current_to: float
    from_included: bool
    to_included: bool


class PairOut(BaseModel):
    main_breaker: str
    upstream_breaker: str
    required_ratio: float
    min_ratio: float
    min_ratio_current: float
    compliant: bool
    violations: list[ViolationOut]


FaultStatus = Literal["qualified", "unqualified", "unprotected"]


class FaultResultOut(BaseModel):
    fault_id: str
    branch: str
    current_range: list[float]
    required_ratio: float
    status: FaultStatus
    main_breaker: Optional[str]
    pairs: list[PairOut]


class VerifyResponse(BaseModel):
    scenario_id: str
    results: list[FaultResultOut]
