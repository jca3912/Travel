"""Carga y validación de config.toml + credenciales."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"

VALID_CLASSES = {"ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"}

# Se declaran aquí, y no en providers/, para no importar en círculo.
VALID_PROVIDERS = ("gflights", "serpapi")


@dataclass(frozen=True)
class Route:
    origin: str
    destination: str
    label: str
    #: Umbral fijo por cabina. Una business a 900 $ es un error de tarifa;
    #: una turista a 900 $ es martes.
    alert_below: dict[str, float] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.origin}-{self.destination}"

    def threshold(self, cabin: str) -> float | None:
        return self.alert_below.get(cabin)


@dataclass
class Config:
    provider: str = "gflights"
    currency: str = "USD"
    adults: int = 1
    #: Cabinas a vigilar. Cada una se busca por separado y mantiene su propio
    #: histórico: los precios de business y turista no son comparables.
    cabins: list[str] = field(default_factory=lambda: ["ECONOMY"])
    max_api_calls_per_run: int = 55

    min_days_ahead: int = 21
    max_days_ahead: int = 300
    samples_per_route: int = 3
    trip_lengths: list[int] = field(default_factory=lambda: [7, 10, 14])
    prefer_weekend: bool = True
    one_way: bool = False
    max_stops: int = 2
    preferred_max_stops: int = 1
    stop_penalty_pct: float = 15.0

    min_observations: int = 8
    mad_z_alert: float = 3.0
    mad_z_error: float = 5.0
    drop_pct_alert: float = 0.35
    drop_pct_error: float = 0.60
    #: Caída mínima para que el z-score pueda disparar por sí solo. Sin esto,
    #: un histórico muy estable convierte cualquier bajada modesta en un z
    #: enorme: estadísticamente raro, pero no un chollo.
    min_drop_for_z_alert: float = 0.15
    min_drop_for_z_error: float = 0.40
    cooldown_hours: int = 20
    history_window_days: int = 120
    #: Una cabina superior que cuesta menos que este múltiplo de la mediana de
    #: turista es la firma clásica del error de tarifa en business.
    cross_cabin_ratio: float = 1.3

    routes: list[Route] = field(default_factory=list)

    @property
    def db_path(self) -> Path:
        return DATA_DIR / "prices.db"


def _load_env_file(path: Path) -> None:
    """Mete el .env en os.environ sin depender de python-dotenv."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Las variables reales del sistema tienen prioridad sobre el .env
        os.environ.setdefault(key, value)


