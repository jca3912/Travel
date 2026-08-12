"""Interfaz de línea de comandos."""

from __future__ import annotations

import argparse
import random
import sys
import webbrowser
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import config as config_mod
from . import report as report_mod
from .config import REPORTS_DIR, Config
from .detect import evaluate
from . import providers
from .providers import ProviderError
from .scan import sample_departures, rotation_for, scan, trip_nights_for
from .store import Store


# --------------------------------------------------------------------- scan


def cmd_scan(cfg: Config, args: argparse.Namespace) -> int:
    if args.dry_run:
        today = date.today()
        rotation = rotation_for(today)
        nights = trip_nights_for(cfg, rotation)
        departures = sample_departures(cfg, today, rotation)
        print("Simulacro — no se consulta la fuente de datos.\n")
        print(f"Duración del viaje: {nights} noches" if nights else "Sólo ida")
        print(f"Fechas de salida: {', '.join(d.isoformat() for d in departures)}")
        print(f"Rutas: {', '.join(r.key for r in cfg.routes)}")
        total = len(cfg.routes) * len(departures)
        print(f"\nBúsquedas que gastaría: {total} (presupuesto {cfg.max_api_calls_per_run})")
        print(f"Proyección mensual: {total * 30}")
        if cfg.provider == "serpapi" and total * 30 > 1000:
            print("  ⚠ SerpApi da 1.000 búsquedas/mes en el plan básico. Baja "
                  "samples_per_route o sube de plan.")
        return 0

    with Store(cfg.db_path) as store:
        client = providers.build(cfg, verbose=args.verbose)
        client.check_ready()
        with store.run() as stats:
            scan(cfg, store, client, stats, verbose=True)
        print(
            f"\nHecho: {stats['offers']} precios, {stats['alerts']} alertas nuevas, "
            f"{stats['api_calls']} búsquedas ({store.calls_this_month()} este mes)."
        )
    return 0


# ------------------------------------------------------------------- report


def cmd_report(cfg: Config, args: argparse.Namespace) -> int:
    with Store(cfg.db_path) as store:
        stamp = datetime.now().strftime("%Y-%m-%d")
        standalone = not args.fragment
        name = f"informe-{stamp}.html" if standalone else "artifact.html"
        path = REPORTS_DIR / name
        report_mod.write(cfg, store, path, standalone=standalone, hours=args.hours)

        # Copia estable para abrir siempre el mismo fichero / republicar la misma URL
        latest = REPORTS_DIR / ("ultimo.html" if standalone else "artifact.html")
        if latest != path:
            latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

        n = len(store.alerts_since(args.hours))
        print(f"Informe generado: {path}")
        print(f"Alertas incluidas: {n}")
        if args.open:
            webbrowser.open(path.resolve().as_uri())
    return 0


def cmd_daily(cfg: Config, args: argparse.Namespace) -> int:
    rc = cmd_scan(cfg, args)
    if rc != 0:
        return rc
    print()
    return cmd_report(cfg, args)


# ------------------------------------------------------------------- status


def cmd_status(cfg: Config, args: argparse.Namespace) -> int:
    with Store(cfg.db_path) as store:
        provider = providers.build(cfg)
        try:
            provider.check_ready()
            listo = "lista"
        except SystemExit as exc:
            listo = f"NO LISTA — {str(exc).splitlines()[0]}"

        print(f"Base de datos : {cfg.db_path}")
        print(f"Fuente        : {cfg.provider} — {provider.description}")
        print(f"Estado fuente : {listo}")
        print(f"Rutas         : {len(cfg.routes)}")
        print(f"Observaciones : {store.observation_count()}")
        print(f"Alertas       : {store.alert_count()}")
        print(f"Ejecuciones   : {store.run_count()}")
        print(f"Búsquedas mes : {store.calls_this_month()}\n")

        summaries = store.route_summary(cfg.history_window_days)
        if not summaries:
            print("Sin observaciones todavía.")
            return 0
        print(f"{'Destino':<16}{'Ruta':<10}{'Obs':>5}{'Mín':>9}{'Máx':>9}  Base")
        for s in summaries:
            ready = "ok" if s["n"] >= cfg.min_observations else f"faltan {cfg.min_observations - s['n']}"
            print(
                f"{s['label'][:15]:<16}{s['origin']}-{s['destination']:<6}"
                f"{s['n']:>5}{s['min_price']:>9.0f}{s['max_price']:>9.0f}  {ready}"
            )
    return 0


# ----------------------------------------------------------------- simulate


