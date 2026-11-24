"""Histórico en SQLite (data/pisos.db, versionado en git como data/prices.db)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    source      TEXT NOT NULL,
    ext_id      TEXT NOT NULL,
    name        TEXT NOT NULL,
    kind        TEXT,
    district    TEXT,
    lat         REAL,
    lon         REAL,
    km          REAL,
    rating      REAL,
    reviews     INTEGER,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    dorm_status TEXT NOT NULL,
    dorm_reason TEXT,
    bedrooms    INTEGER,
    size_sqm    REAL,
    sofa_bed    INTEGER,
    license     TEXT,
    evidence    TEXT,
    verified_at TEXT,
    PRIMARY KEY (source, ext_id)
);

CREATE TABLE IF NOT EXISTS observations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date    TEXT NOT NULL,
    source      TEXT NOT NULL,
    ext_id      TEXT NOT NULL,
    checkin     TEXT NOT NULL,
    checkout    TEXT NOT NULL,
    nights      INTEGER NOT NULL,
    total_usd   REAL NOT NULL,
    price_kind  TEXT NOT NULL,          -- 'busqueda' | 'verificado'
    occupancy   INTEGER,
    cancellation TEXT,
    room_name   TEXT,
    url         TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_listing ON observations (source, ext_id, checkin, checkout);
CREATE INDEX IF NOT EXISTS idx_obs_run ON observations (run_date);

CREATE TABLE IF NOT EXISTS searches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date    TEXT NOT NULL,
    source      TEXT NOT NULL,
    checkin     TEXT NOT NULL,
    checkout    TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    error       TEXT,
    n_results   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date    TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- 'nuevo' | 'bajada'
    source      TEXT NOT NULL,
    ext_id      TEXT NOT NULL,
    checkin     TEXT NOT NULL,
    checkout    TEXT NOT NULL,
    total_usd   REAL NOT NULL,
    prev_usd    REAL,
    price_kind  TEXT NOT NULL DEFAULT 'busqueda'
);

CREATE TABLE IF NOT EXISTS runs (
    run_date    TEXT PRIMARY KEY,
    ingested_at TEXT NOT NULL,
    input_file  TEXT
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        if exc[0] is None:
            self.conn.commit()
        self.conn.close()

    def q(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, tuple(params)))

    def one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, tuple(params)).fetchone()

    def x(self, sql: str, params: Iterable[Any] = ()) -> int:
        cur = self.conn.execute(sql, tuple(params))
        return cur.lastrowid or 0

    # ----------------------------------------------------------- consultas

    def listing(self, source: str, ext_id: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM listings WHERE source=? AND ext_id=?", (source, ext_id))

    def min_previo(self, source: str, ext_id: str, checkin: str, checkout: str,
                   antes_de: str, price_kind: str) -> float | None:
        """Mínimo histórico de la misma vivienda, ventana y tipo de precio.

        Sólo se compara verificado con verificado y búsqueda con búsqueda: el
        precio de búsqueda de Booking puede ser el de 2 personas, y mezclarlos
        daría bajadas que no existen.
        """
        row = self.one(
            "SELECT MIN(total_usd) m FROM observations "
            "WHERE source=? AND ext_id=? AND checkin=? AND checkout=? AND run_date<? "
            "AND price_kind=?",
            (source, ext_id, checkin, checkout, antes_de, price_kind),
        )
        return row["m"] if row and row["m"] is not None else None

    def ultima_busqueda_ok(self) -> dict[tuple[str, str], str]:
        rows = self.q(
            "SELECT checkin, checkout, MAX(run_date) d FROM searches WHERE ok=1 "
            "GROUP BY checkin, checkout"
        )
        return {(r["checkin"], r["checkout"]): r["d"] for r in rows}

    def ultima_pasada(self) -> str | None:
        row = self.one("SELECT MAX(run_date) d FROM runs")
        return row["d"] if row else None
