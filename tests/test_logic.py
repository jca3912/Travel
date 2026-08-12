"""Tests de la lógica que no depende de la red.

Sin pytest a propósito: `python tests/test_logic.py` y ya. Cubre lo que puede
romperse en silencio — la preferencia de escalas, el parseo de cada fuente y los
umbrales de detección — no la parte que habla con Google.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from errorfare import config as config_mod  # noqa: E402
from errorfare.providers.base import Offer  # noqa: E402
from errorfare.providers.gflights import _to_offer as gflights_offer  # noqa: E402
from errorfare.providers.serpapi import _to_offer as serpapi_offer  # noqa: E402
from errorfare.scan import pick_offer, sample_departures, trip_nights_for  # noqa: E402

fallos: list[str] = []


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    marca = "OK " if condicion else "MAL"
    print(f"  [{marca}] {nombre}" + (f" — {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


cfg = config_mod.load()


# --------------------------------------------------------- preferencia escalas

def oferta(price: float, stops: int) -> Offer:
    return Offer(
        price=price, currency="USD", carrier="IB", stops=stops,
        route="-".join(["LAS"] + ["XXX"] * stops + ["MAD"]),
        duration="18h", departure_date="2026-10-01", return_date="2026-10-11",
    )


print(f"\nPreferencia de escalas (max={cfg.max_stops}, preferido="
      f"{cfg.preferred_max_stops}, penalización={cfg.stop_penalty_pct}%)")

casos = [
    ("2 escalas apenas más barata gana la de 1", [oferta(900, 1), oferta(800, 2)], 900),
    ("2 escalas mucho más barata gana la de 2", [oferta(900, 1), oferta(700, 2)], 700),
    ("3 escalas se descarta aunque sea regalada", [oferta(900, 1), oferta(300, 3)], 900),
    ("directo más caro pierde con 1 escala barata", [oferta(950, 0), oferta(900, 1)], 900),
    ("justo en el umbral del 15% gana la de 1", [oferta(1000, 1), oferta(870, 2)], 1000),
    ("justo por debajo del umbral gana la de 2", [oferta(1000, 1), oferta(860, 2)], 860),
]
for nombre, ofertas, esperado in casos:
    elegida, _ = pick_offer(cfg, ofertas)
    check(nombre, elegida is not None and elegida.price == esperado,
          f"eligió {elegida.price if elegida else None}, esperaba {esperado}")

elegida, descartadas = pick_offer(cfg, [oferta(300, 3), oferta(250, 4)])
check("todas con demasiadas escalas devuelve None",
      elegida is None and descartadas == 2, f"{elegida}, {descartadas}")
check("lista vacía no revienta", pick_offer(cfg, [])[0] is None)


# ------------------------------------------------------------ parseo gflights

@dataclass
class FakeAirport:
    code: str


@dataclass
class FakeSegment:
    from_airport: FakeAirport
    to_airport: FakeAirport
    duration: int


@dataclass
class FakeItinerary:
    price: int
    airlines: list[str]
    flights: list[FakeSegment]


print("\nParseo de Google Flights")

item = FakeItinerary(
    price=771,
    airlines=["United"],
    flights=[
        FakeSegment(FakeAirport("LAS"), FakeAirport("IAD"), 268),
        FakeSegment(FakeAirport("IAD"), FakeAirport("MAD"), 460),
    ],
)
off = gflights_offer(item, date(2026, 10, 26), date(2026, 11, 5), "USD")
check("precio, escalas y ruta", off is not None and off.price == 771.0
      and off.stops == 1 and off.route == "LAS-IAD-MAD", str(off))
check("duración sumada de los tramos", off is not None and off.duration == "12h 08m",
      off.duration if off else "")
check("fecha de vuelta presente", off is not None and off.return_date == "2026-11-05")

vacio = FakeItinerary(price=0, airlines=[], flights=[])
check("itinerario sin tramos se descarta",
      gflights_offer(vacio, date(2026, 10, 26), None, "USD") is None)


# ------------------------------------------------------------- parseo serpapi

print("\nParseo de SerpApi")

payload = {
    "price": 812,
    "total_duration": 745,
    "flights": [
        {"departure_airport": {"id": "LAX"}, "arrival_airport": {"id": "JFK"},
         "airline": "American", "duration": 330},
        {"departure_airport": {"id": "JFK"}, "arrival_airport": {"id": "MAD"},
         "airline": "Iberia", "duration": 415},
    ],
}
off = serpapi_offer(payload, date(2026, 10, 26), date(2026, 11, 5), "USD")
check("precio, escalas y ruta", off is not None and off.price == 812.0
      and off.stops == 1 and off.route == "LAX-JFK-MAD", str(off))
check("aerolíneas sin repetir", off is not None and off.carrier == "American, Iberia",
      off.carrier if off else "")
check("duración total", off is not None and off.duration == "12h 25m",
      off.duration if off else "")
check("payload roto devuelve None",
      serpapi_offer({"price": 100}, date(2026, 10, 26), None, "USD") is None)


# ------------------------------------------------------------ muestreo fechas

print("\nMuestreo de fechas")

hoy = date(2026, 8, 12)
fechas = sample_departures(cfg, hoy, 100)
check("genera el número pedido", len(fechas) == cfg.samples_per_route,
      f"{len(fechas)} != {cfg.samples_per_route}")
check("todas dentro de la ventana",
      all((f - hoy).days >= cfg.min_days_ahead
          and (f - hoy).days <= cfg.max_days_ahead for f in fechas))
check("sin fechas repetidas", len(set(fechas)) == len(fechas))
check("rotan de un día para otro",
      sample_departures(cfg, hoy, 100) != sample_departures(cfg, hoy, 101))
check("la duración del viaje rota",
      len({trip_nights_for(cfg, r) for r in range(len(cfg.trip_lengths))})
      == len(cfg.trip_lengths))


# ------------------------------------------------- detección entre cabinas

print("\nDetección")

import tempfile  # noqa: E402
from errorfare.detect import evaluate  # noqa: E402
from errorfare.store import Store  # noqa: E402

tmp = Path(tempfile.mkdtemp()) / "test.db"
store = Store(tmp)
ruta = cfg.routes[0]
MES = "2027-03"


def sembrar(cabin: str, precios: list[float]) -> None:
    for p in precios:
        store.record_observation(
            origin=ruta.origin, destination=ruta.destination, label=ruta.label,
            departure_date=f"{MES}-15", return_date=f"{MES}-25", trip_nights=10,
            travel_class=cabin, adults=1, currency="USD", price=p,
            carrier="IB", stops=1, route_path="LAS-JFK-MAD", duration="18h",
        )


# Turista alrededor de 800, business alrededor de 4.000: lo normal en esta ruta
sembrar("ECONOMY", [780, 800, 820, 760, 840, 790, 810, 830, 795, 805])
sembrar("BUSINESS", [3900, 4100, 4000, 3800, 4200, 3950, 4050, 4150, 3850, 4000])

v = evaluate(cfg, store, ruta, 800, f"{MES}-15", "ECONOMY")
check("turista a precio normal no alerta", v.level is None, str(v.level))

v = evaluate(cfg, store, ruta, 3900, f"{MES}-15", "BUSINESS")
check("business a precio normal no alerta", v.level is None, str(v.level))

v = evaluate(cfg, store, ruta, 900, f"{MES}-15", "BUSINESS")
check("business a precio de turista es ERROR", v.level == "error", str(v.level))
check("y lo explica en el motivo", "turista" in v.reason, v.reason)

v = evaluate(cfg, store, ruta, 1500, f"{MES}-15", "BUSINESS")
check("business por debajo de 1,3× turista es ERROR", v.level == "error", str(v.level))

v = evaluate(cfg, store, ruta, 2800, f"{MES}-15", "BUSINESS")
check("business rebajada un 30% es chollo, no error",
      v.level == "chollo", f"{v.level}: {v.reason}")

# Histórico muy estable: sin el suelo de caída mínima, esto daría z enorme
v = evaluate(cfg, store, ruta, 3600, f"{MES}-15", "BUSINESS")
check("bajada pequeña sobre histórico estable no alerta",
      v.level is None, f"{v.level}: {v.reason}")

# La base de turista no debe contaminar la de business ni al revés
v = evaluate(cfg, store, ruta, 4000, f"{MES}-15", "BUSINESS")
check("las cabinas no mezclan histórico",
      v.baseline is not None and v.baseline > 3000, str(v.baseline))

store.close()


# ------------------------------------------------------------------ resultado

print()
if fallos:
    print(f"{len(fallos)} FALLOS: {', '.join(fallos)}")
    sys.exit(1)
print("Todo correcto.")
