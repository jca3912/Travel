"""Generación del informe HTML (autónomo para el disco, o fragmento para Artifact).

Dirección visual: panel de salidas de aeropuerto. Neutros fríos con sesgo
petróleo, códigos IATA y cifras en monoespaciada tabular, franja de severidad
a la izquierda de cada alerta — como la columna de estado de un teleindicador.
"""

from __future__ import annotations

import html
import statistics
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .detect import nombre_cabina
from .store import Store

CSS = """
/* --- tokens: tema claro completo (estado por defecto) ------------------- */
:root {
  --ground:      #eef1f2;
  --surface:     #ffffff;
  --surface-2:   #e4e9eb;
  --border:      #d3dadd;
  --border-firm: #b9c3c7;
  --ink:         #0e1619;
  --ink-soft:    #4d6169;
  --ink-faint:   #7b8d94;
  --accent:      #0d6c73;
  --accent-soft: #d7ebec;

  --crit:        #a32b1c;
  --crit-bg:     #fbeeec;
  --crit-line:   #e0b3ab;
  --warn:        #8a5300;
  --warn-bg:     #fdf3e3;
  --warn-line:   #e6cda2;
  --good:        #21694a;

  --mono: ui-monospace, "Cascadia Mono", "SF Mono", Menlo, Consolas, monospace;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  color-scheme: light;
}

/* --- mismos tokens, valores oscuros. Sólo se redefinen variables -------- */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --ground:      #0b1215;
    --surface:     #121d21;
    --surface-2:   #18262b;
    --border:      #22353b;
    --border-firm: #344d55;
    --ink:         #e3ecee;
    --ink-soft:    #9aaeb5;
    --ink-faint:   #6d838b;
    --accent:      #4bbfc6;
    --accent-soft: #123238;

    --crit:        #ff9583;
    --crit-bg:     #2a1512;
    --crit-line:   #6b2a20;
    --warn:        #f2bd68;
    --warn-bg:     #2a2010;
    --warn-line:   #6a4d1c;
    --good:        #63c599;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --ground:      #0b1215;
  --surface:     #121d21;
  --surface-2:   #18262b;
  --border:      #22353b;
  --border-firm: #344d55;
  --ink:         #e3ecee;
  --ink-soft:    #9aaeb5;
  --ink-faint:   #6d838b;
  --accent:      #4bbfc6;
  --accent-soft: #123238;

  --crit:        #ff9583;
  --crit-bg:     #2a1512;
  --crit-line:   #6b2a20;
  --warn:        #f2bd68;
  --warn-bg:     #2a2010;
  --warn-line:   #6a4d1c;
  --good:        #63c599;
  color-scheme: dark;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  padding: 2.5rem 1.25rem 4rem;
  background: var(--ground);
  color: var(--ink);
  font: 16px/1.55 var(--sans);
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 940px; margin: 0 auto; display: flex;
        flex-direction: column; gap: 2.25rem; }

/* --- cabecera ----------------------------------------------------------- */
.top { display: flex; flex-direction: column; gap: .5rem;
       padding-bottom: 1.5rem; border-bottom: 2px solid var(--border-firm); }
.eyebrow {
  font: 600 .7rem/1 var(--mono); letter-spacing: .22em; text-transform: uppercase;
  color: var(--accent);
}
h1 { font-size: 2rem; margin: 0; letter-spacing: -0.025em; text-wrap: balance;
     font-weight: 650; }
.sub { color: var(--ink-soft); font-size: .875rem; margin: 0; }
.sub code { font: .85em var(--mono); background: var(--surface-2);
            padding: .1rem .35rem; border-radius: 3px; color: var(--ink-soft); }

/* --- contadores --------------------------------------------------------- */
.counters { display: grid; gap: .7rem;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); }
.counter { background: var(--surface); border: 1px solid var(--border);
           border-radius: 4px; padding: .85rem .95rem; }
.counter .n { font: 600 1.75rem/1 var(--mono); letter-spacing: -0.03em;
              font-variant-numeric: tabular-nums; }
.counter .l { font: 600 .68rem/1.3 var(--mono); letter-spacing: .12em;
              text-transform: uppercase; color: var(--ink-faint); margin-top: .4rem; }

/* --- secciones ---------------------------------------------------------- */
section { display: flex; flex-direction: column; gap: .6rem; }
h2 { font: 600 .75rem/1 var(--mono); letter-spacing: .16em; text-transform: uppercase;
     color: var(--ink-faint); margin: 0 0 .2rem; }

/* --- alertas ------------------------------------------------------------ */
.card {
  background: var(--surface); border: 1px solid var(--border);
  border-left: 4px solid var(--border-firm); border-radius: 3px;
  padding: .95rem 1.15rem; display: flex; flex-direction: column; gap: .45rem;
}
.card.error  { background: var(--crit-bg); border-color: var(--crit-line);
               border-left-color: var(--crit); }
.card.chollo { background: var(--warn-bg); border-color: var(--warn-line);
               border-left-color: var(--warn); }
.badge { font: 700 .65rem/1 var(--mono); letter-spacing: .14em; text-transform: uppercase;
         align-self: flex-start; }
.card.error  .badge { color: var(--crit); }
.card.chollo .badge { color: var(--warn); }

.headline { display: flex; flex-wrap: wrap; gap: .5rem 1rem;
            align-items: baseline; justify-content: space-between; }
.dest { font-size: 1.15rem; font-weight: 620; letter-spacing: -0.01em; }
.iata { font: 500 .8rem/1 var(--mono); letter-spacing: .06em; color: var(--ink-faint);
        margin-left: .5rem; }
.price { font: 700 1.5rem/1 var(--mono); letter-spacing: -0.03em; white-space: nowrap;
         font-variant-numeric: tabular-nums; }
.card.error  .price { color: var(--crit); }
.card.chollo .price { color: var(--warn); }

.meta { color: var(--ink-soft); font-size: .84rem; }
.meta .sep { color: var(--ink-faint); margin: 0 .45rem; }
.meta time, .meta .mono { font: .95em var(--mono); font-variant-numeric: tabular-nums; }
.why { font-size: .84rem; color: var(--ink-soft); padding-top: .45rem;
       border-top: 1px dashed var(--border); }
.strike { text-decoration: line-through; }

/* --- tabla de rutas ----------------------------------------------------- */
.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch;
                border: 1px solid var(--border); border-radius: 4px;
                background: var(--surface); }
table { border-collapse: collapse; width: 100%; font-size: .875rem; min-width: 660px; }
th, td { text-align: left; padding: .6rem .8rem; border-bottom: 1px solid var(--border); }
thead th { font: 600 .66rem/1 var(--mono); letter-spacing: .13em; text-transform: uppercase;
           color: var(--ink-faint); background: var(--surface-2); }
tbody tr:last-child td { border-bottom: none; }
td.num { text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; }
.dest-cell { font-weight: 600; }
.dest-cell span { display: block; font: .78rem/1.4 var(--mono); color: var(--ink-faint);
                  letter-spacing: .05em; font-weight: 400; }
.pill { font: 600 .66rem/1 var(--mono); letter-spacing: .08em; text-transform: uppercase;
        padding: .28rem .5rem; border-radius: 3px; white-space: nowrap;
        background: var(--surface-2); color: var(--ink-faint); }
.pill.ready { background: var(--accent-soft); color: var(--accent); }

/* --- tabla mensual ------------------------------------------------------ */
.mes-bloque { display: flex; flex-direction: column; gap: .45rem; }
.mes-bloque + .mes-bloque { margin-top: 1.1rem; }
.mes-bloque h3 { font-size: .95rem; font-weight: 620; margin: 0;
                 letter-spacing: -0.01em; }
.mes-bloque h3 span { font: 400 .78rem/1 var(--mono); color: var(--ink-faint);
                      letter-spacing: .05em; margin-left: .4rem; }
table.mensual { min-width: 460px; }
table.mensual td.mes { font: 500 .82rem/1 var(--mono); color: var(--ink-soft);
                       letter-spacing: .04em; white-space: nowrap; }
table.mensual td.num { font-weight: 600; }
table.mensual td.sin { color: var(--ink-faint); font-weight: 400; }
/* Mediana y tamaño de muestra: presentes, pero sin competir con el mínimo. */
table.mensual .med { display: block; font-weight: 400; font-size: .76rem;
                     color: var(--ink-faint); margin-top: .15rem; }
table.mensual .med .n { letter-spacing: .04em; }
table.mensual td.suelo { color: var(--good); }

/* --- avisos y vacíos ---------------------------------------------------- */
.notice { border: 1px solid var(--border); border-left: 4px solid var(--accent);
          background: var(--surface); border-radius: 3px; padding: .85rem 1.1rem;
          font-size: .86rem; color: var(--ink-soft); }
.empty { border: 1px dashed var(--border-firm); border-radius: 4px; padding: 1.6rem;
         text-align: center; color: var(--ink-faint); font-size: .9rem;
         background: var(--surface); }
.empty code { font: .9em var(--mono); color: var(--ink-soft); }

footer { border-top: 1px solid var(--border); padding-top: 1.25rem;
         color: var(--ink-faint); font-size: .78rem;
         display: flex; flex-direction: column; gap: .4rem; }
footer strong { color: var(--ink-soft); }
footer .mono { font-family: var(--mono); font-variant-numeric: tabular-nums; }
"""

MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)
DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _fecha_larga(dt: datetime) -> str:
    """strftime depende del locale del sistema y en Windows sale en inglés."""
    return (
        f"{DIAS[dt.weekday()]} {dt.day} de {MESES[dt.month - 1]} "
        f"de {dt.year}, {dt:%H:%M}"
    )


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return iso


def _sparkline(prices: list[float], width: int = 130, height: int = 28) -> str:
    """Histórico de la ruta. Relleno tenue + punto en el último valor."""
    if len(prices) < 2:
        return '<span style="color:var(--ink-faint)">—</span>'
    lo, hi = min(prices), max(prices)
    span = hi - lo or 1.0
    step = width / (len(prices) - 1)
    coords = [
        (i * step, height - 4 - ((p - lo) / span) * (height - 8))
        for i, p in enumerate(prices)
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"0,{height} {line} {width:.1f},{height}"
    lx, ly = coords[-1]
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width + 4} {height}" '
        f'role="img" aria-label="evolución del precio">'
        f'<polygon points="{area}" fill="var(--accent)" opacity=".10"/>'
        f'<polyline points="{line}" fill="none" stroke="var(--accent)" stroke-width="1.4" '
        f'stroke-linejoin="round" stroke-linecap="round" opacity=".75"/>'
        f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.4" fill="var(--accent)"/>'
        f"</svg>"
    )