def load(config_path: Path | None = None) -> Config:
    path = config_path or (ROOT / "config.toml")
    if not path.exists():
        raise SystemExit(f"No encuentro la configuración: {path}")

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    _load_env_file(ROOT / ".env")

    general = raw.get("general", {})
    search = raw.get("search", {})
    detection = raw.get("detection", {})

    routes: list[Route] = []
    for i, item in enumerate(raw.get("routes", []), start=1):
        try:
            origin = str(item["origin"]).upper().strip()
            destination = str(item["destination"]).upper().strip()
        except KeyError as exc:
            raise SystemExit(f"Ruta #{i}: falta el campo {exc}") from None
        if len(origin) != 3 or len(destination) != 3:
            raise SystemExit(
                f"Ruta #{i} ({origin}-{destination}): los códigos IATA son de 3 letras"
            )
        raw_threshold = item.get("alert_below")
        thresholds: dict[str, float] = {}
        if isinstance(raw_threshold, dict):
            for cabin, value in raw_threshold.items():
                cabin = cabin.upper()
                if cabin not in VALID_CLASSES:
                    raise SystemExit(
                        f"Ruta #{i}: cabina desconocida en alert_below: {cabin!r}"
                    )
                thresholds[cabin] = float(value)
        elif raw_threshold is not None:
            # Forma antigua: un solo número, que era el de turista
            thresholds["ECONOMY"] = float(raw_threshold)

        routes.append(
            Route(
                origin=origin,
                destination=destination,
                label=str(item.get("label") or destination),
                alert_below=thresholds,
            )
        )

    cfg = Config(
        provider=str(general.get("provider", "gflights")).lower(),
        currency=str(general.get("currency", "USD")).upper(),
        adults=int(general.get("adults", 1)),
        cabins=[str(c).upper() for c in general.get("cabins", ["ECONOMY"])],
        max_api_calls_per_run=int(general.get("max_api_calls_per_run", 55)),
        min_days_ahead=int(search.get("min_days_ahead", 21)),
        max_days_ahead=int(search.get("max_days_ahead", 300)),
        samples_per_route=int(search.get("samples_per_route", 3)),
        trip_lengths=[int(x) for x in search.get("trip_lengths", [7, 10, 14])],
        prefer_weekend=bool(search.get("prefer_weekend", True)),
        one_way=bool(search.get("one_way", False)),
        max_stops=int(search.get("max_stops", 2)),
        preferred_max_stops=int(search.get("preferred_max_stops", 1)),
        stop_penalty_pct=float(search.get("stop_penalty_pct", 15.0)),
        min_observations=int(detection.get("min_observations", 8)),
        mad_z_alert=float(detection.get("mad_z_alert", 3.0)),
        mad_z_error=float(detection.get("mad_z_error", 5.0)),
        drop_pct_alert=float(detection.get("drop_pct_alert", 0.35)),
        drop_pct_error=float(detection.get("drop_pct_error", 0.60)),
        min_drop_for_z_alert=float(detection.get("min_drop_for_z_alert", 0.15)),
        min_drop_for_z_error=float(detection.get("min_drop_for_z_error", 0.40)),
        cooldown_hours=int(detection.get("cooldown_hours", 20)),
        history_window_days=int(detection.get("history_window_days", 120)),
        cross_cabin_ratio=float(detection.get("cross_cabin_ratio", 1.3)),
        routes=routes,
    )

    _validate(cfg)
    DATA_DIR.mkdir(exist_ok=True)
    REPORTS_DIR.mkdir(exist_ok=True)
    return cfg


def _validate(cfg: Config) -> None:
    problems: list[str] = []
    if cfg.provider not in VALID_PROVIDERS:
        problems.append(
            f"provider debe ser uno de {list(VALID_PROVIDERS)}, no {cfg.provider!r}"
        )
    if not cfg.cabins:
        problems.append("cabins no puede estar vacío")
    for cabin in cfg.cabins:
        if cabin not in VALID_CLASSES:
            problems.append(f"cabina desconocida {cabin!r}; válidas: {sorted(VALID_CLASSES)}")
    if len(set(cfg.cabins)) != len(cfg.cabins):
        problems.append("hay cabinas repetidas en `cabins`")
    if cfg.cross_cabin_ratio <= 1:
        problems.append("cross_cabin_ratio debe ser mayor que 1")
    if not cfg.routes:
        problems.append("no hay ninguna [[routes]] definida")
    if cfg.min_days_ahead >= cfg.max_days_ahead:
        problems.append("min_days_ahead debe ser menor que max_days_ahead")
    if cfg.samples_per_route < 1:
        problems.append("samples_per_route debe ser >= 1")
    if not cfg.trip_lengths:
        problems.append("trip_lengths no puede estar vacío")
    if cfg.preferred_max_stops > cfg.max_stops:
        problems.append("preferred_max_stops no puede ser mayor que max_stops")
    if not 0 <= cfg.max_stops <= 2:
        problems.append("max_stops debe estar entre 0 y 2 (es el tope que aceptan las fuentes)")
    if cfg.mad_z_error < cfg.mad_z_alert:
        problems.append("mad_z_error debería ser mayor que mad_z_alert")
    if cfg.drop_pct_error < cfg.drop_pct_alert:
        problems.append("drop_pct_error debería ser mayor que drop_pct_alert")
    if problems:
        raise SystemExit("Errores en config.toml:\n  - " + "\n  - ".join(problems))
