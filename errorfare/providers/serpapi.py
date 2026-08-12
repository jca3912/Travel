"""Fuente: Google Flights vía SerpApi (de pago, con clave).

Es el plan B de `gflights`: mismos datos, pero servidos por un tercero que se
encarga de que sigan llegando. Interesa si el scraper directo deja de funcionar
o si Google bloquea la IP desde la que corre la pasada, cosa nada rara cuando
se ejecuta desde un servidor en vez de desde casa.

OJO: este adaptador está escrito contra la documentación de SerpApi pero no se
ha podido probar contra la API real por no tener clave. Al activarlo, revisa la
primera pasada con `-v` antes de fiarte de los datos.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any

from ..config import Config
from .base import FlightProvider, Offer, ProviderError, QuotaExhausted, minutes_to_human

ENDPOINT = "https://serpapi.com/search"
TIMEOUT = 40

TRAVEL_CLASS = {"ECONOMY": 1, "PREMIUM_ECONOMY": 2, "BUSINESS": 3, "FIRST": 4}

# El parámetro `stops` de SerpApi es un tope, no un número exacto:
# 0 = cualquiera, 1 = sin escalas, 2 = 1 o menos, 3 = 2 o menos.
STOPS_PARAM = {0: 1, 1: 2, 2: 3}


class SerpApiProvider(FlightProvider):
    name = "serpapi"
    description = "Google Flights vía SerpApi"

    def __init__(self, cfg: Config, *, verbose: bool = False):
        super().__init__()
        self.cfg = cfg
        self.verbose = verbose
        self.api_key = os.environ.get("SERPAPI_KEY")

    def check_ready(self) -> None:
        if not self.api_key:
            raise SystemExit(
                "Falta SERPAPI_KEY. Añádela al fichero .env:\n"
                "  SERPAPI_KEY=tu_clave\n"
                "Se obtiene en https://serpapi.com (plan de 1.000 búsquedas/mes)."
            )

    def search_offers(
        self,
        origin: str,
        destination: str,
        departure: date,
        return_date: date | None,
    ) -> list[Offer]:
        if self.calls_made >= self.cfg.max_api_calls_per_run:
            raise QuotaExhausted(
                f"presupuesto de {self.cfg.max_api_calls_per_run} búsquedas agotado"
            )

        params: dict[str, Any] = {
            "engine": "google_flights",
            "api_key": self.api_key,
            "departure_id": origin,
            "arrival_id": destination,
            "outbound_date": departure.isoformat(),
            "currency": self.cfg.currency,
            "travel_class": TRAVEL_CLASS[self.cfg.travel_class],
            "adults": self.cfg.adults,
            "stops": STOPS_PARAM.get(self.cfg.max_stops, 0),
            "type": 1 if return_date else 2,
            "hl": "es",
        }
        if return_date is not None:
            params["return_date"] = return_date.isoformat()

        url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"
        self.calls_made += 1
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            if exc.code == 401:
                raise SystemExit(f"SerpApi rechazó la clave: {body}") from None
            raise ProviderError(f"HTTP {exc.code}: {body}") from None
        except urllib.error.URLError as exc:
            raise ProviderError(f"red no disponible: {exc.reason}") from None

        if error := payload.get("error"):
            # "hasn't returned any results" es una respuesta legítima, no un fallo
            if "results" in error.lower():
                return []
            raise ProviderError(str(error)[:200])

        itineraries = (payload.get("best_flights") or []) + (
            payload.get("other_flights") or []
        )
        offers = [
            offer
            for offer in (
                _to_offer(item, departure, return_date, self.cfg.currency)
                for item in itineraries
            )
            if offer is not None
        ]
        return offers


def _to_offer(
    item: dict[str, Any], departure: date, ret: date | None, currency: str
) -> Offer | None:
    try:
        segments = item["flights"]
        if not segments:
            return None
        price = float(item["price"])
        if price <= 0:
            return None
        codes = [segments[0]["departure_airport"]["id"]] + [
            s["arrival_airport"]["id"] for s in segments
        ]
        airlines: list[str] = []
        for seg in segments:
            name = seg.get("airline")
            if name and name not in airlines:
                airlines.append(name)
        duration = int(item.get("total_duration") or sum(s.get("duration", 0) for s in segments))
    except (KeyError, TypeError, ValueError, IndexError):
        return None

    return Offer(
        price=price,
        currency=currency,
        carrier=", ".join(airlines[:2]) or "??",
        stops=len(segments) - 1,
        route="-".join(codes),
        duration=minutes_to_human(duration),
        departure_date=departure.isoformat(),
        return_date=ret.isoformat() if ret else None,
        raw={"airlines": airlines, "route": codes, "duration_min": duration},
    )