def _mes_corto(iso_month: str) -> str:
    """'2027-01' → 'ene 2027'."""
    try:
        anio, mes = iso_month.split("-")
        return f"{MESES[int(mes) - 1][:3]} {anio}"
    except (ValueError, IndexError):
        return iso_month


def _tabla_mensual(store: Store, cfg: Config) -> str:
    """Precio por mes de salida. Una tabla por origen, una columna por cabina.

    Se marca el mínimo de cada columna para que se vea de un vistazo cuándo sale
    barato volar, que es para lo que uno mira esta tabla.
    """
    filas = [dict(r) for r in store.monthly_summary()]
    if not filas:
        return ""

    medianas = store.monthly_medians()
    meses = sorted({f["month"] for f in filas})
    etiquetas = {f["origin"]: (f["label"], f["destination"]) for f in filas}
    indice = {(f["month"], f["origin"], f["travel_class"]): f for f in filas}

    bloques = []
    for origin in sorted(etiquetas):
        # El mínimo de cada cabina en toda la tabla, para resaltarlo.
        suelos = {
            cabin: min(
                (
                    indice[(m, origin, cabin)]["min_price"]
                    for m in meses
                    if (m, origin, cabin) in indice
                ),
                default=None,
            )
            for cabin in cfg.cabins
        }

        cuerpo = []
        for m in meses:
            celdas = []
            for cabin in cfg.cabins:
                f = indice.get((m, origin, cabin))
                if f is None:
                    celdas.append('<td class="num sin">—</td>')
                    continue
                mediana = medianas.get((m, origin, cabin), f["min_price"])
                suelo = suelos[cabin] is not None and f["min_price"] <= suelos[cabin]
                # La n va visible a propósito: un mes con n=1 es una anécdota, no
                # un precio de referencia, y sin el dato no hay forma de saberlo.
                celdas.append(
                    f'<td class="num{" suelo" if suelo else ""}">'
                    f'{f["min_price"]:.0f}'
                    f'<span class="med">{mediana:.0f}'
                    f'<span class="n"> · n={f["n"]}</span></span></td>'
                )
            cuerpo.append(
                f'<tr><td class="mes">{_esc(_mes_corto(m))}</td>{"".join(celdas)}</tr>'
            )

        cabeceras = "".join(
            f'<th class="num">{_esc(nombre_cabina(c))}</th>' for c in cfg.cabins
        )
        bloques.append(
            f"""<div class="mes-bloque">
              <h3>{_esc(etiquetas[origin][0])} <span>{_esc(origin)} →
                {_esc(etiquetas[origin][1])}</span></h3>
              <div class="table-scroll"><table class="mensual">
                <thead><tr><th>Mes de salida</th>{cabeceras}</tr></thead>
                <tbody>{"".join(cuerpo)}</tbody>
              </table></div>
            </div>"""
        )

    return "".join(bloques)


