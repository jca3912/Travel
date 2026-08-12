"""Fuentes de precios disponibles."""

from __future__ import annotations

from ..config import Config
from .base import (
    FlightProvider,
    Offer,
    ProviderError,
    QuotaExhausted,
    minutes_to_human,
)

__all__ = [
    "FlightProvider",
    "Offer",
    "ProviderError",
    "QuotaExhausted",
    "minutes_to_human",
    "PROVIDERS",
    "build",
]

PROVIDERS = ("gflights", "serpapi")


def build(cfg: Config, *, verbose: bool = False) -> FlightProvider:
    """Instancia la fuente indicada en config.toml."""
    if cfg.provider == "gflights":
        from .gflights import GoogleFlightsProvider

        return GoogleFlightsProvider(cfg, verbose=verbose)

    if cfg.provider == "serpapi":
        from .serpapi import SerpApiProvider

        return SerpApiProvider(cfg, verbose=verbose)

    raise SystemExit(
        f"Fuente desconocida: {cfg.provider!r}. Opciones: {', '.join(PROVIDERS)}"
    )
