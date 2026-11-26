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
.tabs { display: flex; gap: .25rem; border-bottom: 1px solid var(--border-firm); }
.tabs button { font: 600 .74rem/1 var(--mono); letter-spacing: .12em; text-transform: uppercase;
               background: none; border: 0; border-bottom: 2px solid transparent;
               color: var(--ink-faint); padding: .8rem .9rem .7rem; cursor: pointer; margin-bottom: -1px; }
.tabs button[aria-selected="true"] { color: var(--accent); border-bottom-color: var(--accent); }
.tabs .n { font-variant-numeric: tabular-nums; margin-left: .35rem; color: var(--ink-faint); }
.vista { display: flex; flex-direction: column; gap: 2.25rem; }
button.quitar, button.recuperar {
  font: 600 .68rem/1 var(--mono); letter-spacing: .08em; text-transform: uppercase;
  padding: .45rem .6rem; border-radius: 3px; cursor: pointer; white-space: nowrap;
  background: var(--surface); color: var(--ink-soft); border: 1px solid var(--border-firm); }
button.quitar:hover { color: var(--crit); border-color: var(--crit-line); background: var(--crit-bg); }
button.recuperar:hover { color: var(--accent); border-color: var(--accent); background: var(--accent-soft); }
button:disabled { opacity: .5; cursor: progress; }
td.accion { width: 1%; text-align: right; }
.card .acciones { display: flex; justify-content: flex-end; }
.lista-desc { display: flex; flex-direction: column; gap: .5rem; }
.desc-item { background: var(--surface); border: 1px solid var(--border); border-radius: 4px;
             padding: .75rem .95rem; display: flex; gap: .75rem; align-items: center;
             justify-content: space-between; flex-wrap: wrap; }
.desc-item .t { font-weight: 600; min-width: 0; overflow-wrap: anywhere; }
.desc-item .m { font: .76rem/1.4 var(--mono); color: var(--ink-faint); }
.estado-desc { font-size: .84rem; color: var(--ink-soft); }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
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


