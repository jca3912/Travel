"""Validación y carga del JSON que deja el agente tras consultar los conectores.

El agente transcribe a mano los resultados de las herramientas, así que aquí
se valida todo lo comprobable: que las fechas del enlace coinciden con la
búsqueda, que el enlace es del dominio correcto, que los precios son números
plausibles. Lo que no cuadra se rechaza con un error claro en vez de guardarse.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from . import clasifica as cl
from .config import Config
from .store import Store

FUENTES = {"booking": ("booking.com",), "expedia": ("expedia.com", "vrbo.com")}


class DatosInvalidos(ValueError):
    pass


@dataclass
class Resumen:
    run_date: str
    busquedas: int = 0
    fallidas: int = 0
    resultados: int = 0
    verificadas: int = 0
    eventos: list[dict] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def degradado(self) -> bool:
        return self.busquedas > 0 and self.fallidas / self.busquedas > 0.30


# ----------------------------------------------------------------- validación

def _fecha(valor: object, campo: str) -> str:
    try:
        return date.fromisoformat(str(valor)).isoformat()
    except ValueError as exc:
        raise DatosInvalidos(f"{campo}: fecha no válida {valor!r}") from exc


def _num(valor: object, campo: str, *, opcional: bool = False) -> float | None:
    if valor is None and opcional:
        return None
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise DatosInvalidos(f"{campo}: se esperaba un número y llegó {valor!r}")
    return float(valor)


def _url_ok(url: str, source: str, checkin: str, checkout: str, donde: str) -> None:
    host = urlparse(url).netloc.lower()
    if not any(host == d or host.endswith("." + d) for d in FUENTES[source]):
        raise DatosInvalidos(f"{donde}: el enlace no es de {source}: {url}")
    # Las dos fuentes llevan las fechas en el enlace. Si no coinciden con las
    # de la búsqueda, el resultado se ha copiado en la ventana equivocada.
    if checkin not in url or checkout not in url:
        raise DatosInvalidos(
            f"{donde}: las fechas del enlace no coinciden con {checkin}→{checkout}"
        )


def _validar_resultado(r: dict, source: str, ci: str, co: str, donde: str) -> dict:
    for k in ("id", "nombre", "url", "total_usd"):
        if k not in r:
            raise DatosInvalidos(f"{donde}: falta el campo '{k}'")
    total = _num(r["total_usd"], f"{donde}.total_usd")
    noches = (date.fromisoformat(co) - date.fromisoformat(ci)).days
    if not 15 * noches <= total <= 2000 * noches:
        raise DatosInvalidos(f"{donde}: total {total} USD no es plausible para {noches} noches")
    _url_ok(str(r["url"]), source, ci, co, donde)
    if source == "expedia" and f".h{r['id']}." not in str(r["url"]):
        raise DatosInvalidos(f"{donde}: el id {r['id']} no aparece en el enlace de Expedia")
    lat = _num(r.get("lat"), f"{donde}.lat", opcional=True)
    lon = _num(r.get("lon"), f"{donde}.lon", opcional=True)
    if lat is not None and not (39.5 < lat < 41.5 and -4.8 < (lon or 0) < -2.8):
        raise DatosInvalidos(f"{donde}: coordenadas fuera de la Comunidad de Madrid")
    nota = _num(r.get("nota"), f"{donde}.nota", opcional=True)
    if source == "expedia" and nota is not None and nota <= 5:
        # Expedia puntúa los hoteles sobre 10 y los alquileres vacacionales
        # (inventario Vrbo) sobre 5. Se pasa todo a /10 para comparar con Booking.
        nota = nota * 2
    return {
        "id": str(r["id"]), "nombre": str(r["nombre"]).strip(), "url": str(r["url"]),
        "total": round(total, 2), "lat": lat, "lon": lon,
        "tipo": r.get("tipo"), "distrito": r.get("distrito"),
        "nota": nota,
        "opiniones": int(r["opiniones"]) if r.get("opiniones") is not None else None,
    }


# --------------------------------------------------------------------- carga

def _upsert_listing(store: Store, cfg: Config, source: str, r: dict, run_date: str) -> None:
    km = cl.km_desde_sol(r["lat"], r["lon"])
    prev = store.listing(source, r["id"])
    if prev is None:
        estado, motivo, dorms = cl.clasifica_por_nombre(r["nombre"], r["tipo"])
        store.x(
            "INSERT INTO listings (source, ext_id, name, kind, district, lat, lon, km, rating, "
            "reviews, first_seen, last_seen, dorm_status, dorm_reason, bedrooms) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (source, r["id"], r["nombre"], r["tipo"], r["distrito"], r["lat"], r["lon"], km,
             r["nota"], r["opiniones"], run_date, run_date, estado, motivo, dorms),
        )
        return
    store.x(
        "UPDATE listings SET name=?, kind=COALESCE(?, kind), district=COALESCE(?, district), "
        "lat=COALESCE(?, lat), lon=COALESCE(?, lon), km=COALESCE(?, km), "
        "rating=COALESCE(?, rating), reviews=COALESCE(?, reviews), last_seen=? "
        "WHERE source=? AND ext_id=?",
        (r["nombre"], r["tipo"], r["distrito"], r["lat"], r["lon"], km, r["nota"],
         r["opiniones"], run_date, source, r["id"]),
    )
    if prev["verified_at"] is None:
        # Sin verificar: se reclasifica por si las reglas han cambiado.
        estado, motivo, dorms = cl.clasifica_por_nombre(r["nombre"], r["tipo"])
        store.x(
            "UPDATE listings SET dorm_status=?, dorm_reason=?, bedrooms=? "
            "WHERE source=? AND ext_id=?",
            (estado, motivo, dorms, source, r["id"]),
        )


def _aplicar_verificacion(store: Store, cfg: Config, v: dict, run_date: str,
                          resumen: Resumen) -> None:
    donde = f"verificaciones[{v.get('id')}]"
    ext_id = str(v.get("id"))
    if store.listing("booking", ext_id) is None:
        raise DatosInvalidos(f"{donde}: no existe esa vivienda de Booking en el histórico")
    ci, co = _fecha(v.get("checkin"), donde), _fecha(v.get("checkout"), donde)
    noches = (date.fromisoformat(co) - date.fromisoformat(ci)).days

    dorms_desc = v.get("dormitorios")
    if dorms_desc is not None:
        dorms_desc = int(_num(dorms_desc, f"{donde}.dormitorios"))
        if dorms_desc and not (v.get("evidencia") or "").strip():
            raise DatosInvalidos(f"{donde}: 'dormitorios' necesita la cita literal en 'evidencia'")

    # 1) Cada habitación/unidad: ¿es apta y cuántos dormitorios declara?
    habs = []
    motivos: list[str] = []
    for i, hab in enumerate(v.get("habitaciones") or []):
        nombre = str(hab.get("nombre", ""))
        if (m := cl.motivo_descarte(nombre)):
            motivos.append(f"«{nombre}»: {m}")
            continue
        occ_max = hab.get("ocupacion_max")
        if occ_max is not None and int(occ_max) < cfg.personas:
            motivos.append(f"«{nombre}»: máximo {occ_max} personas")
            continue
        m2 = _num(hab.get("m2"), f"{donde}.habitaciones[{i}].m2", opcional=True)
        if m2 is not None and m2 < cfg.m2_minimo:
            motivos.append(f"«{nombre}»: {m2:g} m², no caben {cfg.dormitorios_min} dormitorios")
            continue
        habs.append((i, hab, cl.dormitorios_en_texto(nombre), m2))

    # 2) Si alguna unidad dice explícitamente tener 2+ dormitorios, sólo cuentan
    #    ésas: la vivienda puede tener también estudios más baratos, y su precio
    #    no es el del piso que buscamos (caso real: Apartamentos Adelfas).
    explicitas = [h for h in habs if (h[2] or 0) >= cfg.dormitorios_min]
    candidatas = explicitas or [h for h in habs if h[2] is None]

    # 3) Tarifa más barata para 3 dentro de las unidades candidatas.
    mejor = None            # (precio, ocupación, hab, tarifa, dorms, m2)
    for i, hab, dorms_h, m2 in candidatas:
        for j, t in enumerate(hab.get("tarifas") or []):
            precio = _num(t.get("precio_total"), f"{donde}.habitaciones[{i}].tarifas[{j}]")
            if (t.get("moneda") or "USD").upper() != "USD":
                raise DatosInvalidos(f"{donde}: tarifa en {t.get('moneda')}, se esperaba USD")
            if not 15 * noches <= precio <= 2000 * noches:
                raise DatosInvalidos(f"{donde}: tarifa {precio} no plausible")
            url = str(t.get("url") or "")
            if url:
                _url_ok(url, "booking", ci, co, donde)
            occ = cl.ocupacion_de_bloque(url)
            if occ is not None and occ < cfg.personas:
                continue        # tarifa para menos personas: no es nuestro precio
            if mejor is None or precio < mejor[0]:
                mejor = (precio, occ, hab, t, dorms_h, m2)

    # 4) Estado de la vivienda según la unidad elegida.
    m2 = sofa = None
    dorms: int | None = None
    if mejor is None:
        estado = cl.DESCARTADO
        motivo = (f"ninguna unidad sirve para {cfg.personas} personas en "
                  f"{cfg.dormitorios_min} dormitorios") + (f" ({'; '.join(motivos)})" if motivos else "")
        if dorms_desc is not None and dorms_desc < cfg.dormitorios_min:
            motivo = f"la descripción indica {dorms_desc} dormitorio(s)"
        dorms = dorms_desc
    else:
        _, _, hab, _, dorms_h, m2 = mejor
        sofa = 1 if hab.get("sofa_cama") else 0
        nombre = hab.get("nombre")
        if dorms_h:
            dorms = dorms_h
            estado, motivo = cl.CONFIRMADO, f"la unidad «{nombre}» indica {dorms_h} dormitorios"
        elif dorms_desc is not None and dorms_desc >= cfg.dormitorios_min:
            dorms = dorms_desc
            estado, motivo = cl.CONFIRMADO, f"la descripción indica {dorms_desc} dormitorios"
        elif dorms_desc is not None:
            dorms = dorms_desc
            estado, motivo = cl.DESCARTADO, f"la descripción indica {dorms_desc} dormitorio(s)"
        elif m2 is not None and m2 >= cfg.m2_probable:
            estado, motivo = cl.PROBABLE, f"«{nombre}», {m2:g} m²; no dice cuántos dormitorios"
        else:
            estado = cl.SIN_VERIFICAR
            motivo = (f"«{nombre}», {m2:g} m², sin dato de dormitorios" if m2 is not None
                      else f"«{nombre}», sin dato de dormitorios ni superficie")

    store.x(
        "UPDATE listings SET dorm_status=?, dorm_reason=?, bedrooms=?, size_sqm=?, sofa_bed=?, "
        "license=COALESCE(?, license), evidence=?, verified_at=? WHERE source='booking' AND ext_id=?",
        (estado, motivo, dorms, m2, sofa, v.get("licencia"), v.get("evidencia"), run_date, ext_id),
    )
    if mejor is not None:
        precio, occ, hab, t, _, _ = mejor
        store.x(
            "INSERT INTO observations (run_date, source, ext_id, checkin, checkout, nights, "
            "total_usd, price_kind, occupancy, cancellation, room_name, url) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_date, "booking", ext_id, ci, co, noches, round(precio, 2), "verificado", occ,
             t.get("cancelacion"), hab.get("nombre"), t.get("url")),
        )
        if occ is None and "highlighted_blocks=" not in str(t.get("url") or ""):
            resumen.avisos.append(
                f"{ext_id}: la tarifa elegida no trae enlace con su bloque; "
                "no se ha podido comprobar que sea la de 3 personas")
    resumen.verificadas += 1


def _detectar_eventos(store: Store, cfg: Config, run_date: str) -> list[dict]:
    """Novedades y bajadas de la pasada, sobre viviendas que cumplen todo."""
    filas = store.q(
        """
        SELECT o.*, l.name, l.first_seen, l.dorm_status, l.km
        FROM observations o JOIN listings l ON l.source=o.source AND l.ext_id=o.ext_id
        WHERE o.run_date=? AND l.dorm_status != ? AND (l.km IS NULL OR l.km <= ?)
        """,
        (run_date, cl.DESCARTADO, cfg.km_max),
    )
    # Precio efectivo por vivienda y ventana: el verificado manda sobre el de búsqueda.
    efectivo: dict[tuple, dict] = {}
    for f in filas:
        k = (f["source"], f["ext_id"], f["checkin"], f["checkout"])
        if k not in efectivo or (f["price_kind"] == "verificado"
                                 and efectivo[k]["price_kind"] != "verificado"):
            efectivo[k] = dict(f)

    eventos: list[dict] = []
    nuevos: dict[tuple, dict] = {}
    for (src, ext, ci, co), f in efectivo.items():
        if f["total_usd"] > cfg.presupuesto_usd:
            continue
        if f["first_seen"] == run_date:
            k = (src, ext)
            if k not in nuevos or f["total_usd"] < nuevos[k]["total_usd"]:
                nuevos[k] = f
            continue
        prev = store.min_previo(src, ext, ci, co, run_date, f["price_kind"])
        if prev is not None and f["total_usd"] <= prev * (1 - cfg.bajada_pct):
            eventos.append({"kind": "bajada", **f, "prev_usd": prev})
    for f in nuevos.values():
        eventos.append({"kind": "nuevo", **f, "prev_usd": None})

    for e in eventos:
        store.x(
            "INSERT INTO events (run_date, kind, source, ext_id, checkin, checkout, total_usd, "
            "prev_usd, price_kind) VALUES (?,?,?,?,?,?,?,?,?)",
            (run_date, e["kind"], e["source"], e["ext_id"], e["checkin"], e["checkout"],
             e["total_usd"], e["prev_usd"], e["price_kind"]),
        )
    eventos.sort(key=lambda e: e["total_usd"])
    return eventos


def ingest(cfg: Config, store: Store, path: Path) -> Resumen:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DatosInvalidos(f"{path.name}: JSON mal formado ({exc})") from exc
    run_date = _fecha(data.get("run_date"), "run_date")
    resumen = Resumen(run_date)

    # Validar TODO antes de escribir nada: o entra la pasada entera o no entra.
    busquedas = []
    for i, b in enumerate(data.get("busquedas") or []):
        donde = f"busquedas[{i}]"
        src = b.get("source")
        if src not in FUENTES:
            raise DatosInvalidos(f"{donde}: fuente desconocida {src!r}")
        ci, co = _fecha(b.get("checkin"), donde), _fecha(b.get("checkout"), donde)
        if co <= ci:
            raise DatosInvalidos(f"{donde}: la salida no es posterior a la entrada")
        ok = bool(b.get("ok"))
        res = [_validar_resultado(r, src, ci, co, f"{donde}.resultados[{j}]")
               for j, r in enumerate(b.get("resultados") or [])] if ok else []
        busquedas.append((src, ci, co, ok, b.get("error"), res))
    if not busquedas:
        raise DatosInvalidos("el fichero no trae ninguna búsqueda")

    # Re-ingerir el mismo día sustituye la pasada, no la duplica.
    for t in ("observations", "searches", "events"):
        store.x(f"DELETE FROM {t} WHERE run_date=?", (run_date,))

    for src, ci, co, ok, err, res in busquedas:
        resumen.busquedas += 1
        resumen.fallidas += 0 if ok else 1
        store.x(
            "INSERT INTO searches (run_date, source, checkin, checkout, ok, error, n_results) "
            "VALUES (?,?,?,?,?,?,?)", (run_date, src, ci, co, int(ok), err, len(res)),
        )
        noches = (date.fromisoformat(co) - date.fromisoformat(ci)).days
        vistos: set[str] = set()
        for r in res:
            if r["id"] in vistos:
                continue
            vistos.add(r["id"])
            _upsert_listing(store, cfg, src, r, run_date)
            store.x(
                "INSERT INTO observations (run_date, source, ext_id, checkin, checkout, nights, "
                "total_usd, price_kind, url) VALUES (?,?,?,?,?,?,?,?,?)",
                (run_date, src, r["id"], ci, co, noches, r["total"], "busqueda", r["url"]),
            )
            resumen.resultados += 1

    for v in data.get("verificaciones") or []:
        _aplicar_verificacion(store, cfg, v, run_date, resumen)

    resumen.eventos = _detectar_eventos(store, cfg, run_date)
    store.x(
        "INSERT OR REPLACE INTO runs (run_date, ingested_at, input_file) VALUES (?,?,?)",
        (run_date, datetime.now(timezone.utc).isoformat(timespec="seconds"), path.name),
    )
    return resumen
