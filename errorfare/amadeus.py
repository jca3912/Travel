"""Cliente mínimo de la API Amadeus Self-Service (sólo stdlib)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .config import Config

USER_AGENT = "errorfare/0.1 (+personal fare monitor)"
TIMEOUT = 30


class AmadeusError(RuntimeError):
    """Fallo de la API que no tiene sentido reintentar."""


class QuotaExhausted(RuntimeError):
    """Se ha alcanzado el presupuesto de llamadas de esta ejecución."""


@dataclass
class Offer:
    """Una oferta de vuelo ya normalizada."""

    price: float
    currency: str
    carrier: str
    stops: int
    duration: str
    departure_date: str
    return_date: str | None
    raw: dict[str, Any]


def _iso_duration_to_human(iso: str) -> str:
    """PT14H35M -> 14h 35m"""
    if not iso.startswith("PT"):
        return iso
    body = iso[2:]
    hours = minutes = 0
    num = ""
    for ch in body:
        if ch.isdigit():
            num += ch
        elif ch == "H":
            hours = int(num or 0)
            num = ""
        elif ch == "M":
            minutes = int(num or 0)
            num = ""
        else:
            num = ""
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


class AmadeusClient:
    def __init__(self, cfg: Config, *, verbose: bool = False):
        if not cfg.client_id or not cfg.client_secret:
            raise SystemExit(
                "Faltan credenciales. Crea un fichero .env junto a config.toml con:\n"
                "  AMADEUS_CLIENT_ID=tu_api_key\n"
                "  AMADEUS_CLIENT_SECRET=tu_api_secret\n"
                "Se obtienen gratis en https://developers.amadeus.com"
            )
        self.cfg = cfg
        self.verbose = verbose
        self._token: str | None = None
        self._token_expires: datetime = datetime.now(timezone.utc)
        self.calls_made = 0
        self._last_call = 0.0

    # ---------------------------------------------------------------- auth

    def _authenticate(self) -> None:
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.cfg.client_id,
                "client_secret": self.cfg.client_secret,
            }
        ).encode()
        req = urllib.request.Request(
            f"{self.cfg.host}/v1/security/oauth2/token",
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                payload = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise SystemExit(
                f"Amadeus rechazó las credenciales (HTTP {exc.code}). "
                f"Revisa AMADEUS_CLIENT_ID/SECRET y que la app esté en el entorno "
                f"'{self.cfg.environment}'.\n{detail}"
            ) from None
        except urllib.error.URLError as exc:
            raise AmadeusError(f"No hay conexión con Amadeus: {exc.reason}") from None

        self._token = payload["access_token"]
        # 30 s de margen para no usar un token que caduca a mitad de vuelo
        self._token_expires = datetime.now(timezone.utc) + timedelta(
            seconds=int(payload.get("expires_in", 1799)) - 30
        )
        if self.verbose:
            print(f"  [auth] token ok, caduca a las {self._token_expires:%H:%M:%S} UTC")

    def _ensure_token(self) -> str:
        if self._token is None or datetime.now(timezone.utc) >= self._token_expires:
            self._authenticate()
        assert self._token is not None
        return self._token

    # ------------------------------------------------------------ requests

    def _throttle(self) -> None:
        """El entorno de test permite 10 req/s. Nos quedamos muy por debajo."""
        elapsed = time.monotonic() - self._last_call
        if elapsed < 0.25:
            time.sleep(0.25 - elapsed)
        self._last_call = time.monotonic()

    def _get(self, path: str, params: dict[str, Any], *, attempt: int = 1) -> dict[str, Any]:
        if self.calls_made >= self.cfg.max_api_calls_per_run:
            raise QuotaExhausted(
                f"presupuesto de {self.cfg.max_api_calls_per_run} llamadas agotado"
            )

        token = self._ensure_token()
        self._throttle()
        url = f"{self.cfg.host}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
        )

        self.calls_made += 1
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code == 401 and attempt == 1:
                self._token = None
                return self._get(path, params, attempt=attempt + 1)
            if exc.code == 429 and attempt <= 3:
                wait = 2**attempt
                if self.verbose:
                    print(f"  [429] rate limit, espero {wait}s")
                time.sleep(wait)
                return self._get(path, params, attempt=attempt + 1)
            if exc.code >= 500 and attempt <= 2:
                time.sleep(2)
                return self._get(path, params, attempt=attempt + 1)
            raise AmadeusError(f"HTTP {exc.code} en {path}: {_short_error(body)}") from None
        except urllib.error.URLError as exc:
            if attempt <= 2:
                time.sleep(2)
                return self._get(path, params, attempt=attempt + 1)
            raise AmadeusError(f"Red no disponible: {exc.reason}") from None

    # -------------------------------------------------------------- search

    def search_offers(
        self,
        origin: str,
        destination: str,
        departure: date,
        return_date: date | None,
        *,
        max_results: int = 5,
    ) -> list[Offer]:
        params: dict[str, Any] = {
            "originLocationCode": origin,
            "destinationLocationCode": destination,
            "departureDate": departure.isoformat(),
            "adults": self.cfg.adults,
            "currencyCode": self.cfg.currency,
            "travelClass": self.cfg.travel_class,
            "max": max_results,
        }
        if return_date is not None:
            params["returnDate"] = return_date.isoformat()
        if self.cfg.non_stop:
            params["nonStop"] = "true"

        payload = self._get("/v2/shopping/flight-offers", params)
        return [
            offer
            for offer in (
                _parse_offer(item, departure, return_date) for item in payload.get("data", [])
            )
            if offer is not None
        ]


def _parse_offer(item: dict[str, Any], departure: date, ret: date | None) -> Offer | None:
    try:
        price_block = item["price"]
        price = float(price_block.get("grandTotal") or price_block["total"])
        currency = price_block.get("currency", "EUR")
        itineraries = item.get("itineraries", [])
        segments = [s for it in itineraries for s in it.get("segments", [])]
        # "Escalas" = segmentos totales menos un tramo por trayecto
        stops = max(len(segments) - len(itineraries), 0)
        carriers = item.get("validatingAirlineCodes") or [
            segments[0].get("carrierCode", "??") if segments else "??"
        ]
        duration = _iso_duration_to_human(itineraries[0].get("duration", "")) if itineraries else ""
    except (KeyError, ValueError, TypeError, IndexError):
        return None

    return Offer(
        price=price,
        currency=currency,
        carrier=carriers[0],
        stops=stops,
        duration=duration,
        departure_date=departure.isoformat(),
        return_date=ret.isoformat() if ret else None,
        raw=item,
    )


def _short_error(body: str) -> str:
    try:
        payload = json.loads(body)
        errors = payload.get("errors") or []
        if errors:
            first = errors[0]
            bits = [first.get("title"), first.get("detail")]
            return " — ".join(b for b in bits if b)
    except (ValueError, AttributeError):
        pass
    return body[:200]
