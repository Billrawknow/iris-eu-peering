"""Run orchestration: fetch or replay, transform, load, report."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .client import PeeringDBClient
from .config import Config
from .geocode import Geocoder
from .load import StagingLoader
from .logs import get_logger
from .report import build_report, export_results, write_report
from .snapshot import Snapshot
from .transform import TransformResult, build_nodes

log = get_logger("pipeline")


@dataclass
class RunOutcome:
    run_id: str
    result: TransformResult
    report: dict[str, Any]
    report_dir: Path
    snapshot_dir: Path | None


def run(
    cfg: Config,
    *,
    snapshot_dir: Path | None = None,
    offline: bool = False,
    load_db: bool = True,
    client: PeeringDBClient | None = None,
    geocoder: Geocoder | None = None,
    run_id: str | None = None,
) -> RunOutcome:
    run_id = run_id or str(uuid.uuid4())
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log.info("run started", extra={"run_id": run_id})

    if snapshot_dir:
        snap = Snapshot.read(snapshot_dir)
        log.info("replaying snapshot", extra={"run_id": run_id, "url": str(snapshot_dir)})
    else:
        if offline:
            raise RuntimeError("--offline requires --snapshot (no network fetch allowed)")
        client = client or PeeringDBClient(cfg.source)
        snap = client.fetch_scope()
        snap.write(Path(cfg.paths.snapshot_dir) / run_id)
    fetch_complete = not snap.malformed_responses()

    if snap.scope.get("region_continent"):
        scope_countries: tuple[str, ...] = ()
        scope_region = snap.scope["region_continent"]
    else:
        scope_countries = tuple(snap.scope.get("countries") or cfg.source.countries)
        scope_region = None

    geocoder = geocoder or Geocoder(cfg.geocoding, cfg.source.user_agent, offline=offline)
    try:
        result = build_nodes(snap, geocoder, scope_countries)
    finally:
        geocoder.close()

    load_stats = None
    if load_db:
        loader = StagingLoader(cfg.storage)
        with loader.connect() as conn:
            loader.ensure_schema(conn)
            loader.start_run(conn, run_id, started, snap.scope)
            conn.commit()
            try:
                load_stats = loader.load(
                    conn, run_id, result.nodes, result.rejects,
                    list(scope_countries), scope_region, fetch_complete,
                )
                loader.finish_run(
                    conn, run_id, "succeeded", str(snap.path) if snap.path else None,
                    snap.digest(), geocoder.stats, {**result.counts, **load_stats},
                )
                conn.commit()
            except Exception:
                conn.rollback()
                loader.finish_run(
                    conn, run_id, "failed", str(snap.path) if snap.path else None,
                    snap.digest(), geocoder.stats, result.counts,
                )
                conn.commit()
                log.exception("staging load failed; transaction rolled back", extra={"run_id": run_id})
                raise

    report = build_report(
        result,
        run_id=run_id,
        scope=snap.scope,
        snapshot_digest=snap.digest(),
        geocode_stats=geocoder.stats,
        load_stats=load_stats,
        fetch_complete=fetch_complete,
    )
    out = Path(cfg.paths.report_dir) / run_id
    write_report(report, out)
    export_results(result.nodes, out)
    log.info(
        "run finished",
        extra={"run_id": run_id, "status": "gates_pass" if report["all_gates_pass"] else "gates_fail", "url": str(out)},
    )
    return RunOutcome(run_id, result, report, out, snap.path)