def _escalas(stops: int | None, route_path: str | None) -> str:
    """Escalas del trayecto de ida, con los aeropuertos por los que pasa."""
    texto = (
        "sin escalas"
        if not stops
        else f"{stops} escala" + ("s" if stops > 1 else "")
    )
    if route_path and "-" in route_path:
        vias = route_path.split("-")[1:-1]
        if vias:
            texto += " vía " + ", ".join(_esc(v) for v in vias)
    return texto


def _alert_card(row: dict, currency_default: str) -> str:
    level = row["level"]
    cls = "error" if level == "error" else "chollo"
    badge = "Posible error de tarifa" if level == "error" else "Chollo"
    cur = row["currency"] or currency_default

    escalas = _escalas(row["stops"], row["route_path"])
    vuelta = (
        f'<span class="sep">·</span>vuelta <time>{_fmt_date(row["return_date"])}</time>'
        if row["return_date"]
        else '<span class="sep">·</span>sólo ida'
    )

    # El motivo ya incluye la caída porcentual, así que aquí sólo el precio normal
    baseline_html = (
        f'<span class="strike">{row["baseline"]:.0f} {_esc(cur)}</span> habitual · '
        if row["baseline"]
        else ""
    )

    return f"""
    <article class="card {cls}">
      <span class="badge">{badge}</span>
      <div class="headline">
        <span class="dest">{_esc(row["label"])}<span class="iata">{_esc(row["origin"])} →
          {_esc(row["destination"])}</span></span>
        <span class="price">{row["price"]:.0f} {_esc(cur)}</span>
      </div>
      <p class="meta">salida <time>{_fmt_date(row["departure_date"])}</time>{vuelta}
        <span class="sep">·</span><span class="mono">{_esc(row["carrier"] or "??")}</span>
        <span class="sep">·</span>{escalas}
        <span class="sep">·</span>{_esc(nombre_cabina(row["travel_class"]))}</p>
      <p class="why">{baseline_html}{_esc(row["reason"])}</p>
    </article>"""


