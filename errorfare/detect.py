"""Motor de detección: umbral fijo + estadística robusta sobre el histórico.

Se usa mediana + MAD (desviación absoluta mediana) en vez de media + sigma
porque los precios de vuelos tienen colas muy largas: una sola tarifa de
business colada en el histórico destroza una media, pero no mueve la mediana.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from .config import Config, Route
from .store import Store

# Constante que hace el MAD comparable a una desviación típica en distribución normal
MAD_TO_SIGMA = 0.6745

LEVEL_ORDER = {None: 0, "chollo": 1, "error": 2}


@dataclass
class Verdict:
    level: str | None
    reason: str
    baseline: float | None = None
    drop_pct: float | None = None
    zscore: float | None = None
    sample_size: int = 0
    scope: str = "ninguno"  # "mes" | "ruta" | "ninguno"

    @property
    def is_alert(self) -> bool:
        return self.level is not None


def _stronger(a: str | None, b: str | None) -> str | None:
    return a if LEVEL_ORDER[a] >= LEVEL_ORDER[b] else b


def baseline_for(
    store: Store, cfg: Config, route: Route, departure_date: str
) -> tuple[list[float], str]:
    """Histórico más específico que tenga muestra suficiente.

    Preferimos comparar contra el mismo mes de salida (agosto no vale lo mismo
    que febrero). Si no hay datos de ese mes, caemos al histórico de la ruta.
    """
    month = departure_date[:7]
    monthly = store.history_prices(
        route.origin,
        route.destination,
        cfg.travel_class,
        window_days=cfg.history_window_days,
        departure_month=month,
    )
    if len(monthly) >= cfg.min_observations:
        return monthly, "mes"

    overall = store.history_prices(
        route.origin,
        route.destination,
        cfg.travel_class,
        window_days=cfg.history_window_days,
    )
    if len(overall) >= cfg.min_observations:
        return overall, "ruta"

    return overall, "ninguno"


def evaluate(
    cfg: Config,
    store: Store,
    route: Route,
    price: float,
    departure_date: str,
) -> Verdict:
    history, scope = baseline_for(store, cfg, route, departure_date)
    n = len(history)

    level: str | None = None
    reasons: list[str] = []
    median = drop_pct = z = None

    # ---- 1. Umbral fijo definido por el usuario (funciona desde el día 1)
    if route.alert_below is not None and price <= route.alert_below:
        level = "chollo"
        reasons.append(f"por debajo de tu umbral de {route.alert_below:.0f} {cfg.currency}")
        # Menos de la mitad del umbral ya no es una oferta, es un síntoma
        if price <= route.alert_below * 0.5:
            level = "error"
            reasons.append("y a menos de la mitad de ese umbral")

    # ---- 2. Estadística robusta sobre el histórico
    if scope != "ninguno":
        median = statistics.median(history)
        deviations = [abs(x - median) for x in history]
        mad = statistics.median(deviations)
        drop_pct = (median - price) / median if median > 0 else 0.0

        # MAD casi nulo = histórico degenerado (precios idénticos repetidos).
        # En ese caso el z-score se dispara con cualquier cambio, así que no
        # nos fiamos de él y decidimos sólo por caída porcentual.
        mad_usable = mad > median * 0.01
        if mad_usable:
            z = MAD_TO_SIGMA * (median - price) / mad

        stat_level: str | None = None
        if z is not None and z >= cfg.mad_z_error:
            stat_level = "error"
            reasons.append(f"z={z:.1f} sobre el histórico ({scope}, n={n})")
        elif z is not None and z >= cfg.mad_z_alert:
            stat_level = "chollo"
            reasons.append(f"z={z:.1f} sobre el histórico ({scope}, n={n})")

        if drop_pct >= cfg.drop_pct_error:
            stat_level = _stronger(stat_level, "error")
            reasons.append(f"−{drop_pct * 100:.0f}% frente a la mediana ({median:.0f})")
        elif drop_pct >= cfg.drop_pct_alert:
            stat_level = _stronger(stat_level, "chollo")
            reasons.append(f"−{drop_pct * 100:.0f}% frente a la mediana ({median:.0f})")

        level = _stronger(level, stat_level)

    if level is None:
        if scope == "ninguno":
            falta = cfg.min_observations - n
            return Verdict(
                None,
                f"sin base histórica todavía (faltan {max(falta, 0)} observaciones)",
                sample_size=n,
                scope=scope,
            )
        return Verdict(
            None,
            "precio dentro de lo normal",
            baseline=median,
            drop_pct=drop_pct,
            zscore=z,
            sample_size=n,
            scope=scope,
        )

    return Verdict(
        level=level,
        reason="; ".join(reasons),
        baseline=median,
        drop_pct=drop_pct,
        zscore=z,
        sample_size=n,
        scope=scope,
    )
