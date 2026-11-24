"""Informe HTML. Reutiliza los tokens y estilos del informe de vuelos."""

from __future__ import annotations

import html
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from errorfare.report import CSS as CSS_BASE

from . import clasifica as cl
from .config import Config
from .plan import enlace_airbnb, enlace_vrbo, ventanas
from .store import Store

CSS_PISOS = """
.pill.confirmado { background: var(--accent-soft); color: var(--good); }
.pill.probable   { background: var(--accent-soft); color: var(--accent); }
.pill.sinver     { background: var(--warn-bg); color: var(--warn); }
.pill.descartado { background: var(--crit-bg); color: var(--crit); }
.pill.src { background: var(--surface-2); color: var(--ink-soft); }
td .small { display: block; font: .74rem/1.4 var(--mono); color: var(--ink-faint);
            letter-spacing: .03em; font-weight: 400; margin-top: .15rem; }
td a, .card a, .links a { color: var(--accent); text-underline-offset: 2px; }
td.num .small { text-align: right; }
.card.nuevo  { border-left-color: var(--accent); }
.card.bajada { border-left-color: var(--good); }
.card.nuevo .badge { color: var(--accent); }
.card.bajada .badge { color: var(--good); }
.card.degradado { background: var(--crit-bg); border-color: var(--crit-line);
                  border-left-color: var(--crit); }
.card.degradado .badge { color: var(--crit); }
table.ventanas { min-width: 520px; }
th.num { text-align: right; }
table.ventanas td.num { font-weight: 600; }
table.ventanas td.vacio { color: var(--ink-faint); font-weight: 400; }
table.ventanas td.fuera { color: var(--ink-faint); font-weight: 400; }
table.ventanas td.top { color: var(--good); }
.links { display: grid; gap: .5rem; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); }
.links div { background: var(--surface); border: 1px solid var(--border); border-radius: 4px;
             padding: .6rem .75rem; font-size: .84rem; display: flex; flex-direction: column;
             gap: .2rem; }
.links .f { font: 500 .78rem/1.3 var(--mono); color: var(--ink-soft); }
details { background: var(--surface); border: 1px solid var(--border); border-radius: 4px;
          padding: .7rem .95rem; font-size: .86rem; color: var(--ink-soft); }
details summary { cursor: pointer; color: var(--ink); font-weight: 600; }
details ul { margin: .6rem 0 0; padding-left: 1.1rem; display: flex; flex-direction: column;
             gap: .25rem; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
"""

_PILL = {cl.CONFIRMADO: ("confirmado", "2+ dorm. confirmado"),
         cl.PROBABLE: ("probable", "2 dorm. probable"),
         cl.SIN_VERIFICAR: ("sinver", "dorm. sin verificar"),
         cl.DESCARTADO: ("descartado", "descartado")}
_FUENTE = {"booking": "Booking", "expedia": "Expedia/Vrbo"}
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def _e(v: object) -> str:
    return html.escape("" if v is None else str(v))


