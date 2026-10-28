"""Command line entry point: ``iris-peering {run,fetch}``."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from .client import PeeringDBClient
from .config import ConfigError, load_config, validate
from .logs import setup_logging


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="iris-peering", description=__doc__)
    p.add_argument("--config", default=None, help="YAML config (default: built-in defaults)")
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--log-format", choices=["json", "text"], default="json")
    sub = p.add_subparsers(dest="cmd", required=True)

    def scope_args(sp):
        sp.add_argument("--countries", help="comma-separated ISO alpha-2 codes, e.g. DE,NL,FR")
        sp.add_argument("--region", help="PeeringDB region_continent, e.g. Europe (overrides --countries)")

    r = sub.add_parser("run", help="fetch/replay, transform, geocode, load to staging, report")
    scope_args(r)
    r.add_argument("--snapshot", type=Path, help="replay a saved snapshot directory instead of fetching")
    r.add_argument("--offline", action="store_true", help="no network at all (needs --snapshot; geocode cache only)")
    r.add_argument("--no-db", action="store_true", help="skip the database load (report + exports only)")
    r.add_argument("--strict", action="store_true", help="exit 2 if any acceptance gate fails")
    r.add_argument("--run-id", help="explicit run id (uuid); default random")

    f = sub.add_parser("fetch", help="only fetch and save a raw snapshot")
    scope_args(f)
    f.add_argument("--out", type=Path, required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    setup_logging(args.log_level, args.log_format)
    try:
        cfg = load_config(args.config)
        src = cfg.source
        if args.countries:
            src = replace(src, countries=tuple(c.strip().upper() for c in args.countries.split(",") if c.strip()), region_continent=None)
        if args.region:
            src = replace(src, region_continent=args.region)
        cfg = validate(replace(cfg, source=src))
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    if args.cmd == "fetch":
        snap = PeeringDBClient(cfg.source).fetch_scope()
        snap.write(args.out)
        print(json.dumps({"snapshot": str(args.out), "digest": snap.digest()}))
        return 0

    from .pipeline import run  # imported late so `fetch` works without psycopg configured

    outcome = run(
        cfg,
        snapshot_dir=args.snapshot,
        offline=args.offline,
        load_db=not args.no_db,
        run_id=args.run_id,
    )
    rep = outcome.report
    print(
        json.dumps(
            {
                "run_id": outcome.run_id,
                "report_dir": str(outcome.report_dir),
                "coverage": rep["coverage"],
                "by_coord_source": rep["by_coord_source"],
                "rejects": len(rep["rejects"]),
                "all_gates_pass": rep["all_gates_pass"],
            },
            indent=1,
        )
    )
    if args.strict and not rep["all_gates_pass"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
