"""Orquestación de una pasada: qué fechas mirar, buscar, guardar y evaluar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .amadeus import AmadeusClient, AmadeusError, Offer, QuotaExhausted
from .config import Config, Route
from .detect import Verdict, evaluate
from .store import Store

FRIDAY = 4


@dataclass
class ScanResult:
    route: Route
    departure: date
    return_date: date | None
    offer: Offer | None
    verdict: Verdict | None
    error: str | None = None


def rotation_for(today: date) -> int:
    """Índice que cambia cada día: reparte la cobertura de fechas entre pasadas."""
    return today.toordinal()


def sample_departures(cfg: Config, today: date, rotation: int) -> list[date]:
    """Reparte `samples_per_route` salidas por la ventana de búsqueda.

    La cuota gratuita no da para barrer 280 días cada mañana, así que cada
    ejecución muestrea unas pocas fechas y el offset rota para que en una
    semana hayas cubierto toda la ventana.
    """
    start = today + timedelta(days=cfg.min_days_ahead)
    end = today + timedelta(days=cfg.max_days_ahead)
    span = (end - start).days
    n = max(cfg.samples_per_route, 1)
    step = max(span // n, 1)
    offset = (rotation * 5) % step

    dates: list[date] = []
    for i in range(n):
        candidate = start + timedelta(days=i * step + offset)
        if cfg.prefer_weekend:
            candidate += timedelta(days=(FRIDAY - candidate.weekday()) % 7)
        if candidate > end:
            candidate = end
        if candidate not in dates:
            dates.append(candidate)
    return dates


def trip_nights_for(cfg: Config, rotation: int) -> int | None:
    if cfg.one_way:
        return None
    return cfg.trip_lengths[rotation % len(cfg.trip_lengths)]


def scan(
    cfg: Config,
    store: Store,
    client: AmadeusClient,
    stats: dict[str, Any],
    *,
    today: date | None = None,
    verbose: bool = True,
) -> list[ScanResult]:
    today = today or date.today()
    rotation = rotation_for(today)
    nights = trip_nights_for(cfg, rotation)
    departures = sample_departures(cfg, today, rotation)

    if verbose:
        vuelta = f"{nights} noches" if nights else "sólo ida"
        print(f"Pasada del {today:%d/%m/%Y} — {vuelta}")
        print(f"Fechas de salida: {', '.join(d.isoformat() for d in departures)}")
        print(f"Presupuesto: {cfg.max_api_calls_per_run} llamadas "
              f"({len(cfg.routes)} rutas × {len(departures)} fechas = "
              f"{len(cfg.routes) * len(departures)} previstas)\n")

    results: list[ScanResult] = []
    quota_hit = False

    for route in cfg.routes:
        if quota_hit:
            break
        stats["routes"] += 1
        for departure in departures:
            ret = departure + timedelta(days=nights) if nights else None
            try:
                offers = client.search_offers(
                    route.origin, route.destination, departure, ret
                )
            except QuotaExhausted as exc:
                print(f"  ! {exc}. Corto la pasada aquí.")
                stats["note"] = str(exc)
                quota_hit = True
                break
            except AmadeusError as exc:
                if verbose:
                    print(f"  ! {route.key} {departure}: {exc}")
                results.append(ScanResult(route, departure, ret, None, None, str(exc)))
                continue

            if not offers:
                if verbose:
                    print(f"  · {route.label:<14} {departure} → sin ofertas")
                results.append(
                    ScanResult(route, departure, ret, None, None, "sin ofertas")
                )
                continue

            cheapest = min(offers, key=lambda o: o.price)
            stats["offers"] += 1

            # Evaluamos ANTES de insertar, para que la observación nueva no
            # contamine su propia línea base.
            verdict = evaluate(cfg, store, route, cheapest.price, departure.isoformat())

            obs_id = store.record_observation(
                origin=route.origin,
                destination=route.destination,
                label=route.label,
                departure_date=departure.isoformat(),
                return_date=ret.isoformat() if ret else None,
                trip_nights=nights,
                travel_class=cfg.travel_class,
                adults=cfg.adults,
                currency=cheapest.currency,
                price=cheapest.price,
                carrier=cheapest.carrier,
                stops=cheapest.stops,
                duration=cheapest.duration,
                raw=cheapest.raw,
            )

            if verdict.is_alert:
                if store.recent_alert_exists(
                    route.origin,
                    route.destination,
                    departure.isoformat(),
                    ret.isoformat() if ret else None,
                    cfg.cooldown_hours,
                ):
                    if verbose:
                        print(f"  · {route.label:<14} {departure} → alerta repetida, silenciada")
                else:
                    store.record_alert(
                        level=verdict.level,
                        origin=route.origin,
                        destination=route.destination,
                        label=route.label,
                        departure_date=departure.isoformat(),
                        return_date=ret.isoformat() if ret else None,
                        travel_class=cfg.travel_class,
                        currency=cheapest.currency,
                        price=cheapest.price,
                        baseline=verdict.baseline,
                        drop_pct=verdict.drop_pct,
                        zscore=verdict.zscore,
                        sample_size=verdict.sample_size,
                        reason=verdict.reason,
                        carrier=cheapest.carrier,
                        stops=cheapest.stops,
                        observation_id=obs_id,
                    )
                    stats["alerts"] += 1
                    tag = "!! POSIBLE ERROR" if verdict.level == "error" else "*  CHOLLO"
                    if verbose:
                        print(
                            f"  {tag}  {route.label} {departure} — "
                            f"{cheapest.price:.0f} {cheapest.currency} ({verdict.reason})"
                        )
            elif verbose:
                print(
                    f"  · {route.label:<14} {departure} → "
                    f"{cheapest.price:>7.0f} {cheapest.currency}  ({verdict.reason})"
                )

            results.append(ScanResult(route, departure, ret, cheapest, verdict))

    stats["api_calls"] = client.calls_made
    return results
