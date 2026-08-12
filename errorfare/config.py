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

HOSTS = {
    "test": "https://test.api.amadeus.com",
    "production": "https://api.amadeus.com",
}


@dataclass(frozen=True)
class Route:
    origin: str
    destination: str
    label: str
    alert_below: float | None = None

    @property
    def key(self) -> str:
        return f"{self.origin}-{self.destination}"


@dataclass
class Config:
    currency: str = "EUR"
    adults: int = 1
    travel_class: str = "ECONOMY"
    non_stop: bool = False
    environment: str = "test"
    max_api_calls_per_run: int = 55

    min_days_ahead: int = 21
    max_days_ahead: int = 300
    samples_per_route: int = 3
    trip_lengths: list[int] = field(default_factory=lambda: [7, 10, 14])
    prefer_weekend: bool = True
    one_way: bool = False

    min_observations: int = 8
    mad_z_alert: float = 3.0
    mad_z_error: float = 5.0
    drop_pct_alert: float = 0.35
    drop_pct_error: float = 0.60
    cooldown_hours: int = 20
    history_window_days: int = 120

    routes: list[Route] = field(default_factory=list)

    client_id: str | None = None
    client_secret: str | None = None

    @property
    def host(self) -> str:
        return HOSTS[self.environment]

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
        alert_below = item.get("alert_below")
        routes.append(
            Route(
                origin=origin,
                destination=destination,
                label=str(item.get("label") or destination),
                alert_below=float(alert_below) if alert_below is not None else None,
            )
        )

    cfg = Config(
        currency=str(general.get("currency", "EUR")).upper(),
        adults=int(general.get("adults", 1)),
        travel_class=str(general.get("travel_class", "ECONOMY")).upper(),
        non_stop=bool(general.get("non_stop", False)),
        environment=str(general.get("environment", "test")).lower(),
        max_api_calls_per_run=int(general.get("max_api_calls_per_run", 55)),
        min_days_ahead=int(search.get("min_days_ahead", 21)),
        max_days_ahead=int(search.get("max_days_ahead", 300)),
        samples_per_route=int(search.get("samples_per_route", 3)),
        trip_lengths=[int(x) for x in search.get("trip_lengths", [7, 10, 14])],
        prefer_weekend=bool(search.get("prefer_weekend", True)),
        one_way=bool(search.get("one_way", False)),
        min_observations=int(detection.get("min_observations", 8)),
        mad_z_alert=float(detection.get("mad_z_alert", 3.0)),
        mad_z_error=float(detection.get("mad_z_error", 5.0)),
        drop_pct_alert=float(detection.get("drop_pct_alert", 0.35)),
        drop_pct_error=float(detection.get("drop_pct_error", 0.60)),
        cooldown_hours=int(detection.get("cooldown_hours", 20)),
        history_window_days=int(detection.get("history_window_days", 120)),
        routes=routes,
        client_id=os.environ.get("AMADEUS_CLIENT_ID"),
        client_secret=os.environ.get("AMADEUS_CLIENT_SECRET"),
    )

    _validate(cfg)
    DATA_DIR.mkdir(exist_ok=True)
    REPORTS_DIR.mkdir(exist_ok=True)
    return cfg


def _validate(cfg: Config) -> None:
    problems: list[str] = []
    if cfg.environment not in HOSTS:
        problems.append(f"environment debe ser 'test' o 'production', no {cfg.environment!r}")
    if cfg.travel_class not in VALID_CLASSES:
        problems.append(f"travel_class debe ser uno de {sorted(VALID_CLASSES)}")
    if not cfg.routes:
        problems.append("no hay ninguna [[routes]] definida")
    if cfg.min_days_ahead >= cfg.max_days_ahead:
        problems.append("min_days_ahead debe ser menor que max_days_ahead")
    if cfg.samples_per_route < 1:
        problems.append("samples_per_route debe ser >= 1")
    if not cfg.trip_lengths:
        problems.append("trip_lengths no puede estar vacío")
    if cfg.mad_z_error < cfg.mad_z_alert:
        problems.append("mad_z_error debería ser mayor que mad_z_alert")
    if cfg.drop_pct_error < cfg.drop_pct_alert:
        problems.append("drop_pct_error debería ser mayor que drop_pct_alert")
    if problems:
        raise SystemExit("Errores en config.toml:\n  - " + "\n  - ".join(problems))
