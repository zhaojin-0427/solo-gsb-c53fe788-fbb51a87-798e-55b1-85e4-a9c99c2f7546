"""SQLite persistence.

Scenarios are immutable: they are inserted once and never updated or
deleted.  Verification results are deterministic for a given scenario and
are cached per (scenario, fault).
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scenarios (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    payload     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS results (
    scenario_id TEXT NOT NULL,
    fault_id    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    result      TEXT NOT NULL,
    PRIMARY KEY (scenario_id, fault_id)
);
"""


def _db_path() -> str:
    return os.environ.get(
        "SELECTIVITY_DB",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "selectivity.db"),
    )


def _connect() -> sqlite3.Connection:
    path = _db_path()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def insert_scenario(record: dict[str, Any]) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO scenarios (id, created_at, payload) VALUES (?, ?, ?)",
            (record["id"], record["created_at"], json.dumps(record)),
        )


def get_scenario(scenario_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload FROM scenarios WHERE id = ?", (scenario_id,)
        ).fetchone()
    return json.loads(row["payload"]) if row else None


def list_scenarios() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT payload FROM scenarios ORDER BY created_at, id"
        ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def save_results(scenario_id: str, created_at: str, results: list[dict[str, Any]]) -> None:
    with _connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO results (scenario_id, fault_id, created_at, result)"
            " VALUES (?, ?, ?, ?)",
            [
                (scenario_id, r["fault_id"], created_at, json.dumps(r))
                for r in results
            ],
        )


def get_results(scenario_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT result FROM results WHERE scenario_id = ? ORDER BY fault_id",
            (scenario_id,),
        ).fetchall()
    return [json.loads(row["result"]) for row in rows]


def get_result(scenario_id: str, fault_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT result FROM results WHERE scenario_id = ? AND fault_id = ?",
            (scenario_id, fault_id),
        ).fetchone()
    return json.loads(row["result"]) if row else None