def build(cfg: Config, store: Store, *, hours: int = 26, standalone: bool = True) -> str:
    now = datetime.now(timezone.utc).astimezone()
    alerts = [dict(r) for r in store.alerts_since(hours)]
    errors = [a for a in alerts if a["level"] == "error"]
    deals = [a for a in alerts if a["level"] != "error"]

    # Si nunca se ha lanzado un `scan` real, lo que hay dentro es histórico
    # sintético de calibración. Hay que decirlo bien claro en el informe.
    demo = store.run_count() == 0 and store.observation_count() > 0
    demo_banner = (
        '<div class="notice"><strong>Datos sintéticos de calibración.</strong> '
        "Todavía no se ha ejecutado ninguna consulta real a la API, así que ningún "
        "precio de este informe corresponde a un vuelo que exista.</div>"
        if demo
        else ""
    )

    # ---------------------------------------------------------- alertas
    if alerts:
        alerts_html = "\n".join(_alert_card(a, cfg.currency) for a in errors + deals)
    else:
        alerts_html = (
            f'<div class="empty">Sin anomalías en las últimas {hours} horas. '
            "Todos los precios observados están dentro de su rango normal.</div>"
        )

    # ------------------------------------------------------------ rutas
    rows: list[str] = []
    for summary in store.route_summary(cfg.history_window_days):
        history = store.history_prices(
            summary["origin"],
            summary["destination"],
            summary["travel_class"],
            window_days=cfg.history_window_days,
        )
        recent = store.latest_prices_for_route(
            summary["origin"], summary["destination"], summary["travel_class"], limit=40
        )
        series = [r["price"] for r in reversed(recent)]
        median = statistics.median(history) if history else 0.0
        last = series[-1] if series else 0.0
        cur = summary["currency"] or cfg.currency
        n = summary["n"]
        faltan = cfg.min_observations - n
        pill = (
            '<span class="pill ready">calibrada</span>'
            if faltan <= 0
            else f'<span class="pill">faltan {faltan}</span>'
        )
        rows.append(
            f"""<tr>
              <td class="dest-cell">{_esc(summary["label"])}
                  <span>{_esc(summary["origin"])} → {_esc(summary["destination"])}
                  · {_esc(nombre_cabina(summary["travel_class"]))}</span></td>
              <td class="num">{n}</td>
              <td class="num">{summary["min_price"]:.0f}</td>
              <td class="num">{median:.0f}</td>
              <td class="num">{last:.0f}</td>
              <td>{_sparkline(series)}</td>
              <td>{pill}</td>
            </tr>"""
        )

    if rows:
        routes_html = f"""<div class="table-scroll"><table>
          <thead><tr>
            <th>Origen y cabina</th><th class="num">Obs.</th>
            <th class="num">Mín. {_esc(cfg.currency)}</th>
            <th class="num">Mediana</th><th class="num">Último</th>
            <th>Histórico</th><th>Línea base</th>
          </tr></thead>
          <tbody>{"".join(rows)}</tbody></table></div>"""
    else:
        routes_html = (
            '<div class="empty">Todavía no hay observaciones. Ejecuta '
            "<code>python run.py scan</code> para la primera pasada.</div>"
        )

    # -------------------------------------------------------- por meses
    mensual_html = _tabla_mensual(store, cfg)
    mensual_section = (
        f"""
  <section>
    <h2>Precio por mes de salida</h2>
    <p class="sub">Mínimo en grande; debajo, mediana y número de precios
      observados. En <span style="color:var(--good)">verde</span>, el mes más
      barato de cada cabina.</p>
    {mensual_html}
  </section>"""
        if mensual_html
        else ""
    )

    # ------------------------------------------------------------ pie
    last_run = (store.last_runs(1) or [None])[0]
    run_line = "Sin ejecuciones registradas."
    if last_run is not None:
        run_line = (
            f"Última pasada <span class='mono'>{_fmt_date(last_run['started_at'])} "
            f"{last_run['started_at'][11:16]} UTC</span> · "
            f"{last_run['api_calls']} llamadas · {last_run['offers']} precios · "
            f"estado {last_run['status']}"
        )
    monthly = store.calls_this_month()

    body = f"""
<div class="wrap">
  <header class="top">
    <span class="eyebrow">Vigilancia de tarifas</span>
    <h1>Informe diario</h1>
    <p class="sub">{_fecha_larga(now)} · fuente <code>{_esc(cfg.provider)}</code>
      · {len(cfg.routes)} rutas ·
      {_esc(", ".join(nombre_cabina(c) for c in cfg.cabins))}</p>
  </header>

  {demo_banner}

  <div class="counters">
    <div class="counter"><div class="n" style="color:var(--crit)">{len(errors)}</div>
      <div class="l">Posibles errores</div></div>
    <div class="counter"><div class="n" style="color:var(--warn)">{len(deals)}</div>
      <div class="l">Chollos</div></div>
    <div class="counter"><div class="n">{store.observation_count()}</div>
      <div class="l">Precios guardados</div></div>
    <div class="counter"><div class="n">{monthly}</div>
      <div class="l">Llamadas este mes</div></div>
  </div>

  <section>
    <h2>Alertas · últimas {hours} h</h2>
    {alerts_html}
  </section>

  <section>
    <h2>Estado de cada ruta</h2>
    {routes_html}
  </section>
{mensual_section}

  <footer>
    <p>{run_line}</p>
    <p>Búsquedas este mes: <strong class="mono">{monthly}</strong>.</p>
    <p>Las escalas y la ruta indicadas son las del trayecto de <strong>ida</strong>.
       La vuelta va acotada por el mismo límite, porque el filtro se aplica en la
       propia consulta a los dos trayectos.</p>
    <p>Una alerta no es una reserva confirmada. Las tarifas erróneas se corrigen en
       minutos y la aerolínea puede cancelar el billete después de emitirlo. Verifica
       el precio en la web de la compañía antes de comprar.</p>
  </footer>
</div>"""

    head = f"<title>Informe de tarifas — {now:%d/%m/%Y}</title>\n<style>{CSS}</style>"

    if not standalone:
        # Artifact envuelve el fichero en su propio doctype/head/body
        return head + body

    return (
        '<!doctype html>\n<html lang="es">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"{head}\n</head>\n<body>{body}\n</body>\n</html>\n"
    )


def write(
    cfg: Config, store: Store, path: Path, *, standalone: bool = True, hours: int = 26
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build(cfg, store, hours=hours, standalone=standalone), encoding="utf-8")
    return path
