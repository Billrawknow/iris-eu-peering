-- Validation queries for iris_staging.peeringdb_ix_nodes.
-- Run:  psql "$DATABASE_URL" -f sql/validation_queries.sql
-- Each query states what a healthy result looks like.

\echo '1. Row/IX counts and location coverage (expect located/total close to 1, geom = located)'
SELECT count(*)                                    AS nodes,
       count(DISTINCT ix_id)                       AS ixps,
       count(geom)                                 AS located,
       round(100.0 * count(geom) / NULLIF(count(*), 0), 1) AS located_pct
FROM iris_staging.peeringdb_ix_nodes
WHERE is_active;

\echo '2. Provenance mix (where each coordinate came from, and how accurate it is)'
SELECT coord_source, coord_accuracy, count(*) AS nodes, min(coord_accuracy_m) AS min_m, max(coord_accuracy_m) AS max_m
FROM iris_staging.peeringdb_ix_nodes
WHERE is_active
GROUP BY 1, 2
ORDER BY 3 DESC;

\echo '3. Not collapsed to one coordinate (expect distinct_points close to located, top_share small)'
WITH pts AS (
    SELECT latitude, longitude, count(*) AS n
    FROM iris_staging.peeringdb_ix_nodes
    WHERE is_active AND geom IS NOT NULL
    GROUP BY 1, 2
)
SELECT count(*) AS distinct_points, sum(n) AS located, max(n) AS top_point_nodes,
       round(max(n)::numeric / NULLIF(sum(n), 0), 3) AS top_share
FROM pts;

\echo '4. CRS and geometry integrity (expect one row: srid 4326, all valid, no lat/lon mismatch)'
SELECT ST_SRID(geom) AS srid,
       bool_and(ST_IsValid(geom)) AS all_valid,
       sum(CASE WHEN abs(ST_Y(geom) - latitude) > 1e-9 OR abs(ST_X(geom) - longitude) > 1e-9 THEN 1 ELSE 0 END) AS mismatches
FROM iris_staging.peeringdb_ix_nodes
WHERE geom IS NOT NULL
GROUP BY 1;

\echo '5. Duplicate identity check (expect 0 rows)'
SELECT ix_id, fac_id, count(*)
FROM iris_staging.peeringdb_ix_nodes
WHERE is_active AND fac_id IS NOT NULL
GROUP BY 1, 2
HAVING count(*) > 1;

\echo '6. Per-country coverage, incl. cross-border facilities'
SELECT node_country, count(*) AS nodes, count(geom) AS located, sum(cross_border::int) AS cross_border
FROM iris_staging.peeringdb_ix_nodes
WHERE is_active
GROUP BY 1
ORDER BY 2 DESC;

\echo '7. Unlocated or downgraded nodes to review'
SELECT node_id, ix_name, node_city, node_country, coord_source, quality_flags
FROM iris_staging.peeringdb_ix_nodes
WHERE is_active AND (geom IS NULL OR 'source_coordinates_rejected' = ANY (quality_flags))
ORDER BY node_id;

\echo '8. Rejected source records of the latest run'
SELECT entity, reason, count(*)
FROM iris_staging.peeringdb_rejects
WHERE run_id = (SELECT run_id FROM iris_staging.peeringdb_ingest_runs ORDER BY started_at DESC LIMIT 1)
GROUP BY 1, 2
ORDER BY 1, 2;

\echo '9. Run history (inputs digest identifies the exact raw snapshot used)'
SELECT run_id, status, started_at, finished_at, scope ->> 'countries' AS countries,
       left(snapshot_digest, 16) AS snapshot_digest, counts ->> 'nodes' AS nodes
FROM iris_staging.peeringdb_ingest_runs
ORDER BY started_at DESC
LIMIT 5;

\echo '10. Example screening use: IX nodes within 50 km of a candidate site (Frankfurt city centre)'
SELECT node_id, ix_name, fac_name, coord_accuracy,
       round((ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(8.6821, 50.1109), 4326)::geography) / 1000)::numeric, 1) AS km
FROM iris_staging.peeringdb_ix_nodes
WHERE is_active AND geom IS NOT NULL
  AND ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(8.6821, 50.1109), 4326)::geography, 50000)
ORDER BY km;
