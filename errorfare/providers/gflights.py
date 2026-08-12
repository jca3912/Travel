"""Fuente: Google Flights, vía el paquete fast-flights.

Gratis y sin registro. A cambio es un scraper no oficial: si Google cambia el
formato de su página, esto deja de funcionar hasta que actualicen el paquete.
Por eso la pasada diaria avisa en vez de fallar en silencio.
"""

from __future__ import annotations

import time
from datetime import date

from ..config import Config
from .base import FlightProvider, Offer, ProviderError, QuotaExhausted, minutes_to_human

SEAT_MAP = {
    "ECONOMY": "economy",
    "PREMIUM_ECONOMY": "premium-economy",
    "BUSINESS": "business",
    "FIRST": "first",
}


class GoogleFlightsProvider(FlightProvider):
    name = "gflights"
    description = "Google Flights (fast-flights, sin clave)"

    def __init__(self, cfg: Config, *, verbose: bool = False):
        super().__init__()
        self.cfg = cfg
        self.verbose = verbose
        self._last_call = 0.0
        #: Respuestas que no se han podido interpretar. Si son casi todas, el
        #: scraper se ha quedado obsoleto y hay que actualizar fast-flights.
        self.parse_failures = 0

    def check_ready(self) -> None:
        try:
            import fast_flights  # noqa: F401
        except ImportError:
            raise SystemExit(
                "Falta el paquete fast-flights. Instálalo con:\n"
                "  .venv\\Scripts\\python.exe -m pip install fast-flights typing_extensions"
            ) from None

    def _throttle(self) -> None:
        """Un segundo entre consultas: no hay límite publicado, pero tampoco
        hay motivo para ir a por él."""
        elapsed = time.monotonic() - self._last_call
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        self._last_call = time.monotonic()

    def search_offers(
        self,
        origin: str,
        destination: str,
        departure: date,
        return_date: date | None,
        cabin: str,
    ) -> list[Offer]:
        from fast_flights import FlightQuery, Passengers, create_query, get_flights

        if self.calls_made >= self.cfg.max_api_calls_per_run:
            raise QuotaExhausted(
                f"presupuesto de {self.cfg.max_api_calls_per_run} búsquedas agotado"
            )

        legs = [
            FlightQuery(
                date=departure.isoformat(), from_airport=origin, to_airport=destination
            )
        ]
        if return_date is not None:
            legs.append(
                FlightQuery(
                    date=return_date.isoformat(),
                    from_airport=destination,
                    to_airport=origin,
                )
            )

        query = create_query(
            flights=legs,
            trip="round-trip" if return_date else "one-way",
            seat=SEAT_MAP[cabin],
            passengers=Passengers(adults=self.cfg.adults),
            currency=self.cfg.currency,
            # El límite se aplica a CADA trayecto, que es la semántica que queremos
            max_stops=self.cfg.max_stops,
        )

        self._throttle()
        self.calls_made += 1
        try:
            itineraries = list(get_flights(query))
        except (TypeError, IndexError, KeyError, AttributeError) as exc:
            # El parser de fast-flights 3.0.2 no cubre el caso "Google respondió
            # pero sin bloque de vuelos" y revienta con payload[3][0] sobre None.
            # Pasa en combinaciones sin resultados, así que lo tratamos como
            # búsqueda vacía. Si pasara en TODAS, lo canta el aviso de la pasada.
            self.parse_failures += 1
            raise ProviderError(
                f"sin resultados utilizables ({type(exc).__name__} al interpretar "
                f"la respuesta)"
            ) from None
        except Exception as exc:  # red, bloqueo, cambio de formato...
            raise ProviderError(f"{type(exc).__name__}: {exc}") from None

        offers: list[Offer] = []
        for item in itineraries:
            offer = _to_offer(item, departure, return_date, self.cfg.currency)
            if offer is not None:
                offers.append(offer)
        return offers


def _to_offer(item: object, departure: date, ret: date | None, currency: str) -> Offer | None:
    try:
        segments = list(item.flights)  # type: ignore[attr-defined]
        if not segments:
            return None
        price = float(item.price)  # type: ignore[attr-defined]
        if price <= 0:
            return None
        airlines = list(item.airlines) or ["??"]  # type: ignore[attr-defined]
        codes = [segments[0].from_airport.code] + [s.to_airport.code for s in segments]
        duration = sum(int(s.duration) for s in segments)
    except (AttributeError, TypeError, ValueError):
        return None

    return Offer(
        price=price,
        currency=currency,
        carrier=", ".join(airlines[:2]),
        stops=len(segments) - 1,
        route="-".join(codes),
        duration=minutes_to_human(duration),
        departure_date=departure.isoformat(),
        return_date=ret.isoformat() if ret else None,
        raw={"airlines": airlines, "route": codes, "duration_min": duration},
    )
