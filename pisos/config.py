"""Carga de pisos.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "pisos.toml"
DB_PATH = ROOT / "data" / "pisos.db"
INBOX_DIR = ROOT / "data" / "pisos"
REPORTS_DIR = ROOT / "reports"

# Puerta del Sol, kilómetro cero.
SOL = (40.416775, -3.703790)


@dataclass(frozen=True)
class Config:
    ciudad: str
    desde: date
    hasta: date
    noches: tuple[int, ...]
    paso_dias: int
    adultos: int
    ninos_edades: tuple[int, ...]
    dormitorios_min: int
    presupuesto_usd: float
    km_ok: float
    km_max: float
    m2_probable: float
    m2_minimo: float
    ventanas_por_dia: int
    verificaciones_max: int
    bajada_pct: float
    artifact_url: str

    @property
    def personas(self) -> int:
        return self.adultos + len(self.ninos_edades)


def load(path: Path = CONFIG_PATH) -> Config:
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    v, h, r, p = raw["viaje"], raw["huespedes"], raw["requisitos"], raw["pasada"]
    cfg = Config(
        ciudad=v["ciudad"],
        desde=date.fromisoformat(v["desde"]),
        hasta=date.fromisoformat(v["hasta"]),
        noches=tuple(sorted(int(n) for n in v["noches"])),
        paso_dias=int(v["paso_dias"]),
        adultos=int(h["adultos"]),
        ninos_edades=tuple(int(e) for e in h.get("ninos_edades", [])),
        dormitorios_min=int(r["dormitorios_min"]),
        presupuesto_usd=float(r["presupuesto_usd"]),
        km_ok=float(r["km_ok"]),
        km_max=float(r["km_max"]),
        m2_probable=float(r["m2_probable"]),
        m2_minimo=float(r.get("m2_minimo", 0)),
        ventanas_por_dia=int(p["ventanas_por_dia"]),
        verificaciones_max=int(p["verificaciones_max"]),
        bajada_pct=float(p["bajada_pct"]),
        artifact_url=raw.get("informe", {}).get("artifact_url", ""),
    )
    if cfg.hasta <= cfg.desde:
        raise ValueError("pisos.toml: 'hasta' debe ser posterior a 'desde'")
    if cfg.km_ok > cfg.km_max:
        raise ValueError("pisos.toml: km_ok no puede superar km_max")
    return cfg
