"""End-to-end API tests using FastAPI's TestClient."""

import math

import pytest
from fastapi.testclient import TestClient

from app.main import app

DEMO_SCENARIO = {
    "name": "demo-feeder",
    "source": "S",
    "nodes": ["S", "A", "B", "C"],
    "branches": [
        {"id": "br1", "from": "S", "to": "A"},
        {"id": "br2", "from": "A", "to": "B"},
        {"id": "br3", "from": "B", "to": "C"},
    ],
    "breakers": [
        {"id": "CB1", "branch": "br2", "curve": [[50, 20], [500, 1.5], [5000, 0.25]]},
        {"id": "CB2", "branch": "br3", "curve": [[50, 10], [500, 1.0], [5000, 0.1]]},
    ],
    "faults": [
        {"id": "F1", "branch": "br1", "current_min": 100, "current_max": 1000, "ratio": 2},
        {"id": "F2", "branch": "br2", "current_min": 100, "current_max": 1000, "ratio": 2},
        {"id": "F3", "branch": "br3", "current_min": 100, "current_max": 1000, "ratio": 2},
        {"id": "F4", "branch": "br3", "current_min": 50, "current_max": 5000, "ratio": 2},
        {"id": "F5", "branch": "br3", "current_min": 1000, "current_max": 3000, "ratio": 2},
        {"id": "F6", "branch": "br3", "current_min": 3000, "current_max": 5000, "ratio": 2},
        {"id": "F7", "branch": "br3", "current_min": 100, "current_max": 1000, "ratio": 1.5},
    ],
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SELECTIVITY_DB", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def scenario_id(client):
    resp = client.post("/scenarios", json=DEMO_SCENARIO)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# --------------------------------------------------------------------------- #
# Scenario storage
# --------------------------------------------------------------------------- #
def test_create_and_get_scenario(client, scenario_id):
    resp = client.get(f"/scenarios/{scenario_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == scenario_id
    assert body["source"] == "S"
    assert body["branches"][0] == {"id": "br1", "from": "S", "to": "A"}
    assert body["breakers"][0]["curve"] == [[50, 20], [500, 1.5], [5000, 0.25]]
    assert len(body["faults"]) == 7


def test_list_scenarios(client, scenario_id):
    resp = client.get("/scenarios")
    assert resp.status_code == 200
    summaries = resp.json()
    assert len(summaries) == 1
    assert summaries[0]["id"] == scenario_id
    assert summaries[0]["fault_count"] == 7


def test_scenario_is_immutable(client, scenario_id):
    assert client.put(f"/scenarios/{scenario_id}", json={}).status_code == 405
    assert client.patch(f"/scenarios/{scenario_id}", json={}).status_code == 405
    assert client.delete(f"/scenarios/{scenario_id}").status_code == 405


def test_unknown_scenario_404(client):
    assert client.get("/scenarios/nope").status_code == 404
    assert client.post("/scenarios/nope/verify").status_code == 404


# --------------------------------------------------------------------------- #
# Topology / input rejection
# --------------------------------------------------------------------------- #
def _base(**overrides):
    scenario = {
        "source": "S",
        "nodes": ["S", "A", "B"],
        "branches": [
            {"id": "br1", "from": "S", "to": "A"},
            {"id": "br2", "from": "A", "to": "B"},
        ],
        "breakers": [],
        "faults": [],
    }
    scenario.update(overrides)
    return scenario


def test_reject_disconnected_graph(client):
    bad = _base(
        nodes=["S", "A", "B", "X", "Y"],
        branches=[
            {"id": "br1", "from": "S", "to": "A"},
            {"id": "br2", "from": "A", "to": "B"},
            {"id": "br3", "from": "X", "to": "Y"},
        ],
    )
    resp = client.post("/scenarios", json=bad)
    assert resp.status_code == 422


def test_reject_cycle(client):
    bad = _base(
        branches=[
            {"id": "br1", "from": "S", "to": "A"},
            {"id": "br2", "from": "A", "to": "B"},
            {"id": "br3", "from": "B", "to": "A"},
        ],
    )
    assert client.post("/scenarios", json=bad).status_code == 422


def test_reject_two_parents(client):
    bad = _base(
        nodes=["S", "A", "B", "C"],
        branches=[
            {"id": "br1", "from": "S", "to": "A"},
            {"id": "br2", "from": "S", "to": "B"},
            {"id": "br3", "from": "A", "to": "C"},
            {"id": "br4", "from": "B", "to": "C"},
        ],
    )
    assert client.post("/scenarios", json=bad).status_code == 422


def test_reject_two_breakers_on_one_branch(client):
    bad = _base(
        breakers=[
            {"id": "CB1", "branch": "br1", "curve": [[10, 1], [100, 0.1]]},
            {"id": "CB2", "branch": "br1", "curve": [[10, 2], [100, 0.2]]},
        ]
    )
    resp = client.post("/scenarios", json=bad)
    assert resp.status_code == 422
    assert "at most one breaker per branch" in resp.text


def test_reject_bad_curves(client):
    # single breakpoint
    bad = _base(breakers=[{"id": "CB1", "branch": "br1", "curve": [[10, 1]]}])
    assert client.post("/scenarios", json=bad).status_code == 422
    # non-increasing currents
    bad = _base(breakers=[{"id": "CB1", "branch": "br1", "curve": [[100, 1], [50, 2]]}])
    assert client.post("/scenarios", json=bad).status_code == 422
    # non-positive time
    bad = _base(breakers=[{"id": "CB1", "branch": "br1", "curve": [[50, 1], [100, 0]]}])
    assert client.post("/scenarios", json=bad).status_code == 422
    # non-positive current
    bad = _base(breakers=[{"id": "CB1", "branch": "br1", "curve": [[0, 1], [100, 2]]}])
    assert client.post("/scenarios", json=bad).status_code == 422


def test_reject_bad_faults(client):
    # ratio must be > 1
    bad = _base(faults=[{"id": "F", "branch": "br1", "current_min": 10, "current_max": 20, "ratio": 1.0}])
    assert client.post("/scenarios", json=bad).status_code == 422
    # interval must be positive and ordered
    bad = _base(faults=[{"id": "F", "branch": "br1", "current_min": 0, "current_max": 20, "ratio": 2}])
    assert client.post("/scenarios", json=bad).status_code == 422
    bad = _base(faults=[{"id": "F", "branch": "br1", "current_min": 30, "current_max": 20, "ratio": 2}])
    assert client.post("/scenarios", json=bad).status_code == 422
    # unknown branch
    bad = _base(faults=[{"id": "F", "branch": "nope", "current_min": 10, "current_max": 20, "ratio": 2}])
    assert client.post("/scenarios", json=bad).status_code == 422


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #
def test_verify_all_faults(client, scenario_id):
    resp = client.post(f"/scenarios/{scenario_id}/verify")
    assert resp.status_code == 200, resp.text
    results = {r["fault_id"]: r for r in resp.json()["results"]}
    assert set(results) == {"F1", "F2", "F3", "F4", "F5", "F6", "F7"}

    # F1: no breaker on the path -> unprotected
    f1 = results["F1"]
    assert f1["status"] == "unprotected"
    assert f1["main_breaker"] is None
    assert f1["pairs"] == []

    # F2: main protection but no upstream -> qualified
    f2 = results["F2"]
    assert f2["status"] == "qualified"
    assert f2["main_breaker"] == "CB1"
    assert f2["pairs"] == []

    # F3: ratio 1.5 < 2 over the whole interval -> unqualified, closed range
    f3 = results["F3"]
    assert f3["status"] == "unqualified"
    assert f3["main_breaker"] == "CB2"
    assert len(f3["pairs"]) == 1
    pair = f3["pairs"][0]
    assert pair["upstream_breaker"] == "CB1"
    assert pair["min_ratio"] == pytest.approx(1.5)
    assert pair["min_ratio_current"] == pytest.approx(500.0)
    assert pair["compliant"] is False
    assert pair["violations"] == [
        {"current_from": 100.0, "current_to": 1000.0,
         "from_included": True, "to_included": True}
    ]

    # F4: equality at 50 is compliant -> open lower bound; root inside
    f4 = results["F4"]
    assert f4["status"] == "unqualified"
    (v,) = f4["pairs"][0]["violations"]
    assert v["current_from"] == pytest.approx(50.0)
    assert v["from_included"] is False
    w = (math.log(2.0) - math.log(1.5)) / (math.log(2.5) - math.log(1.5))
    assert v["current_to"] == pytest.approx(500.0 * 10.0**w, rel=1e-9)
    assert v["to_included"] is False

    # F5: violation starts at interval start (included), ends at root (excluded)
    f5 = results["F5"]
    assert f5["status"] == "unqualified"
    (v,) = f5["pairs"][0]["violations"]
    assert v["current_from"] == pytest.approx(1000.0)
    assert v["from_included"] is True
    assert v["to_included"] is False

    # F6: fully compliant
    f6 = results["F6"]
    assert f6["status"] == "qualified"
    assert f6["pairs"][0]["compliant"] is True
    assert f6["pairs"][0]["violations"] == []

    # F7: minimum ratio equals R -> equality counts as qualified
    f7 = results["F7"]
    assert f7["status"] == "qualified"
    assert f7["pairs"][0]["min_ratio"] == pytest.approx(1.5)
    assert f7["pairs"][0]["min_ratio_current"] == pytest.approx(500.0)


def test_results_are_stored(client, scenario_id):
    assert client.get(f"/scenarios/{scenario_id}/results").status_code == 404
    client.post(f"/scenarios/{scenario_id}/verify")
    resp = client.get(f"/scenarios/{scenario_id}/results")
    assert resp.status_code == 200
    assert len(resp.json()["results"]) == 7
    resp = client.get(f"/scenarios/{scenario_id}/results/F3")
    assert resp.status_code == 200
    assert resp.json()["fault_id"] == "F3"
    assert client.get(f"/scenarios/{scenario_id}/results/FX").status_code == 404


def test_verify_single_fault(client, scenario_id):
    resp = client.post(f"/scenarios/{scenario_id}/faults/F3/verify")
    assert resp.status_code == 200
    assert resp.json()["status"] == "unqualified"
    assert client.post(f"/scenarios/{scenario_id}/faults/FX/verify").status_code == 404


def test_insufficient_coverage_rejected(client):
    scenario = _base(
        breakers=[
            {"id": "CB1", "branch": "br1", "curve": [[50, 2], [500, 0.2]]},
            {"id": "CB2", "branch": "br2", "curve": [[50, 1], [500, 0.1]]},
        ],
        faults=[
            {"id": "Fok", "branch": "br2", "current_min": 100, "current_max": 400, "ratio": 2},
            {"id": "Fbad", "branch": "br2", "current_min": 10, "current_max": 400, "ratio": 2},
        ],
    )
    sid = client.post("/scenarios", json=scenario).json()["id"]

    # whole-scenario verification is rejected, nothing stored
    resp = client.post(f"/scenarios/{sid}/verify")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "insufficient_curve_coverage"
    assert [f["fault_id"] for f in detail["faults"]] == ["Fbad"]
    assert detail["faults"][0]["required_current_range"] == [10.0, 400.0]
    assert client.get(f"/scenarios/{sid}/results").status_code == 404

    # single-fault verification is rejected the same way
    resp = client.post(f"/scenarios/{sid}/faults/Fbad/verify")
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "insufficient_curve_coverage"
    # ... while the covered fault verifies fine
    resp = client.post(f"/scenarios/{sid}/faults/Fok/verify")
    assert resp.status_code == 200
    assert resp.json()["status"] == "qualified"


def test_multiple_upstream_breakers(client):
    scenario = {
        "source": "S",
        "nodes": ["S", "A", "B", "C"],
        "branches": [
            {"id": "br1", "from": "S", "to": "A"},
            {"id": "br2", "from": "A", "to": "B"},
            {"id": "br3", "from": "B", "to": "C"},
        ],
        "breakers": [
            {"id": "CB1", "branch": "br1", "curve": [[10, 30], [1000, 0.3]]},
            {"id": "CB2", "branch": "br2", "curve": [[10, 6], [1000, 0.06]]},
            {"id": "CB3", "branch": "br3", "curve": [[10, 1], [1000, 0.01]]},
        ],
        "faults": [
            {"id": "F", "branch": "br3", "current_min": 20, "current_max": 500, "ratio": 2}
        ],
    }
    sid = client.post("/scenarios", json=scenario).json()["id"]
    resp = client.post(f"/scenarios/{sid}/verify")
    assert resp.status_code == 200
    (result,) = resp.json()["results"]
    assert result["main_breaker"] == "CB3"
    # both upstream breakers are graded against the main one
    assert [p["upstream_breaker"] for p in result["pairs"]] == ["CB2", "CB1"]
    # ratios: CB2/CB3 = 6, CB1/CB3 = 30 -> both >= 2
    assert result["status"] == "qualified"
    assert result["pairs"][0]["min_ratio"] == pytest.approx(6.0)
    assert result["pairs"][1]["min_ratio"] == pytest.approx(30.0)