def _clave(f) -> str:
    """Id del documento de descarte: fuente-id (sólo caracteres válidos en rutas)."""
    return f"{f['source']}-{f['ext_id']}"


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
    return f"""<tr data-key="{_e(_clave(f))}" data-nombre="{_e(f['name'])}">
  <td class="dest-cell"><a href="{_e(f['url'])}" target="_blank" rel="noopener">{_e(f['name'])}</a>
    <span>{zona}</span></td>
  <td><span class="pill {clase}" title="{_e(f['dorm_reason'])}">{texto}</span>
    <span class="small">{_e(f['dorm_reason'])}</span></td>
  <td>{_f(f['checkin'])} → {_f(f['checkout'])}<span class="small">{noches} noches</span></td>
  <td class="num">{_usd(f['total_usd'])}<span class="small">{_usd(f['total_usd'] / noches)}/noche</span></td>
  <td><span class="pill src">{_FUENTE[f['source']]}</span>
    <span class="small">{precio_tipo}{(' · ' + ' · '.join(extra)) if extra else ''}</span></td>
  <td class="num">{nota}<span class="small">{opin}</span></td>
  <td class="accion"><button type="button" class="quitar" hidden>Descartar</button></td>
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
        WHERE e.run_date >= ?
          AND NOT EXISTS (SELECT 1 FROM rechazos r WHERE r.source = e.source AND r.ext_id = e.ext_id)
        ORDER BY e.kind='bajada' DESC, e.total_usd
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
        cards.append(f"""<div class="card {f['kind']}" data-key="{_e(_clave(f))}" data-nombre="{_e(f['name'])}">
  <span class="badge">{'Bajada de precio' if f['kind'] == 'bajada' else 'Nuevo'}</span>
  <div class="headline"><span class="dest"><a href="{_e(f['url'])}" target="_blank" rel="noopener">{_e(f['name'])}</a></span>
    <span class="price">{antes}{_usd(f['total_usd'])}</span></div>
  <div class="meta">{_f(f['checkin'])} → {_f(f['checkout'])} · {noches} noches<span class="sep">|</span>{_zona(f)}<span class="sep">|</span>{_FUENTE[f['source']]} · {'precio para 3 verificado' if f['price_kind'] == 'verificado' else 'precio de búsqueda'}</div>
  <div class="why"><span class="pill {clase}">{texto}</span> {_e(f['dorm_reason'])}</div>
  <div class="acciones"><button type="button" class="quitar" hidden>Descartar</button></div>
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
<th>Fuente</th><th class="num">Nota</th><th><span class="sr">Acción</span></th></tr></thead><tbody>{filas}</tbody></table></div>"""
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
  <nav class="tabs" role="tablist" aria-label="Vistas del informe">
    <button type="button" role="tab" id="tab-opciones" aria-controls="vista-opciones" aria-selected="true">Opciones</button>
    <button type="button" role="tab" id="tab-descartados" aria-controls="vista-descartados" aria-selected="false">Descartados<span class="n" id="n-descartados"></span></button>
  </nav>
  <div class="vista" id="vista-opciones" role="tabpanel" aria-labelledby="tab-opciones">
  <div class="counters">
    <div class="counter"><div class="n" id="n-opciones">{len(mejores)}</div><div class="l">opciones ≤ tope</div></div>
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
  </div>
  <div class="vista" id="vista-descartados" role="tabpanel" aria-labelledby="tab-descartados" hidden>
    <section>
      <h2>Pisos que has descartado</h2>
      <p class="estado-desc" id="estado-desc">Cargando…</p>
      <div class="lista-desc" id="lista-desc"></div>
      <div class="notice">Al descartar un piso desaparece de este informe al momento. En la
        siguiente pasada diaria queda fuera también de las novedades, del email y de las
        verificaciones. Booking y Expedia lo seguirán devolviendo, pero se filtra aquí.
        «Recuperar» lo devuelve a la lista.</div>
    </section>
  </div>
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
</div>
<script>{SCRIPT}</script>"""

    head = f"<title>Piso Madrid 2027</title>\n<style>{CSS_BASE}{CSS_PISOS}</style>"
    if not standalone:
        return head + body
    return ('<!doctype html>\n<html lang="es">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"{head}\n</head>\n<body>{body}\n</body>\n</html>\n")


SCRIPT = r"""
(function () {
  "use strict";
  var COL = "descartes";
  var tabs = { opciones: document.getElementById("tab-opciones"),
               descartados: document.getElementById("tab-descartados") };
  var vistas = { opciones: document.getElementById("vista-opciones"),
                 descartados: document.getElementById("vista-descartados") };
  var lista = document.getElementById("lista-desc");
  var estado = document.getElementById("estado-desc");
  var nDesc = document.getElementById("n-descartados");
  var nOpc = document.getElementById("n-opciones");
  var db = null, puedeEscribir = false, descartados = new Map(), cargado = false;

  function mostrar(nombre) {
    Object.keys(vistas).forEach(function (k) {
      vistas[k].hidden = k !== nombre;
      tabs[k].setAttribute("aria-selected", String(k === nombre));
    });
  }
  function desdeHash() { mostrar(location.hash === "#descartados" ? "descartados" : "opciones"); }
  tabs.opciones.addEventListener("click", function () { history.replaceState(null, "", "#opciones"); mostrar("opciones"); });
  tabs.descartados.addEventListener("click", function () { history.replaceState(null, "", "#descartados"); mostrar("descartados"); });
  window.addEventListener("hashchange", desdeHash);
  desdeHash();

  function aplicar() {
    var visibles = 0;
    document.querySelectorAll("[data-key]").forEach(function (el) {
      var fuera = descartados.has(el.dataset.key);
      el.hidden = fuera;
      if (!fuera && el.tagName === "TR") visibles++;
    });
    nOpc.textContent = String(visibles);
    nDesc.textContent = descartados.size ? String(descartados.size) : "";
    pintarLista();
  }

  function pintarLista() {
    lista.textContent = "";
    if (!db) { estado.textContent = "Los descartes no están disponibles en esta vista (hace falta abrir el informe con tu cuenta)."; return; }
    if (!cargado) return;
    if (!descartados.size) { estado.textContent = "No has descartado ningún piso. Usa «Descartar» en la pestaña Opciones."; return; }
    estado.textContent = "";
    Array.from(descartados.values())
      .sort(function (a, b) { return String(b.at || "").localeCompare(String(a.at || "")); })
      .forEach(function (d) {
        var item = document.createElement("div"); item.className = "desc-item";
        var txt = document.createElement("div");
        var t = document.createElement("div"); t.className = "t"; t.textContent = d.nombre || d.ext_id;
        var m = document.createElement("div"); m.className = "m";
        var fecha = d.at ? new Date(d.at) : null;
        m.textContent = (d.source === "booking" ? "Booking" : "Expedia/Vrbo") +
          (fecha && !isNaN(fecha) ? " · descartado el " + fecha.toLocaleDateString("es-ES") : "");
        txt.appendChild(t); txt.appendChild(m); item.appendChild(txt);
        if (puedeEscribir) {
          var b = document.createElement("button");
          b.type = "button"; b.className = "recuperar"; b.textContent = "Recuperar";
          b.addEventListener("click", function () { recuperar(d, b); });
          item.appendChild(b);
        }
        lista.appendChild(item);
      });
  }

  function partes(key) {
    var i = key.indexOf("-");
    return { source: key.slice(0, i), ext_id: key.slice(i + 1) };
  }

  async function descartar(el, boton) {
    var key = el.dataset.key, p = partes(key);
    boton.disabled = true;
    try {
      await db.collection(COL).doc(key).set({
        source: p.source, ext_id: p.ext_id,
        nombre: el.dataset.nombre || "", at: new Date().toISOString()
      });
    } catch (e) {
      boton.disabled = false;
      boton.textContent = e && e.code === "invalid_argument" ? "Sin permiso" : "Reintentar";
    }
  }
  async function recuperar(d, boton) {
    boton.disabled = true;
    try { await db.collection(COL).doc(d.source + "-" + d.ext_id).delete(); }
    catch (e) { boton.disabled = false; boton.textContent = "Reintentar"; }
  }

  (async function iniciar() {
    if (!window.claude || !window.claude.use) { pintarLista(); return; }
    db = await window.claude.use("db");
    if (!db) { pintarLista(); return; }
    var user = await window.claude.use("user");
    var permitido = user ? await user.canEdit() : null;
    puedeEscribir = permitido !== false;
    if (puedeEscribir) {
      document.querySelectorAll("[data-key]").forEach(function (el) {
        var b = el.querySelector("button.quitar");
        if (!b) return;
        b.hidden = false;
        b.addEventListener("click", function () { descartar(el, b); });
      });
    }
    var timer = setTimeout(function () {
      if (!cargado) estado.textContent = "No se puede leer la lista de descartados todavía.";
    }, 10000);
    db.collection(COL).onSnapshot(function (snap) {
      descartados = new Map();
      snap.docs.forEach(function (doc) {
        var d = doc.data() || {};
        if (typeof d.source === "string" && typeof d.ext_id === "string")
          descartados.set(doc.id, { source: d.source, ext_id: d.ext_id,
                                    nombre: typeof d.nombre === "string" ? d.nombre : "",
                                    at: typeof d.at === "string" ? d.at : "" });
      });
      if (!snap.metadata.fromCache) { cargado = true; clearTimeout(timer); }
      else cargado = true;
      aplicar();
    }, function () {
      clearTimeout(timer); cargado = true;
      estado.textContent = "No se ha podido cargar la lista de descartados.";
    });
  })();
})();
"""


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
          AND NOT EXISTS (SELECT 1 FROM rechazos r WHERE r.source = l.source AND r.ext_id = l.ext_id)
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