def _f(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {MESES[d.month - 1]}"


def _usd(v: float) -> str:
    return f"{v:,.0f} $".replace(",", ".")


def _zona(row) -> str:
    partes = []
    if row["district"]:
        partes.append(_e(row["district"]))
    if row["km"] is not None:
        partes.append(f"{row['km']:.1f} km de Sol")
    return " · ".join(partes) or "ubicación sin datos"


def _mejores(cfg: Config, store: Store) -> list[dict]:
    """Mejor precio por vivienda, dentro del tope, entre todas sus ventanas."""
    por_vivienda: dict[tuple, dict] = {}
    for f in _ultimos_por_ventana(cfg, store):
        k = (f["source"], f["ext_id"])
        if k not in por_vivienda or f["total_usd"] < por_vivienda[k]["total_usd"]:
            por_vivienda[k] = f
    orden = {cl.CONFIRMADO: 0, cl.PROBABLE: 1, cl.SIN_VERIFICAR: 2}
    return sorted(por_vivienda.values(),
                  key=lambda f: (orden.get(f["dorm_status"], 3), f["total_usd"]))


def _fila(cfg: Config, f: dict) -> str:
    clase, texto = _PILL[f["dorm_status"]]
    noches = f["nights"]
    precio_tipo = ("precio para 3 verificado" if f["price_kind"] == "verificado"
                   else "precio de búsqueda")
    lejos = (f["km"] is not None and f["km"] > cfg.km_ok)
    zona = _zona(f) + (" · revisar transporte" if lejos else "")
    extra = []
    if f.get("cancellation") == "non_refundable":
        extra.append("no reembolsable")
    if f.get("room_name"):
        extra.append(_e(f["room_name"]))
    nota = (f"{f['rating']:.1f}" if f["rating"] is not None else "—")
    opin = f"{f['reviews']} opiniones" if f["reviews"] else "sin opiniones: más riesgo"
    return f"""<tr>
  <td class="dest-cell"><a href="{_e(f['url'])}" target="_blank" rel="noopener">{_e(f['name'])}</a>
    <span>{zona}</span></td>
  <td><span class="pill {clase}" title="{_e(f['dorm_reason'])}">{texto}</span>
    <span class="small">{_e(f['dorm_reason'])}</span></td>
  <td>{_f(f['checkin'])} → {_f(f['checkout'])}<span class="small">{noches} noches</span></td>
  <td class="num">{_usd(f['total_usd'])}<span class="small">{_usd(f['total_usd'] / noches)}/noche</span></td>
  <td><span class="pill src">{_FUENTE[f['source']]}</span>
    <span class="small">{precio_tipo}{(' · ' + ' · '.join(extra)) if extra else ''}</span></td>
  <td class="num">{nota}<span class="small">{opin}</span></td>
</tr>"""


def _tabla_ventanas(cfg: Config, store: Store, mejores_por_ventana: dict) -> str:
    buscadas = store.ultima_busqueda_ok()
    vs = ventanas(cfg)
    entradas = sorted({v.checkin for v in vs})
    cab = "".join(f"<th class='num'>{n} noches</th>" for n in cfg.noches)
    filas = []
    minimo = min((p for p, _ in mejores_por_ventana.values()), default=None)
    for d in entradas:
        celdas = []
        for n in cfg.noches:
            ci, co = d.isoformat(), (d + timedelta(days=n)).isoformat()
            if co > cfg.hasta.isoformat():
                celdas.append("<td class='num fuera'>—</td>")
            elif (ci, co) not in buscadas:
                celdas.append("<td class='num vacio'>pendiente</td>")
            elif (ci, co) in mejores_por_ventana:
                p, cuantos = mejores_por_ventana[(ci, co)]
                top = " top" if p == minimo else ""
                celdas.append(f"<td class='num{top}'>{_usd(p)}"
                              f"<span class='small'>{cuantos} opción(es)</span></td>")
            else:
                celdas.append("<td class='num vacio'>nada ≤ tope</td>")
        filas.append(f"<tr><td class='mes'>{_f(d.isoformat())}</td>{''.join(celdas)}</tr>")
    return (f"<div class='table-scroll'><table class='ventanas mensual'><thead><tr>"
            f"<th>Entrada</th>{cab}</tr></thead><tbody>{''.join(filas)}</tbody></table></div>")


def _eventos_html(store: Store, cfg: Config, hours: int) -> str:
    desde = (datetime.now(timezone.utc) - timedelta(hours=hours)).date().isoformat()
    filas = store.q(
        """
        SELECT e.*, l.name, l.district, l.km, l.dorm_status, l.dorm_reason,
               (SELECT url FROM observations o WHERE o.source=e.source AND o.ext_id=e.ext_id
                  AND o.checkin=e.checkin AND o.checkout=e.checkout AND o.run_date=e.run_date
                  ORDER BY o.price_kind='verificado' DESC LIMIT 1) url
        FROM events e JOIN listings l ON l.source=e.source AND l.ext_id=e.ext_id
        WHERE e.run_date >= ? ORDER BY e.kind='bajada' DESC, e.total_usd
        """,
        (desde,),
    )
    if not filas:
        return ""
    cards = []
    for f in filas[:12]:
        clase, texto = _PILL.get(f["dorm_status"], ("sinver", f["dorm_status"]))
        noches = (date.fromisoformat(f["checkout"]) - date.fromisoformat(f["checkin"])).days
        antes = (f"<span class='strike'>{_usd(f['prev_usd'])}</span> → "
                 if f["prev_usd"] else "")
        cards.append(f"""<div class="card {f['kind']}">
  <span class="badge">{'Bajada de precio' if f['kind'] == 'bajada' else 'Nuevo'}</span>
  <div class="headline"><span class="dest"><a href="{_e(f['url'])}" target="_blank" rel="noopener">{_e(f['name'])}</a></span>
    <span class="price">{antes}{_usd(f['total_usd'])}</span></div>
  <div class="meta">{_f(f['checkin'])} → {_f(f['checkout'])} · {noches} noches<span class="sep">|</span>{_zona(f)}<span class="sep">|</span>{_FUENTE[f['source']]} · {'precio para 3 verificado' if f['price_kind'] == 'verificado' else 'precio de búsqueda'}</div>
  <div class="why"><span class="pill {clase}">{texto}</span> {_e(f['dorm_reason'])}</div>
</div>""")
    return "\n".join(cards)


def build(cfg: Config, store: Store, *, hours: int = 26, standalone: bool = True) -> str:
    now = datetime.now(timezone.utc).astimezone()
    mejores = _mejores(cfg, store)
    ultima = store.ultima_pasada()

    # Estado de la última pasada.
    aviso_estado = ""
    if ultima is None:
        aviso_estado = ('<div class="card degradado"><span class="badge">Sin datos</span>'
                        "<div>Todavía no se ha completado ninguna pasada.</div></div>")
    else:
        s = store.one("SELECT COUNT(*) n, SUM(ok=0) f FROM searches WHERE run_date=?", (ultima,))
        n, fall = s["n"] or 0, s["f"] or 0
        dias = (now.date() - date.fromisoformat(ultima)).days
        if n and fall / n > 0.30:
            aviso_estado = (f'<div class="card degradado"><span class="badge">Pasada degradada</span>'
                            f"<div>Fallaron {fall} de {n} búsquedas el {_f(ultima)}. Un informe "
                            "sin novedades hoy puede deberse a que la fuente falla, no a que "
                            "no haya pisos.</div></div>")
        elif dias >= 2:
            aviso_estado = (f'<div class="card degradado"><span class="badge">Datos atrasados</span>'
                            f"<div>La última pasada es del {_f(ultima)} (hace {dias} días).</div></div>")

    # Mejor precio por ventana para el mapa de fechas.
    por_ventana: dict[tuple, tuple[float, int]] = {}
    tmp: dict[tuple, list[float]] = defaultdict(list)
    for f in _ultimos_por_ventana(cfg, store):
        tmp[(f["checkin"], f["checkout"])].append(f["total_usd"])
    for k, ps in tmp.items():
        por_ventana[k] = (min(ps), len(ps))

    confirmados = sum(1 for m in mejores if m["dorm_status"] == cl.CONFIRMADO)
    probables = sum(1 for m in mejores if m["dorm_status"] == cl.PROBABLE)
    vs = ventanas(cfg)
    cubiertas = sum(1 for v in vs if v.clave in store.ultima_busqueda_ok())
    precio_min = _usd(min(m["total_usd"] for m in mejores)) if mejores else "—"

    filas = "\n".join(_fila(cfg, m) for m in mejores[:25])
    tabla = (f"""<div class="table-scroll"><table><thead><tr>
<th>Vivienda</th><th>Dormitorios</th><th>Mejor ventana</th><th class="num">Total</th>
<th>Fuente</th><th class="num">Nota</th></tr></thead><tbody>{filas}</tbody></table></div>"""
             if mejores else
             '<div class="empty">Todavía nada dentro del tope que cumpla zona y dormitorios.</div>')

    eventos = _eventos_html(store, cfg, hours)
    eventos_sec = (f"<section><h2>Novedades · últimas {hours} h</h2>{eventos}</section>"
                   if eventos else
                   f"<section><h2>Novedades · últimas {hours} h</h2><div class='empty'>"
                   "Nada nuevo ni ninguna bajada desde la pasada anterior.</div></section>")

    # Descartados, para que se vea que el filtro está haciendo su trabajo.
    desc = store.q("SELECT dorm_reason r, COUNT(*) n FROM listings WHERE dorm_status=? "
                   "GROUP BY dorm_reason ORDER BY n DESC", (cl.DESCARTADO,))
    lejos = store.one("SELECT COUNT(*) n FROM listings WHERE dorm_status!=? AND km>?",
                      (cl.DESCARTADO, cfg.km_max))["n"]
    items = [f"<li>{r['n']} · {_e(r['r'])}</li>" for r in desc]
    if lejos:
        items.append(f"<li>{lejos} · a más de {cfg.km_max:g} km de Sol</li>")
    descartes = (f"<details><summary>Descartados automáticamente "
                 f"({sum(r['n'] for r in desc) + lejos})</summary><ul>{''.join(items)}</ul></details>"
                 if items else "")

    # Enlaces manuales a Airbnb / Vrbo para cada ventana.
    enlaces = "".join(
        f"<div><span class='f'>{_f(v.clave[0])} → {_f(v.clave[1])} · {v.noches} n</span>"
        f"<a href='{_e(enlace_airbnb(cfg, v))}' target='_blank' rel='noopener'>Airbnb</a>"
        f"<a href='{_e(enlace_vrbo(cfg, v))}' target='_blank' rel='noopener'>Vrbo</a></div>"
        for v in vs if v.noches in (21, 28)
    )

    edades = " y ".join(str(e) for e in cfg.ninos_edades)
    body = f"""
<div class="wrap">
  <header class="top">
    <span class="eyebrow">Piso en Madrid · verano 2027</span>
    <h1>Apartamento para 3 en Madrid</h1>
    <p class="sub">1 adulto y 2 menores ({edades} años) · {cfg.dormitorios_min} dormitorios ·
      {min(cfg.noches)}–{max(cfg.noches)} noches entre el {_f(cfg.desde.isoformat())} y el
      {_f(cfg.hasta.isoformat())} · tope {_usd(cfg.presupuesto_usd)} todo incluido · hasta
      ~{cfg.km_ok:g} km de Sol (máx. {cfg.km_max:g})</p>
    <p class="sub">Actualizado {now:%d/%m/%Y %H:%M} · última pasada {_f(ultima) if ultima else '—'}</p>
  </header>
  {aviso_estado}
  <div class="counters">
    <div class="counter"><div class="n">{len(mejores)}</div><div class="l">opciones ≤ tope</div></div>
    <div class="counter"><div class="n">{confirmados}</div><div class="l">2 dorm. confirmados</div></div>
    <div class="counter"><div class="n">{probables}</div><div class="l">2 dorm. probables</div></div>
    <div class="counter"><div class="n">{precio_min}</div><div class="l">precio más bajo</div></div>
    <div class="counter"><div class="n">{cubiertas}/{len(vs)}</div><div class="l">ventanas buscadas</div></div>
  </div>
  {eventos_sec}
  <section>
    <h2>Mejores opciones</h2>
    {tabla}
    {descartes}
  </section>
  <section>
    <h2>Precio más bajo por fechas</h2>
    {_tabla_ventanas(cfg, store, por_ventana)}
  </section>
  <section>
    <h2>Airbnb y Vrbo, a mano</h2>
    <div class="notice">Airbnb no tiene acceso automático. Estos enlaces abren la búsqueda
      ya filtrada (piso entero, 2 dormitorios, precio por noche). Airbnb filtra por precio
      por noche sin comisiones: comprueba el total antes de reservar. En Vrbo indica las
      edades de los niños en la web.</div>
    <div class="links">{enlaces}</div>
  </section>
  <footer>
    <p><strong>Antes de reservar.</strong> Los precios vienen de los conectores de Booking
      y Expedia en el momento de la pasada y pueden haber cambiado. «Precio de búsqueda» puede
      ser el de otra unidad más pequeña del mismo alojamiento; «precio para 3 verificado» es
      el de una unidad concreta que admite 3 personas.</p>
    <p>Comprueba en el anuncio el número de dormitorios, que haya aire acondicionado y el
      número de registro de vivienda turística. En Madrid se están retirando los pisos sin
      licencia y una reserva en uno de ellos puede cancelarse.</p>
    <p>La distancia es en línea recta desde la Puerta del Sol, no tiempo de metro.</p>
  </footer>
</div>"""

    head = f"<title>Piso Madrid 2027</title>\n<style>{CSS_BASE}{CSS_PISOS}</style>"
    if not standalone:
        return head + body
    return ('<!doctype html>\n<html lang="es">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"{head}\n</head>\n<body>{body}\n</body>\n</html>\n")


def _ultimos_por_ventana(cfg: Config, store: Store) -> list[dict]:
    """Último precio conocido por (vivienda, ventana) dentro del tope.

    Si hay precio verificado para 3 personas, manda sobre el de búsqueda
    aunque sea más antiguo: el de búsqueda puede ser el de 2 personas.
    """
    filas = store.q(
        """
        SELECT o.*, l.name, l.district, l.km, l.rating, l.reviews, l.dorm_status,
               l.dorm_reason, l.size_sqm, l.license
        FROM observations o
        JOIN listings l ON l.source=o.source AND l.ext_id=o.ext_id
        WHERE l.dorm_status != ? AND (l.km IS NULL OR l.km <= ?)
        ORDER BY o.run_date DESC
        """,
        (cl.DESCARTADO, cfg.km_max),
    )
    ultimo: dict[tuple, dict] = {}
    for f in filas:
        k = (f["source"], f["ext_id"], f["checkin"], f["checkout"])
        if k not in ultimo or (f["price_kind"] == "verificado"
                               and ultimo[k]["price_kind"] != "verificado"):
            ultimo[k] = dict(f)
    return [f for f in ultimo.values() if f["total_usd"] <= cfg.presupuesto_usd]


def write(cfg: Config, store: Store, path: Path, **kw) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build(cfg, store, **kw), encoding="utf-8")
    return path
