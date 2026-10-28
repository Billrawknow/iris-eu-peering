"""PostGIS integration tests; skipped unless IRIS_TEST_DATABASE_URL is set."""

from __future__ import annotations

import os
import uuid
from dataclasses import replace

import psycopg
import pytest

from iris_peering.pipeline import run

DB = os.environ.get("IRIS_TEST_DATABASE_URL")
pytestmark = [pytest.mark.postgis, pytest.mark.skipif(not DB, reason="IRIS_TEST_DATABASE_URL not set")]


@pytest.fixture
def db_cfg(cfg):
    schema = f"iris_staging_test_{uuid.uuid4().hex[:8]}"
    c = replace(cfg, storage=replace(cfg.storage, database_url=DB, schema=schema))
    yield c
    with psycopg.connect(DB, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def _q(cfg, sql, *params):
    with psycopg.connect(DB) as conn:
        return conn.execute(sql.format(s=cfg.storage.schema, t=cfg.storage.qualified_table), params).fetchall()


def test_load_is_idempotent_and_spatial(db_cfg, snapshot_dir):
    first = run(db_cfg, snapshot_dir=snapshot_dir, offline=True)
    assert first.report["load"]["inserted"] == 11
    second = run(db_cfg, snapshot_dir=snapshot_dir, offline=True)
    assert second.report["load"] == {
        "inserted": 0, "updated_changed": 0, "unchanged": 11, "reactivated": 0,
        "deactivated": 0, "rejects_stored": 10,
    }
    assert _q(db_cfg, "SELECT count(*) FROM {t}") == [(11,)]
    # geom is derived in the DB: x=lon, y=lat
    rows = _q(db_cfg, "SELECT ST_SRID(geom), ST_X(geom), ST_Y(geom) FROM {t} WHERE node_id = 'ixfac:8001'")
    assert rows == [(4326, 8.7356, 50.1188)]
    assert _q(db_cfg, "SELECT count(*) FROM {t} WHERE geom IS NULL") == [(1,)]
    assert _q(db_cfg, "SELECT DISTINCT first_seen_run_id::text, last_seen_run_id::text FROM {t}") == [
        (first.run_id, second.run_id)
    ]
    runs = _q(db_cfg, "SELECT status, snapshot_digest FROM {s}.peeringdb_ingest_runs ORDER BY started_at")
    assert [r[0] for r in runs] == ["succeeded", "succeeded"] and runs[0][1] == runs[1][1]


def test_rejects_persisted_with_raw(db_cfg, snapshot_dir):
    out = run(db_cfg, snapshot_dir=snapshot_dir, offline=True)
    rows = _q(db_cfg, "SELECT entity, reason, raw FROM {s}.peeringdb_rejects WHERE run_id = %s", out.run_id)
    assert len(rows) == 10
    assert any(r[1] == "duplicate_superseded" and r[2]["net_count"] == 40 for r in rows)


def test_vanished_node_deactivated_not_deleted(db_cfg, snapshot_dir, tmp_path):
    run(db_cfg, snapshot_dir=snapshot_dir, offline=True)
    # IX 9003 disappears upstream
    from iris_peering.snapshot import Snapshot

    snap = Snapshot.read(snapshot_dir)
    for resp in snap.responses["ix"]:
        resp["body"]["data"] = [r for r in resp["body"]["data"] if not (isinstance(r, dict) and r.get("id") == 9003)]
    d = snap.write(tmp_path / "snap2")
    out = run(db_cfg, snapshot_dir=d, offline=True)
    assert out.report["load"]["deactivated"] == 1
    assert _q(db_cfg, "SELECT is_active FROM {t} WHERE node_id = 'ix:9003'") == [(False,)]
    out3 = run(db_cfg, snapshot_dir=snapshot_dir, offline=True)
    assert out3.report["load"]["reactivated"] == 1


def test_incomplete_fetch_does_not_deactivate(db_cfg, snapshot_dir, tmp_path):
    run(db_cfg, snapshot_dir=snapshot_dir, offline=True)
    from iris_peering.snapshot import Snapshot

    snap = Snapshot.read(snapshot_dir)
    snap.responses["ixfac"][0]["body"] = {"error": "throttled"}  # lost page
    d = snap.write(tmp_path / "snap_bad")
    out = run(db_cfg, snapshot_dir=d, offline=True)
    assert out.report["load"]["deactivated"] == 0
    assert not out.report["acceptance_gates"]["fetch_complete"]["pass"]


def test_changed_source_updates_last_changed(db_cfg, snapshot_dir, tmp_path):
    run(db_cfg, snapshot_dir=snapshot_dir, offline=True)
    from iris_peering.snapshot import Snapshot

    snap = Snapshot.read(snapshot_dir)
    for f in snap.responses["fac"][0]["body"]["data"]:
        if f["id"] == 7001:
            f["latitude"] = 50.1200
    out = run(db_cfg, snapshot_dir=snap.write(tmp_path / "s3"), offline=True)
    assert out.report["load"]["updated_changed"] == 1
    assert _q(db_cfg, "SELECT latitude FROM {t} WHERE node_id = 'ixfac:8001'") == [(50.12,)]


def test_geocode_only_change_is_counted_as_changed(db_cfg, snapshot_dir, cache_copy):
    run(db_cfg, snapshot_dir=snapshot_dir, offline=True)
    before = _q(db_cfg, "SELECT last_changed_at FROM {t} WHERE node_id = 'ix:9003'")
    # same raw source, different geocode result
    import json

    doc = json.loads(cache_copy.read_text())
    key = next(k for k in doc["entries"] if '"Munich"' in k)
    doc["entries"][key]["lat"] = 48.14
    cache_copy.write_text(json.dumps(doc))
    out = run(db_cfg, snapshot_dir=snapshot_dir, offline=True)
    assert out.report["load"]["updated_changed"] == 1 and out.report["load"]["unchanged"] == 10
    after = _q(db_cfg, "SELECT latitude, last_changed_at FROM {t} WHERE node_id = 'ix:9003'")
    assert after[0][0] == 48.14 and after[0][1] > before[0][0]
