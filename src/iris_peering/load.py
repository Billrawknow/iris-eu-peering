"""PostGIS loader for the iris_staging schema."""

from __future__ import annotations

import json
from dataclasses import asdict
from importlib import resources
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from . import __version__
from .config import StorageConfig
from .logs import get_logger
from .transform import Node, Reject

log = get_logger("loader")

NODE_COLUMNS = [
    "node_id", "node_type", "ix_id", "ix_name", "ix_name_long", "ix_aka", "ix_city", "ix_country",
    "ix_region_continent", "ix_website", "ix_net_count", "ix_fac_count", "ix_created_at",
    "ix_updated_at", "ixfac_id", "ixfac_updated_at", "fac_id", "fac_name", "fac_org_name",
    "fac_address1", "fac_address2", "fac_city", "fac_state", "fac_zipcode", "fac_country",
    "fac_clli", "fac_updated_at", "node_city", "node_country", "cross_border", "latitude",
    "longitude", "native_crs", "coord_source", "coord_accuracy", "coord_accuracy_m",
    "source_latitude", "source_longitude", "geocode_query", "geocode_provider", "geocode_osm_ref",
    "geocode_display_name", "geocode_retrieved_at", "geocode_licence", "quality_flags",
    "source_name", "source_url", "source_licence", "source_record_hash", "raw",
]


class StagingGuardError(RuntimeError):
    pass


def _ddl(cfg: StorageConfig) -> str:
    tmpl = resources.files("iris_peering").joinpath("sql/001_staging_schema.sql").read_text(encoding="utf-8")
    return tmpl.format(schema=cfg.schema, table=cfg.table)


class StagingLoader:
    def __init__(self, cfg: StorageConfig) -> None:
        # staging-only schema guard, independent of config validation
        if not cfg.schema.startswith("iris_staging") or cfg.schema.startswith("iris_core"):
            raise StagingGuardError(f"refusing non-staging schema {cfg.schema!r}")
        if not cfg.database_url:
            raise StagingGuardError("DATABASE_URL is not set")
        self.cfg = cfg

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self.cfg.database_url, autocommit=False)

    def ensure_schema(self, conn: psycopg.Connection) -> None:
        """Check before CREATE so a role owning only the staging schema needs no database CREATE rights."""
        has_postgis = conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'postgis'").fetchone()
        if not has_postgis:
            conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        has_schema = conn.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname = %s", (self.cfg.schema,)
        ).fetchone()
        if not has_schema:
            conn.execute(f"CREATE SCHEMA {self.cfg.schema}")
        conn.execute(_ddl(self.cfg))

    def start_run(self, conn: psycopg.Connection, run_id: str, started_at: str, scope: dict) -> None:
        conn.execute(
            f"INSERT INTO {self.cfg.schema}.peeringdb_ingest_runs "
            "(run_id, started_at, status, scope, tool_version) VALUES (%s, %s, 'running', %s, %s)",
            (run_id, started_at, Jsonb(scope), __version__),
        )

    def load(
        self,
        conn: psycopg.Connection,
        run_id: str,
        nodes: list[Node],
        rejects: list[Reject],
        scope_countries: list[str],
        scope_region: str | None,
        fetch_complete: bool,
    ) -> dict[str, int]:
        t = self.cfg.qualified_table
        # changed = source payload or derived location differs (e.g. after a geocoding fix)
        existing = {
            r[0]: (r[1], r[2], (r[3], r[4], r[5], r[6], list(r[7] or [])))
            for r in conn.execute(
                f"SELECT node_id, source_record_hash, is_active, latitude, longitude, "
                f"coord_source, coord_accuracy, quality_flags FROM {t}"
            ).fetchall()
        }
        new = changed = unchanged = reactivated = 0
        for node in nodes:
            prev = existing.get(node.node_id)
            derived = (node.latitude, node.longitude, node.coord_source, node.coord_accuracy, node.quality_flags)
            if prev is None:
                new += 1
            elif prev[0] != node.source_record_hash or prev[2] != derived:
                changed += 1
            else:
                unchanged += 1
            if prev is not None and not prev[1]:
                reactivated += 1

        cols = NODE_COLUMNS + ["first_seen_run_id", "last_seen_run_id"]
        placeholders = ", ".join(["%s"] * len(cols))
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in NODE_COLUMNS if c != "node_id")
        sql = (
            f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT (node_id) DO UPDATE SET {updates}, "
            "last_seen_run_id = EXCLUDED.last_seen_run_id, last_seen_at = now(), is_active = true, "
            f"last_changed_at = CASE WHEN ({t}.source_record_hash, {t}.latitude, {t}.longitude, "
            f"{t}.coord_source, {t}.coord_accuracy, {t}.quality_flags) IS DISTINCT FROM "
            "(EXCLUDED.source_record_hash, EXCLUDED.latitude, EXCLUDED.longitude, "
            "EXCLUDED.coord_source, EXCLUDED.coord_accuracy, EXCLUDED.quality_flags) "
            f"THEN now() ELSE {t}.last_changed_at END"
        )
        with conn.cursor() as cur:
            cur.executemany(sql, [self._row(n) + [run_id, run_id] for n in nodes])
            if rejects:
                cur.executemany(
                    f"INSERT INTO {self.cfg.schema}.peeringdb_rejects (run_id, entity, source_id, reason, raw) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    [
                        (run_id, r.entity, None if r.source_id is None else str(r.source_id), r.reason, Jsonb(_jsonable(r.raw)))
                        for r in rejects
                    ],
                )

        deactivated = 0
        # deactivation is skipped when the fetch was incomplete, to avoid false removals
        if fetch_complete:
            seen = [n.node_id for n in nodes]
            if scope_region:
                where, params = "ix_region_continent = %s", [scope_region]
            else:
                where, params = "ix_country = ANY(%s)", [scope_countries]
            cur = conn.execute(
                f"UPDATE {t} SET is_active = false, last_changed_at = now() "
                f"WHERE is_active AND {where} AND NOT (node_id = ANY(%s))",
                params + [seen],
            )
            deactivated = cur.rowcount
        else:
            log.warning("fetch incomplete; skipping deactivation of missing nodes")

        stats = {
            "inserted": new,
            "updated_changed": changed,
            "unchanged": unchanged,
            "reactivated": reactivated,
            "deactivated": deactivated,
            "rejects_stored": len(rejects),
        }
        log.info("staging load complete", extra={"run_id": run_id, "count": stats})
        return stats

    def finish_run(
        self, conn: psycopg.Connection, run_id: str, status: str, snapshot_path: str | None,
        snapshot_digest: str | None, geocode_stats: dict, counts: dict,
    ) -> None:
        conn.execute(
            f"UPDATE {self.cfg.schema}.peeringdb_ingest_runs SET finished_at = now(), status = %s, "
            "snapshot_path = %s, snapshot_digest = %s, geocode_stats = %s, counts = %s WHERE run_id = %s",
            (status, snapshot_path, snapshot_digest, Jsonb(geocode_stats), Jsonb(_jsonable(counts)), run_id),
        )

    @staticmethod
    def _row(node: Node) -> list[Any]:
        d = asdict(node)
        row = []
        for c in NODE_COLUMNS:
            v = d[c]
            if c == "raw":
                v = Jsonb(v)
            row.append(v)
        return row


def _jsonable(v: Any) -> Any:
    return json.loads(json.dumps(v, default=str))
