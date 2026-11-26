"""python run_pisos.py <plan|ingest|report|resumen>"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import config as config_mod
from . import report as report_mod
from .config import DB_PATH, REPORTS_DIR
from .ingest import DatosInvalidos, cargar_descartes, ingest
from .plan import plan_de_hoy, plan_verificacion
from .store import Store


def _usd(v: float) -> str:
    return f"{v:,.0f} $".replace(",", ".")


def cmd_plan(cfg, args) -> int:
    with Store(DB_PATH) as store:
        plan = plan_de_hoy(cfg, store, date.fromisoformat(args.fecha) if args.fecha else date.today())
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


def cmd_verificar(cfg, args) -> int:
    with Store(DB_PATH) as store:
        print(json.dumps(plan_verificacion(cfg, store), ensure_ascii=False, indent=2))
    return 0


def cmd_descartes(cfg, args) -> int:
    """Sincroniza los descartes del informe (volcado de la colección `descartes`)."""
    path = Path(args.fichero)
    try:
        filas = cargar_descartes(path)
    except DatosInvalidos as exc:
        print(f"RECHAZADO, no se ha guardado nada: {exc}", file=sys.stderr)
        return 2
    with Store(DB_PATH) as store:
        store.sustituir_rechazos(filas)
    print(f"{len(filas)} piso(s) descartado(s) por Julio; quedan fuera de informe, avisos y verificaciones.")
    for f in filas:
        print(f"  {f['source']} {f['ext_id']} · {f.get('nombre') or ''}")
    return 0


def cmd_ingest(cfg, args) -> int:
    path = Path(args.fichero)
    try:
        with Store(DB_PATH) as store:
            r = ingest(cfg, store, path)
    except DatosInvalidos as exc:
        print(f"RECHAZADO, no se ha guardado nada: {exc}", file=sys.stderr)
        return 2
    estado = "DEGRADADO" if r.degradado else "ok"
    print(f"Pasada {r.run_date}: {r.busquedas} búsquedas ({r.fallidas} fallidas, estado {estado}), "
          f"{r.resultados} resultados, {r.verificadas} verificadas, {len(r.eventos)} novedades.")
    for e in r.eventos:
        prev = f" (antes {_usd(e['prev_usd'])})" if e["prev_usd"] else ""
        print(f"  {e['kind'].upper():7} {e['name']} · {e['checkin']}→{e['checkout']} · "
              f"{_usd(e['total_usd'])}{prev} · {e['dorm_status']}")
    for a in r.avisos:
        print(f"  AVISO: {a}")
    return 0


def cmd_report(cfg, args) -> int:
    out = REPORTS_DIR / ("pisos-artifact.html" if args.fragment else "pisos.html")
    with Store(DB_PATH) as store:
        report_mod.write(cfg, store, out, standalone=not args.fragment, hours=args.hours)
    print(out)
    return 0


def cmd_resumen(cfg, args) -> int:
    """Texto del email. Imprime SIN_NOVEDADES si no hay nada que mandar."""
    with Store(DB_PATH) as store:
        run = args.fecha or store.ultima_pasada()
        if run is None:
            print("SIN_NOVEDADES")
            return 0
        evs = store.q(
            "SELECT e.*, l.name, l.dorm_status, l.dorm_reason, l.km FROM events e "
            "JOIN listings l ON l.source=e.source AND l.ext_id=e.ext_id "
            "WHERE e.run_date=? AND NOT EXISTS (SELECT 1 FROM rechazos r "
            "WHERE r.source = e.source AND r.ext_id = e.ext_id) ORDER BY e.total_usd", (run,))
        s = store.one("SELECT COUNT(*) n, SUM(ok=0) f FROM searches WHERE run_date=?", (run,))
    degradado = s["n"] and (s["f"] or 0) / s["n"] > 0.30
    if not evs and not degradado:
        print("SIN_NOVEDADES")
        return 0
    asunto = (f"Piso Madrid: {len(evs)} novedad(es), desde {_usd(evs[0]['total_usd'])}"
              if evs else "Piso Madrid: la búsqueda de hoy ha fallado")
    lineas = [f"ASUNTO: {asunto}", ""]
    if degradado:
        lineas.append(f"ATENCIÓN: fallaron {s['f']} de {s['n']} búsquedas. Puede que falte información.\n")
    for e in evs:
        tipo = "Bajada" if e["kind"] == "bajada" else "Nuevo"
        prev = f" (antes {_usd(e['prev_usd'])})" if e["prev_usd"] else ""
        km = f", {e['km']:.1f} km de Sol" if e["km"] is not None else ""
        lineas.append(f"- {tipo}: {e['name']} — {e['checkin']} a {e['checkout']} — "
                      f"{_usd(e['total_usd'])}{prev}{km}"
                      f"{'' if e['price_kind'] == 'verificado' else ' (precio de búsqueda: sin verificar la unidad)'}"
                      f". Dormitorios: {e['dorm_status']} "
                      f"({e['dorm_reason']}).")
    lineas += ["", f"Informe completo: {cfg.artifact_url or '(enlace pendiente)'}",
               "Verifica precio, dormitorios y licencia en el anuncio antes de reservar."]
    print("\n".join(lineas))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="run_pisos.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("plan", help="ventanas y llamadas de hoy (JSON)")
    s.add_argument("--fecha")
    sub.add_parser("verificar", help="viviendas a verificar tras cargar las búsquedas (JSON)")
    s = sub.add_parser("descartes", help="sincroniza los pisos descartados desde el informe")
    s.add_argument("fichero")
    s = sub.add_parser("ingest", help="carga el JSON de la pasada")
    s.add_argument("fichero")
    s = sub.add_parser("report", help="genera el informe")
    s.add_argument("--fragment", action="store_true")
    s.add_argument("--hours", type=int, default=26)
    s = sub.add_parser("resumen", help="texto del email de novedades")
    s.add_argument("--fecha")
    args = p.parse_args(argv)
    cfg = config_mod.load()
    return {"plan": cmd_plan, "verificar": cmd_verificar, "descartes": cmd_descartes, "ingest": cmd_ingest, "report": cmd_report,
            "resumen": cmd_resumen}[args.cmd](cfg, args)
