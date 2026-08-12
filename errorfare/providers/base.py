"""Contrato común a todas las fuentes de precios.

Amadeus cerró su portal Self-Service el 17 de julio de 2026 con las claves
existentes desactivadas de un día para otro. La lección es que la fuente de
datos es la pieza volátil del sistema, así que va detrás de una interfaz:
cambiar de proveedor debe ser una línea en config.toml, no una reescritura.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any


class ProviderError(RuntimeError):
    """Fallo de la fuente de datos en una búsqueda concreta."""


class QuotaExhausted(RuntimeError):
    """Se agotó el presupuesto de búsquedas de esta pasada."""


@dataclass
class Offer:
    """Una oferta de vuelo, ya normalizada, venga de donde venga.

    `stops` y `route` describen el trayecto de IDA. Ni Google Flights ni SerpApi
    devuelven la vuelta en la primera respuesta: dan los itinerarios de ida con
    el precio total del ida y vuelta. La vuelta queda acotada porque el límite
    de escalas se aplica en la propia consulta, a los dos trayectos.
    """

    price: float
    currency: str
    carrier: str
    stops: int
    route: str          # "LAS-IAD-MAD"
    duration: str       # "12h 08m"
    departure_date: str
    return_date: str | None
    raw: dict[str, Any] = field(default_factory=dict)


class FlightProvider(ABC):
    """Fuente de precios de vuelos."""

    name: str = "abstracto"
    #: Texto que se enseña en el informe para que se sepa de dónde salen los datos
    description: str = ""

    def __init__(self) -> None:
        self.calls_made = 0

    @abstractmethod
    def search_offers(
        self,
        origin: str,
        destination: str,
        departure: date,
        return_date: date | None,
        cabin: str,
    ) -> list[Offer]:
        """Ofertas de una ruta, fecha y cabina. Lanza ProviderError si falla."""

    def check_ready(self) -> None:
        """Verifica credenciales/dependencias antes de empezar. Lanza SystemExit."""


def minutes_to_human(minutes: int) -> str:
    hours, mins = divmod(max(int(minutes), 0), 60)
    if hours and mins:
        return f"{hours}h {mins:02d}m"
    return f"{hours}h" if hours else f"{mins}m"
