"""Tests de la búsqueda de piso. Sin pytest: `python tests/test_pisos.py`.

Los casos salen de respuestas reales de los conectores (24/11/2026): Expedia
devolviendo hoteles con el filtro de 2 dormitorios puesto, y Booking dando
varias tarifas por vivienda, la más barata para 2 personas.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pisos import clasifica as cl  # noqa: E402
from pisos import config as config_mod  # noqa: E402
from pisos import report as report_mod  # noqa: E402
from pisos.ingest import DatosInvalidos, ingest  # noqa: E402
from pisos.plan import candidatos_a_verificar, ventanas, ventanas_de_hoy  # noqa: E402
from pisos.store import Store  # noqa: E402

fallos: list[str] = []


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    print(f"  [{'OK ' if condicion else 'MAL'}] {nombre}" + (f" — {detalle}" if not condicion else ""))
    if not condicion:
        fallos.append(nombre)


cfg = config_mod.load()
TMP = Path(tempfile.mkdtemp())

print("clasificación por nombre")
casos = {
    "ibis Madrid Norte Las Tablas": cl.DESCARTADO,
    "Residencia Universitaria Resa Erasmo": cl.DESCARTADO,
    "Rooms San Marcos": cl.DESCARTADO,
    "habitación con 2 ventanas al exterior": cl.DESCARTADO,
    "Stylish Duplex-1Bedroom-1.5Baths-Delicias Atocha": cl.DESCARTADO,
    "Río Manzanares - Apartamento de 1 dormitorio": cl.DESCARTADO,
    "Estudio Superior": cl.DESCARTADO,
    "Apartamento de 2 dormitorios": cl.CONFIRMADO,
    "Bright 3-Bedroom flat near Retiro": cl.CONFIRMADO,
    "Two bedroom apartment Lavapiés": cl.CONFIRMADO,
    "Apartamentos Acogedores - Nuevo a estrenar": cl.SIN_VERIFICAR,
    "GOYA-APARTMENT-ARTES": cl.SIN_VERIFICAR,
    "Oceanview Paradise 2BR High-speed Wifi Office Walk to Beach Restaurants Costa Adeje": cl.DESCARTADO,
    "Parque Emperatriz III · Studio with A/C": cl.DESCARTADO,
    "Sarria Living La Vaguada Suites": cl.SIN_VERIFICAR,
}
for nombre, esperado in casos.items():
    estado = cl.clasifica_por_nombre(nombre)[0]
    check(nombre, estado == esperado, f"{estado} != {esperado}")

print("distancia y ocupación")
check("Sol a Sol = 0", cl.km_desde_sol(40.416775, -3.703790) == 0)
km = cl.km_desde_sol(40.333671, -3.703051)   # Villaverde
check("Villaverde ~9 km", 9.0 <= km <= 9.6, str(km))
url = ("https://www.booking.com/hotel/es/x.html?checkin=2027-06-07&checkout=2027-06-28"
       "&highlighted_blocks=179638006_108233613_3_0_0")
check("ocupación de bloque = 3", cl.ocupacion_de_bloque(url) == 3)
check("bloque ilegible = None", cl.ocupacion_de_bloque("https://x") is None)
check("bloque _0_ (tarifa plana) = None", cl.ocupacion_de_bloque("highlighted_blocks=39516710_88209049_0_0_0") is None)

print("plan")
vs = ventanas(cfg)
check("24 ventanas", len(vs) == 24, str(len(vs)))
check("ninguna sale después de 'hasta'", all(v.checkout <= cfg.hasta for v in vs))


def bk(id_, nombre, total, ci="2027-06-07", co="2027-06-28", lat=40.40, lon=-3.70, tipo="Apartamento"):
    return {"id": id_, "nombre": nombre, "total_usd": total, "lat": lat, "lon": lon, "tipo": tipo,
            "url": f"https://www.booking.com/hotel/es/{id_}.html?checkin={ci}&checkout={co}&x=1"}


def run(fecha: str, busquedas: list, verificaciones: list | None = None, nombre="in.json"):
    p = TMP / nombre
    p.write_text(json.dumps({"run_date": fecha, "busquedas": busquedas,
                             "verificaciones": verificaciones or []}), encoding="utf-8")
    return p


db = TMP / "t.db"
print("ingesta")
with Store(db) as st:
    malas = run("2026-11-24", [{"source": "booking", "checkin": "2027-06-07", "checkout": "2027-06-28",
                                "ok": True, "resultados": [bk("1", "Piso", 2500, ci="2027-06-08")]}])
    try:
        ingest(cfg, st, malas)
        check("rechaza fechas del enlace que no cuadran", False)
    except DatosInvalidos:
        check("rechaza fechas del enlace que no cuadran", True)

with Store(db) as st:
    ok_ = run("2026-11-24", [
        {"source": "booking", "checkin": "2027-06-07", "checkout": "2027-06-28", "ok": True,
         "resultados": [bk("100", "CriteriaHome nice&easy", 2460.63),
                        bk("200", "Apartamento de 2 dormitorios Usera", 2700),
                        bk("300", "ibis Madrid", 2000, tipo="Hotel"),
                        bk("400", "Piso lejos", 2100, lat=40.55, lon=-3.70)]},
        {"source": "expedia", "checkin": "2027-06-07", "checkout": "2027-06-28", "ok": False,
         "error": "timeout", "resultados": []},
    ])
    r = ingest(cfg, st, ok_)
    check("1 de 2 búsquedas fallidas => degradado", r.degradado)
    nuevos = {e["ext_id"] for e in r.eventos if e["kind"] == "nuevo"}
    check("nuevos = sin verificar + confirmado, sin hotel ni lejano", nuevos == {"100", "200"}, str(nuevos))
    cands = candidatos_a_verificar(cfg, st)
    check("candidatos a verificar excluyen hotel y lejano", {c["id"] for c in cands} == {"100", "200"},
          str(cands))
    check("la ventana buscada sale del plan de mañana",
          ("2027-06-07", "2027-06-28") not in [v.clave for v in ventanas_de_hoy(cfg, st)])

print("verificación con tarifas por ocupación")
base = "https://www.booking.com/hotel/es/100.html?checkin=2027-06-07&checkout=2027-06-28&highlighted_blocks="
verif = [{
    "id": "100", "checkin": "2027-06-07", "checkout": "2027-06-28", "dormitorios": None,
    "habitaciones": [
        {"nombre": "Apartamento Ático", "m2": 69, "ocupacion_max": 4, "sofa_cama": True,
         "tarifas": [{"precio_total": 3055.81, "url": base + "1_2_3_0_0", "cancelacion": "non_refundable"},
                     {"precio_total": 2643.93, "url": base + "1_2_2_0_0"}]},
        {"nombre": "Estudio Superior", "m2": 49, "ocupacion_max": 4,
         "tarifas": [{"precio_total": 2294.80, "url": base + "2_2_2_0_0"}]},
    ],
}]
with Store(db) as st:
    r = ingest(cfg, st, run("2026-11-25", [
        {"source": "booking", "checkin": "2027-06-07", "checkout": "2027-06-28", "ok": True,
         "resultados": [bk("100", "CriteriaHome nice&easy", 2460.63)]}], verif, "v.json"))
    obs = st.one("SELECT * FROM observations WHERE ext_id='100' AND price_kind='verificado'")
    check("elige la tarifa de 3 personas, no la de 2 ni el estudio",
          obs is not None and abs(obs["total_usd"] - 3055.81) < 0.01,
          str(obs and dict(obs)))
    lst = st.listing("booking", "100")
    check("69 m² sin dato => probable", lst["dorm_status"] == cl.PROBABLE, lst["dorm_status"])
    mejores = {m["ext_id"] for m in report_mod._mejores(cfg, st)}
    check("verificado por encima del tope sale de 'mejores'", "100" not in mejores, str(mejores))
    check("el confirmado sigue", "200" in mejores, str(mejores))
    check("el informe se genera", "<title>Piso Madrid 2027</title>" in report_mod.build(cfg, st))

    try:
        ingest(cfg, st, run("2026-11-26", [{"source": "booking", "checkin": "2027-06-07",
                                             "checkout": "2027-06-28", "ok": True, "resultados": []}],
                            [{"id": "200", "checkin": "2027-06-07", "checkout": "2027-06-28",
                              "dormitorios": 2, "habitaciones": []}], "e.json"))
        check("exige evidencia literal de los dormitorios", False)
    except DatosInvalidos:
        check("exige evidencia literal de los dormitorios", True)

print("vivienda con varias unidades (caso Apartamentos Adelfas)")
with Store(db) as st:
    ingest(cfg, st, run("2026-11-29", [
        {"source": "booking", "checkin": "2027-06-07", "checkout": "2027-06-28", "ok": True,
         "resultados": [bk("500", "Apartamentos Adelfas", 2664.86)]}], nombre="a.json"))
    b5 = "https://www.booking.com/hotel/es/500.html?checkin=2027-06-07&checkout=2027-06-28&highlighted_blocks="
    ingest(cfg, st, run("2026-11-29", [
        {"source": "booking", "checkin": "2027-06-07", "checkout": "2027-06-28", "ok": True,
         "resultados": [bk("500", "Apartamentos Adelfas", 2664.86)]}], [{
        "id": "500", "checkin": "2027-06-07", "checkout": "2027-06-28", "dormitorios": None,
        "habitaciones": [
            {"nombre": "Apartamento de 2 dormitorios con terraza", "m2": 70, "ocupacion_max": 6,
             "tarifas": [{"precio_total": 4297.90, "url": b5 + "39516710_88209049_0_0_0"}]},
            {"nombre": "Apartamento Superior", "m2": 55, "ocupacion_max": 4,
             "tarifas": [{"precio_total": 3611.08, "url": b5 + "39516713_371282720_3_0_0"}]},
            {"nombre": "Estudio Superior", "m2": 30, "ocupacion_max": 3,
             "tarifas": [{"precio_total": 2931.34, "url": b5 + "39516711_88209049_0_0_0"}]},
        ]}], "a.json"))
    o = st.one("SELECT * FROM observations WHERE ext_id='500' AND price_kind='verificado'")
    check("usa la unidad de 2 dormitorios aunque haya otras más baratas",
          o is not None and o["room_name"].startswith("Apartamento de 2") and o["total_usd"] == 4297.9,
          str(o and dict(o)))
    check("queda confirmado", st.listing("booking", "500")["dorm_status"] == cl.CONFIRMADO)
    check("y fuera de 'mejores' por precio",
          "500" not in {m["ext_id"] for m in report_mod._mejores(cfg, st)})

print("bajada de precio")
with Store(db) as st:
    r = ingest(cfg, st, run("2026-11-27", [
        {"source": "booking", "checkin": "2027-06-07", "checkout": "2027-06-28", "ok": True,
         "resultados": [bk("200", "Apartamento de 2 dormitorios Usera", 2400)]}], nombre="b.json"))
    k = [(e["kind"], e["ext_id"]) for e in r.eventos]
    check("detecta bajada 2700 → 2400", k == [("bajada", "200")], str(k))
    r = ingest(cfg, st, run("2026-11-28", [
        {"source": "booking", "checkin": "2027-06-07", "checkout": "2027-06-28", "ok": True,
         "resultados": [bk("200", "Apartamento de 2 dormitorios Usera", 2390)]}], nombre="c.json"))
    check("una bajada de <5 % no avisa", r.eventos == [], str(r.eventos))

print()
if fallos:
    print(f"{len(fallos)} fallo(s): {', '.join(fallos)}")
    sys.exit(1)
print("Todo OK")
