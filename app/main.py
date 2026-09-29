"""HTTP API for distribution-protection selectivity verification."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException

from . import db
from .models import (
    FaultResultOut,
    ScenarioIn,
    ScenarioOut,
    ScenarioSummary,
    VerifyResponse,
)
from .verify import CoverageError, verify_fault


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(
    title="Distribution Protection Selectivity Verification",
    version="1.0.0",
    description=(
        "Stores immutable single-source radial (directed-tree) feeder scenarios "
        "and verifies time-current selectivity between the main breaker and "
        "every upstream breaker for each declared fault."
    ),
    lifespan=lifespan,
)


def _load_scenario_or_404(scenario_id: str) -> dict:
    scenario = db.get_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"scenario '{scenario_id}' not found")
    return scenario


def _coverage_http_error(errors: list[CoverageError]) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={
            "error": "insufficient_curve_coverage",
            "message": (
                "fault rejected: breaker curve(s) on the path to the source do "
                "not cover the fault current interval (extrapolation is forbidden)"
            ),
            "faults": [e.as_dict() for e in errors],
        },
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/scenarios", status_code=201, response_model=ScenarioOut)
def create_scenario(payload: ScenarioIn) -> dict:
    """Store a new immutable scenario. Non-tree topologies and malformed
    curves/faults are rejected with 422."""
    record = {
        "id": uuid.uuid4().hex,
        "created_at": _now(),
        **payload.model_dump(by_alias=True),
    }
    db.insert_scenario(record)
    return record


@app.get("/scenarios", response_model=list[ScenarioSummary])
def list_scenarios() -> list[dict]:
    return [
        {
            "id": s["id"],
            "name": s.get("name"),
            "created_at": s["created_at"],
            "node_count": len(s["nodes"]),
            "branch_count": len(s["branches"]),
            "breaker_count": len(s["breakers"]),
            "fault_count": len(s["faults"]),
        }
        for s in db.list_scenarios()
    ]


@app.get("/scenarios/{scenario_id}", response_model=ScenarioOut)
def get_scenario(scenario_id: str) -> dict:
    return _load_scenario_or_404(scenario_id)


@app.post("/scenarios/{scenario_id}/verify", response_model=VerifyResponse)
def verify_scenario(scenario_id: str) -> dict:
    """Verify every fault of the scenario. If any fault's current interval is
    not fully covered by the curves on its path to the source, the request is
    rejected with 422 and nothing is stored."""
    scenario = _load_scenario_or_404(scenario_id)
    results = []
    errors: list[CoverageError] = []
    for fault in scenario["faults"]:
        try:
            results.append(verify_fault(scenario, fault))
        except CoverageError as exc:
            errors.append(exc)
    if errors:
        raise _coverage_http_error(errors)
    db.save_results(scenario_id, _now(), results)
    return {"scenario_id": scenario_id, "results": results}


@app.post("/scenarios/{scenario_id}/faults/{fault_id}/verify", response_model=FaultResultOut)
def verify_single_fault(scenario_id: str, fault_id: str) -> dict:
    """Verify one fault. Rejected with 422 on insufficient curve coverage."""
    scenario = _load_scenario_or_404(scenario_id)
    fault = next((f for f in scenario["faults"] if f["id"] == fault_id), None)
    if fault is None:
        raise HTTPException(
            status_code=404,
            detail=f"fault '{fault_id}' not found in scenario '{scenario_id}'",
        )
    try:
        result = verify_fault(scenario, fault)
    except CoverageError as exc:
        raise _coverage_http_error([exc]) from exc
    db.save_results(scenario_id, _now(), [result])
    return result


@app.get("/scenarios/{scenario_id}/results", response_model=VerifyResponse)
def get_results(scenario_id: str) -> dict:
    """Return the stored verification results (run POST .../verify first)."""
    _load_scenario_or_404(scenario_id)
    results = db.get_results(scenario_id)
    if not results:
        raise HTTPException(
            status_code=404,
            detail=f"no stored results for scenario '{scenario_id}'; "
            "run POST /scenarios/{id}/verify first",
        )
    return {"scenario_id": scenario_id, "results": results}


@app.get("/scenarios/{scenario_id}/results/{fault_id}", response_model=FaultResultOut)
def get_result(scenario_id: str, fault_id: str) -> dict:
    _load_scenario_or_404(scenario_id)
    result = db.get_result(scenario_id, fault_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"no stored result for fault '{fault_id}' in scenario "
            f"'{scenario_id}'; run POST /scenarios/{{id}}/verify first",
        )
    return result
