"""Persistencia en SQLite: observaciones de precio, alertas y consumo de cuota."""

from __future__ import annotations

import json
import sqlite3
import statistics
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at    TEXT    NOT NULL,
    origin         TEXT    NOT NULL,
    destination    TEXT    NOT NULL,
    label          TEXT    NOT NULL,
    departure_date TEXT    NOT NULL,
    return_date    TEXT,
    trip_nights    INTEGER,
    travel_class   TEXT    NOT NULL,
    adults         INTEGER NOT NULL,
    currency       TEXT    NOT NULL,
    price          REAL    NOT NULL,
    carrier        TEXT,
    stops          INTEGER,
    route_path     TEXT,
    duration       TEXT,
    offer_json     TEXT
);

CREATE INDEX IF NOT EXISTS idx_obs_route
    ON observations (origin, destination, travel_class, observed_at);
CREATE INDEX IF NOT EXISTS idx_obs_dep
    ON observations (origin, destination, departure_date);

CREATE TABLE IF NOT EXISTS alerts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT    NOT NULL,
    level          TEXT    NOT NULL,
    origin         TEXT    NOT NULL,
    destination    TEXT    NOT NULL,
    label          TEXT    NOT NULL,
    departure_date TEXT    NOT NULL,
    return_date    TEXT,
    travel_class   TEXT    NOT NULL,
    currency       TEXT    NOT NULL,
    price          REAL    NOT NULL,
    baseline       REAL,
    drop_pct       REAL,
    zscore         REAL,
    sample_size    INTEGER,
    reason         TEXT    NOT NULL,
    carrier        TEXT,
    stops          INTEGER,
    route_path     TEXT,
    observation_id INTEGER REFERENCES observations(id)
);

CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts (created_at);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    api_calls   INTEGER DEFAULT 0,
    routes      INTEGER DEFAULT 0,
    offers      INTEGER DEFAULT 0,
    alerts      INTEGER DEFAULT 0,
    status      TEXT,
    note        TEXT
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Añade columnas que falten en una base creada por una versión anterior.

        `CREATE TABLE IF NOT EXISTS` no toca una tabla que ya existe, así que sin
        esto un cambio de esquema rompería la pasada diaria con el histórico ya
        acumulado dentro. Sólo se añaden columnas: nunca se borra nada.
        """
        expected = {
            "observations": {
                "route_path": "TEXT",
                "stops": "INTEGER",
                "duration": "TEXT",
                "offer_json": "TEXT",
            },
            "alerts": {
                "route_path": "TEXT",
                "stops": "INTEGER",
                "sample_size": "INTEGER",
            },
        }
        for table, columns in expected.items():
            present = {
                row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")
            }
            if not present:  # la tabla no existe todavía
                continue
            for name, sql_type in columns.items():
                if name not in present:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -------------------------------------------------------- observations

    def record_observation(
        self,
        *,
        origin: str,
        destination: str,
        label: str,
        departure_date: str,
        return_date: str | None,
        trip_nights: int | None,
        travel_class: str,
        adults: int,
        currency: str,
        price: float,
        carrier: str | None,
        stops: int | None,
        duration: str | None,
        route_path: str | None = None,
        raw: dict[str, Any] | None = None,
        observed_at: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO observations (
                observed_at, origin, destination, label, departure_date, return_date,
                trip_nights, travel_class, adults, currency, price, carrier, stops,
                route_path, duration, offer_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                observed_at or utcnow(),
                origin,
                destination,
                label,
                departure_date,
                return_date,
                trip_nights,
                travel_class,
                adults,
                currency,
                price,
                carrier,
                stops,
                route_path,
                duration,
                json.dumps(raw, separators=(",", ":")) if raw else None,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def history_prices(
        self,
        origin: str,
        destination: str,
        travel_class: str,
        *,
        window_days: int,
        departure_month: str | None = None,
        exclude_observation_id: int | None = None,
    ) -> list[float]:
        """Precios históricos de una ruta, opcionalmente sólo de un mes de salida."""
        since = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat(
            timespec="seconds"
        )
        sql = """
            SELECT price FROM observations
             WHERE origin = ? AND destination = ? AND travel_class = ?
               AND observed_at >= ?
        """
        args: list[Any] = [origin, destination, travel_class, since]
        if departure_month:
            sql += " AND substr(departure_date, 1, 7) = ?"
            args.append(departure_month)
        if exclude_observation_id is not None:
            sql += " AND id != ?"
            args.append(exclude_observation_id)
        return [row[0] for row in self.conn.execute(sql, args)]

    def observation_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0])

    def route_summary(self, window_days: int) -> list[sqlite3.Row]:
        since = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat(
            timespec="seconds"
        )
        return list(
            self.conn.execute(
                """
                SELECT origin, destination, label, travel_class,
                       COUNT(*)  AS n,
                       MIN(price) AS min_price,
                       MAX(price) AS max_price,
                       currency,
                       MAX(observed_at) AS last_seen
                  FROM observations
                 WHERE observed_at >= ?
                 GROUP BY origin, destination, travel_class
                 ORDER BY label
                """,
                (since,),
            )
        )

    def monthly_summary(self) -> list[sqlite3.Row]:
        """Precios agrupados por mes de *salida*, no por mes de observación.

        Es la pregunta que de verdad se hace uno mirando el informe: cuándo sale
        barato volar. Agrupar por fecha de observación sólo diría cuándo se
        consultó, que es un artefacto de cuándo corre la pasada.
        """
        return list(
            self.conn.execute(
                """
                SELECT SUBSTR(departure_date, 1, 7) AS month,
                       origin, destination, label, travel_class, currency,
                       COUNT(*)   AS n,
                       MIN(price) AS min_price
                  FROM observations
                 GROUP BY month, origin, travel_class
                 ORDER BY month, origin
                """
            )
        )

    def monthly_medians(self) -> dict[tuple[str, str, str], float]:
        """Mediana por (mes de salida, origen, cabina).

        SQLite no trae mediana, y la media aquí no sirve: los precios de vuelos
        tienen colas largas y una tarifa rara la desplaza entera. Con 167 filas
        sale más barato ordenarlo en Python que inventar percentiles en SQL.
        """
        grupos: dict[tuple[str, str, str], list[float]] = {}
        for row in self.conn.execute(
            "SELECT SUBSTR(departure_date, 1, 7), origin, travel_class, price"
            "  FROM observations"
        ):
            grupos.setdefault((row[0], row[1], row[2]), []).append(row[3])
        return {k: statistics.median(v) for k, v in grupos.items()}

    def latest_prices_for_route(
        self, origin: str, destination: str, travel_class: str, limit: int = 60
    ) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT observed_at, price FROM observations
                 WHERE origin = ? AND destination = ? AND travel_class = ?
                 ORDER BY observed_at DESC LIMIT ?
                """,
                (origin, destination, travel_class, limit),
            )
        )

    # --------------------------------------------------------------- alerts

    def recent_alert_exists(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: str | None,
        cooldown_hours: int,
        travel_class: str,
    ) -> bool:
        since = (datetime.now(timezone.utc) - timedelta(hours=cooldown_hours)).isoformat(
            timespec="seconds"
        )
        row = self.conn.execute(
            """
            SELECT 1 FROM alerts
             WHERE origin = ? AND destination = ? AND departure_date = ?
               AND IFNULL(return_date,'') = IFNULL(?,'')
               AND travel_class = ?
               AND created_at >= ?
             LIMIT 1
            """,
            (origin, destination, departure_date, return_date, travel_class, since),
        ).fetchone()
        return row is not None

    def record_alert(self, **kw: Any) -> int:
        cols = (
            "created_at,level,origin,destination,label,departure_date,return_date,"
            "travel_class,currency,price,baseline,drop_pct,zscore,sample_size,reason,"
            "carrier,stops,route_path,observation_id"
        )
        keys = cols.split(",")
        kw.setdefault("created_at", utcnow())
        values = [kw.get(k) for k in keys]
        cur = self.conn.execute(
            f"INSERT INTO alerts ({cols}) VALUES ({','.join('?' * len(keys))})", values
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def alerts_since(self, hours: int) -> list[sqlite3.Row]:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(
            timespec="seconds"
        )
        return list(
            self.conn.execute(
                """
                SELECT * FROM alerts WHERE created_at >= ?
                 ORDER BY CASE level WHEN 'error' THEN 0 ELSE 1 END, drop_pct DESC
                """,
                (since,),
            )
        )

    def alert_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0])

    # ----------------------------------------------------------------- runs

    @contextmanager
    def run(self) -> Iterator[dict[str, Any]]:
        cur = self.conn.execute(
            "INSERT INTO runs (started_at, status) VALUES (?, 'running')", (utcnow(),)
        )
        run_id = int(cur.lastrowid or 0)
        self.conn.commit()
        stats: dict[str, Any] = {
            "api_calls": 0,
            "routes": 0,
            "offers": 0,
            "alerts": 0,
            "status": "ok",
            "note": None,
        }
        try:
            yield stats
        except BaseException as exc:
            stats["status"] = "error"
            stats["note"] = f"{type(exc).__name__}: {exc}"[:400]
            raise
        finally:
            self.conn.execute(
                """
                UPDATE runs SET finished_at=?, api_calls=?, routes=?, offers=?,
                                alerts=?, status=?, note=?
                 WHERE id=?
                """,
                (
                    utcnow(),
                    stats["api_calls"],
                    stats["routes"],
                    stats["offers"],
                    stats["alerts"],
                    stats["status"],
                    stats["note"],
                    run_id,
                ),
            )
            self.conn.commit()

    def calls_this_month(self) -> int:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        row = self.conn.execute(
            "SELECT IFNULL(SUM(api_calls),0) FROM runs WHERE substr(started_at,1,7) = ?",
            (month,),
        ).fetchone()
        return int(row[0])

    def last_runs(self, limit: int = 10) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            )
        )

    def run_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
