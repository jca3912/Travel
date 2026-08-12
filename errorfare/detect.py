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

NOMBRES_CABINA = {
    "ECONOMY": "turista",
    "PREMIUM_ECONOMY": "turista premium",
    "BUSINESS": "business",
    "FIRST": "primera",
}


def nombre_cabina(cabin: str) -> str:
    return NOMBRES_CABINA.get(cabin, cabin.lower())


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
    store: Store, cfg: Config, route: Route, departure_date: str, cabin: str
) -> tuple[list[float], str]:
    """Histórico más específico que tenga muestra suficiente.

    Preferimos comparar contra el mismo mes de salida (agosto no vale lo mismo
    que febrero). Si no hay datos de ese mes, caemos al histórico de la ruta.
    Siempre dentro de la misma cabina: mezclar turista y business daría una
    mediana que no describe ninguna de las dos.
    """
    month = departure_date[:7]
    monthly = store.history_prices(
        route.origin,
        route.destination,
        cabin,
        window_days=cfg.history_window_days,
        departure_month=month,
    )
    if len(monthly) >= cfg.min_observations:
        return monthly, "mes"

    overall = store.history_prices(
        route.origin,
        route.destination,
        cabin,
        window_days=cfg.history_window_days,
    )
    if len(overall) >= cfg.min_observations:
        return overall, "ruta"

    return overall, "ninguno"


def economy_median(
    store: Store, cfg: Config, route: Route, departure_date: str
) -> float | None:
    """Mediana de turista para la misma ruta, como referencia cruzada."""
    history, scope = baseline_for(store, cfg, route, departure_date, "ECONOMY")
    if scope == "ninguno" or not history:
        return None
    return statistics.median(history)


def evaluate(
    cfg: Config,
    store: Store,
    route: Route,
    price: float,
    departure_date: str,
    cabin: str = "ECONOMY",
) -> Verdict:
    history, scope = baseline_for(store, cfg, route, departure_date, cabin)
    n = len(history)

    level: str | None = None
    reasons: list[str] = []
    median = drop_pct = z = None

    # ---- 1. Umbral fijo definido por el usuario (funciona desde el día 1)
    umbral = route.threshold(cabin)
    if umbral is not None and price <= umbral:
        level = "chollo"
        reasons.append(f"por debajo de tu umbral de {umbral:.0f} {cfg.currency}")
        # Menos de la mitad del umbral ya no es una oferta, es un síntoma
        if price <= umbral * 0.5:
            level = "error"
            reasons.append("y a menos de la mitad de ese umbral")

    # ---- 1b. Comparación entre cabinas
    # Una business que cuesta poco más que la turista habitual no es una oferta
    # agresiva: casi siempre es una tarifa mal cargada. Es el patrón de los
    # error fares más sonados, y se detecta sin esperar a tener histórico de
    # business, que es justo lo que más tarda en acumularse.
    if cabin != "ECONOMY":
        eco = economy_median(store, cfg, route, departure_date)
        if eco is not None and price <= eco * cfg.cross_cabin_ratio:
            level = _stronger(level, "error")
            reasons.append(
                f"{nombre_cabina(cabin)} a precio de turista "
                f"(mediana de turista {eco:.0f} {cfg.currency})"
            )

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
        # El z-score dice si el precio es raro; la caída porcentual, si merece
        # la pena. Hacen falta las dos: con un histórico muy estable, bajar un
        # 20 % da un z altísimo sin ser ningún chollo.
        if z is not None and z >= cfg.mad_z_error and drop_pct >= cfg.min_drop_for_z_error:
            stat_level = "error"
            reasons.append(f"z={z:.1f} sobre el histórico ({scope}, n={n})")
        elif z is not None and z >= cfg.mad_z_alert and drop_pct >= cfg.min_drop_for_z_alert:
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