def cmd_simulate(cfg: Config, args: argparse.Namespace) -> int:
    """Rellena la base con histórico sintético para probar el sistema sin API."""
    rng = random.Random(args.seed)
    today = date.today()
    inserted = 0

    with Store(cfg.db_path) as store:
        for route in cfg.routes:
            base = (route.alert_below or 400) * 1.65
            for day_offset in range(args.days, 0, -1):
                observed = datetime.now(timezone.utc) - timedelta(days=day_offset)
                for _ in range(args.per_day):
                    departure = today + timedelta(
                        days=rng.randint(cfg.min_days_ahead, cfg.max_days_ahead)
                    )
                    # Estacionalidad suave + ruido multiplicativo
                    season = 1 + 0.18 * ((departure.month % 12) / 12 - 0.5) * 2
                    price = round(base * season * rng.lognormvariate(0, 0.11), 2)
                    store.record_observation(
                        origin=route.origin,
                        destination=route.destination,
                        label=route.label,
                        departure_date=departure.isoformat(),
                        return_date=(departure + timedelta(days=10)).isoformat(),
                        trip_nights=10,
                        travel_class=cfg.travel_class,
                        adults=cfg.adults,
                        currency=cfg.currency,
                        price=price,
                        carrier=rng.choice(["IB", "AF", "KL", "LH", "UA", "AA"]),
                        stops=(s := rng.choice([1, 1, 1, 2])),
                        route_path="-".join(
                            [route.origin]
                            + rng.sample(["IAD", "ATL", "DFW", "JFK", "ORD", "MIA"], s)
                            + [route.destination]
                        ),
                        duration=f"{rng.randint(14, 28)}h {rng.randint(0, 55)}m",
                        observed_at=observed.isoformat(timespec="seconds"),
                    )
                    inserted += 1

        print(f"{inserted} observaciones sintéticas insertadas.")

        if args.inject:
            print("\nInyectando gangas para probar la detección:")
            for route in cfg.routes[: args.inject]:
                departure = today + timedelta(days=rng.randint(60, 200))
                history = store.history_prices(
                    route.origin,
                    route.destination,
                    cfg.travel_class,
                    window_days=cfg.history_window_days,
                )
                import statistics

                median = statistics.median(history) if history else 400
                price = round(median * rng.uniform(0.18, 0.32), 2)
                verdict = evaluate(cfg, store, route, price, departure.isoformat())
                obs_id = store.record_observation(
                    origin=route.origin,
                    destination=route.destination,
                    label=route.label,
                    departure_date=departure.isoformat(),
                    return_date=(departure + timedelta(days=10)).isoformat(),
                    trip_nights=10,
                    travel_class=cfg.travel_class,
                    adults=cfg.adults,
                    currency=cfg.currency,
                    price=price,
                    carrier="Prueba",
                    stops=1,
                    route_path=f"{route.origin}-JFK-{route.destination}",
                    duration="19h 40m",
                )
                if verdict.is_alert:
                    store.record_alert(
                        level=verdict.level,
                        origin=route.origin,
                        destination=route.destination,
                        label=route.label,
                        departure_date=departure.isoformat(),
                        return_date=(departure + timedelta(days=10)).isoformat(),
                        travel_class=cfg.travel_class,
                        currency=cfg.currency,
                        price=price,
                        baseline=verdict.baseline,
                        drop_pct=verdict.drop_pct,
                        zscore=verdict.zscore,
                        sample_size=verdict.sample_size,
                        reason=verdict.reason,
                        carrier="Prueba",
                        stops=1,
                        route_path=f"{route.origin}-JFK-{route.destination}",
                        observation_id=obs_id,
                    )
                    tag = "POSIBLE ERROR" if verdict.level == "error" else "CHOLLO"
                    print(f"  [{tag}] {route.label}: {price:.0f} {cfg.currency} — {verdict.reason}")
                else:
                    print(f"  [nada] {route.label}: {price:.0f} — {verdict.reason}")
    return 0


def cmd_reset(cfg: Config, args: argparse.Namespace) -> int:
    if not cfg.db_path.exists():
        print("No hay base de datos que borrar.")
        return 0
    if not args.yes:
        print(f"Esto borra {cfg.db_path} y todo el histórico acumulado.")
        print("Vuelve a lanzarlo con --yes si estás seguro.")
        return 1
    cfg.db_path.unlink()
    print(f"Borrado: {cfg.db_path}")
    return 0


# --------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="errorfare",
        description="Detector de precios anómalos en vuelos con informe diario.",
    )
    p.add_argument("--config", type=Path, default=None, help="ruta a config.toml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="consulta la API y guarda precios")
    s.add_argument("--dry-run", action="store_true", help="no llama a la API, sólo enseña el plan")
    s.set_defaults(func=cmd_scan)

    r = sub.add_parser("report", help="genera el informe HTML")
    r.add_argument("--hours", type=int, default=26, help="ventana de alertas (por defecto 26)")
    r.add_argument("--open", action="store_true", help="ábrelo en el navegador")
    r.add_argument("--fragment", action="store_true", help="formato para publicar como Artifact")
    r.set_defaults(func=cmd_report)

    d = sub.add_parser("daily", help="scan + report en una sola orden")
    d.add_argument("--hours", type=int, default=26)
    d.add_argument("--open", action="store_true")
    d.add_argument("--fragment", action="store_true")
    d.add_argument("--dry-run", action="store_true")
    d.set_defaults(func=cmd_daily)

    st = sub.add_parser("status", help="resumen del estado del sistema")
    st.set_defaults(func=cmd_status)

    sim = sub.add_parser("simulate", help="histórico sintético para probar sin API")
    sim.add_argument("--days", type=int, default=30)
    sim.add_argument("--per-day", type=int, default=3)
    sim.add_argument("--seed", type=int, default=7)
    sim.add_argument("--inject", type=int, default=2, help="nº de rutas con ganga inyectada")
    sim.set_defaults(func=cmd_simulate)

    rs = sub.add_parser("reset", help="borra la base de datos")
    rs.add_argument("--yes", action="store_true")
    rs.set_defaults(func=cmd_reset)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = config_mod.load(args.config)
    try:
        return int(args.func(cfg, args))
    except ProviderError as exc:
        print(f"Error de la fuente de datos: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrumpido.", file=sys.stderr)
        return 130
