"""Qué buscar hoy: ventanas de fechas, parámetros exactos y candidatos a verificar."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import quote, urlencode

from .clasifica import DESCARTADO
from .config import Config
from .store import Store


@dataclass(frozen=True, order=True)
class Ventana:
    checkin: date
    checkout: date

    @property
    def noches(self) -> int:
        return (self.checkout - self.checkin).days

    @property
    def clave(self) -> tuple[str, str]:
        return self.checkin.isoformat(), self.checkout.isoformat()


def ventanas(cfg: Config) -> list[Ventana]:
    out: list[Ventana] = []
    d = cfg.desde
    while d < cfg.hasta:
        for n in cfg.noches:
            fin = d + timedelta(days=n)
            if fin <= cfg.hasta:
                out.append(Ventana(d, fin))
        d += timedelta(days=cfg.paso_dias)
    return out


def ventanas_de_hoy(cfg: Config, store: Store) -> list[Ventana]:
    """Las que llevan más tiempo sin una búsqueda correcta (nunca buscadas primero).

    Así una búsqueda que falla hoy vuelve a salir mañana sola, sin estado extra.
    En el desempate van primero las de 21 noches ("unas 3 semanas" es el plan
    principal), luego 28 por el descuento mensual, y después el resto.
    """
    ultima = store.ultima_busqueda_ok()
    todas = ventanas(cfg)
    pref = {21: 0, 28: 1}
    todas.sort(key=lambda v: (ultima.get(v.clave, ""), pref.get(v.noches, 2),
                              abs(v.noches - 21), v.checkin))
    return todas[: cfg.ventanas_por_dia]


def tope_noche_booking(cfg: Config, v: Ventana) -> int:
    # Margen del 10 %: el precio de la búsqueda de Booking no siempre es el de
    # 3 personas (puede ser más bajo). Mejor traer de más y filtrar después.
    return math.ceil(cfg.presupuesto_usd * 1.10 / v.noches)


def parametros(cfg: Config, v: Ventana) -> dict:
    ci, co = v.clave
    query = (
        f"Whole apartment in {cfg.ciudad.split(',')[0]} for {cfg.adultos} adult and "
        f"{len(cfg.ninos_edades)} children aged {', '.join(map(str, cfg.ninos_edades))}, "
        f"at least {cfg.dormitorios_min} separate bedrooms, about {v.noches} nights, "
        f"max {int(cfg.presupuesto_usd)} USD total"
    )
    return {
        "booking": {
            "tool": "mcp__Booking_com__accommodations_search_headless",
            "args": {
                "destination": cfg.ciudad,
                "checkin_date": ci,
                "checkout_date": co,
                "number_of_adults": cfg.adultos,
                "children_ages": list(cfg.ninos_edades),
                "number_of_rooms": 1,
                "accommodation_types": ["APARTMENT", "HOLIDAY_HOME"],
                "currency": "USD",
                "price": {"maximum": tope_noche_booking(cfg, v)},
                "result_count": 20,
                "user_country_code": "us",
                "user_locale": "es",
                "user_query": query,
            },
        },
        "expedia": {
            "tool": "mcp__Expedia__search_hotels",
            "args": {
                "destination": cfg.ciudad,
                "check_in_date": ci,
                "check_out_date": co,
                "adult_count": cfg.adultos,
                "children_age_list": list(cfg.ninos_edades),
                "property_types": ["VR"],
                "min_bed_rooms": cfg.dormitorios_min,
                "max_total_price": round(cfg.presupuesto_usd * 1.10),
                "sort_type": "CHEAPEST",
                "limit": 20,
                "user_locale": "es-US",
                "user_location": {"country": "US", "region": "NV", "city": "Las Vegas"},
                "client_device_info": {"device_type": "desktop", "agent_name": "ClaudeCode"},
                "query_text": query,
            },
        },
    }


def candidatos_a_verificar(cfg: Config, store: Store) -> list[dict]:
    """Viviendas de Booking aún sin verificar que podrían entrar en presupuesto.

    Sólo Booking: es la única fuente con herramienta de tarifas por vivienda.
    Para cada una se elige la ventana con el precio de búsqueda más bajo.
    """
    rows = store.q(
        """
        SELECT l.ext_id, l.name, l.km, o.checkin, o.checkout, MIN(o.total_usd) p
        FROM listings l JOIN observations o
          ON o.source = l.source AND o.ext_id = l.ext_id AND o.price_kind = 'busqueda'
        WHERE l.source = 'booking' AND l.verified_at IS NULL
          AND l.dorm_status != ? AND (l.km IS NULL OR l.km <= ?)
        GROUP BY l.ext_id
        HAVING p <= ?
        ORDER BY p
        LIMIT ?
        """,
        (DESCARTADO, cfg.km_max, cfg.presupuesto_usd * 1.10, cfg.verificaciones_max),
    )
    return [
        {
            "id": r["ext_id"],
            "nombre": r["name"],
            "checkin": r["checkin"],
            "checkout": r["checkout"],
            "precio_busqueda": round(r["p"], 2),
        }
        for r in rows
    ]


def plan_de_hoy(cfg: Config, store: Store, hoy: date) -> dict:
    vs = ventanas_de_hoy(cfg, store)
    return {
        "run_date": hoy.isoformat(),
        "ventanas": [
            {"checkin": v.clave[0], "checkout": v.clave[1], "noches": v.noches,
             "busquedas": parametros(cfg, v)}
            for v in vs
        ],
    }


def plan_verificacion(cfg: Config, store: Store) -> dict:
    """Se pide DESPUÉS de cargar las búsquedas del día, para verificar ya las
    viviendas nuevas de hoy y que el aviso salga con el precio de 3 personas."""
    cands = candidatos_a_verificar(cfg, store)
    return {
            "detalles": {
                "tool": "mcp__Booking_com__accommodations_details",
                "args": {"accommodation_ids": [int(c["id"]) for c in cands],
                         "user_country_code": "us", "user_locale": "es"},
            } if cands else None,
            "tarifas": [
                {"tool": "mcp__Booking_com__accommodations_room_search",
                 "nombre": c["nombre"], "precio_busqueda": c["precio_busqueda"],
                 "args": {"accommodation_id": int(c["id"]), "checkin_date": c["checkin"],
                          "checkout_date": c["checkout"], "number_of_adults": cfg.adultos,
                          "children_ages": list(cfg.ninos_edades), "currency": "USD",
                          "user_country_code": "us", "user_locale": "es"}}
                for c in cands
            ],
    }


# ------------------------------------------------------------ enlaces a mano

def enlace_airbnb(cfg: Config, v: Ventana) -> str:
    ci, co = v.clave
    params = [
        ("checkin", ci), ("checkout", co),
        ("adults", cfg.adultos), ("children", len(cfg.ninos_edades)),
        ("min_bedrooms", cfg.dormitorios_min),
        ("room_types[]", "Entire home/apt"),
        # Airbnb filtra por precio por noche; el total con comisiones puede
        # pasarse un poco del tope. Por eso el informe dice que hay que mirarlo.
        ("price_max", int(cfg.presupuesto_usd // v.noches)),
        ("currency", "USD"),
    ]
    ciudad = cfg.ciudad.replace(", ", "--").replace(" ", "-")
    return f"https://www.airbnb.com/s/{quote(ciudad)}/homes?{urlencode(params)}"


def enlace_vrbo(cfg: Config, v: Ventana) -> str:
    ci, co = v.clave
    params = {"destination": cfg.ciudad, "startDate": ci, "endDate": co,
              "adults": cfg.personas}
    return f"https://www.vrbo.com/search?{urlencode(params)}"
