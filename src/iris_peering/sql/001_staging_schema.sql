-- Staging schema for IRIS task 149 (IRIS-EU-PEERING).
-- Placeholders {schema} and {table} are substituted by the loader after strict
-- identifier validation; the loader refuses any schema not starting with iris_staging.
-- Idempotent: safe to run on every start.
--
-- Prerequisites (the loader creates them only if missing, which needs extra rights;
-- on shared databases a DBA should run these once and grant nothing else):
--   CREATE EXTENSION IF NOT EXISTS postgis;
--   CREATE SCHEMA IF NOT EXISTS {schema} AUTHORIZATION <staging_writer_role>;

CREATE TABLE IF NOT EXISTS {schema}.peeringdb_ingest_runs (
    run_id              uuid PRIMARY KEY,
    started_at          timestamptz NOT NULL,
    finished_at         timestamptz,
    status              text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    scope               jsonb NOT NULL,          -- countries / region requested
    snapshot_path       text,
    snapshot_digest     text,                    -- sha256 over raw response files
    geocode_stats       jsonb,
    counts              jsonb,
    tool_version        text NOT NULL
);

CREATE TABLE IF NOT EXISTS {schema}.{table} (
    node_id               text PRIMARY KEY,      -- 'ixfac:<id>' or 'ix:<id>' (stable PeeringDB ids)
    node_type             text NOT NULL CHECK (node_type IN ('ix_facility', 'ix_city')),

    ix_id                 integer NOT NULL,
    ix_name               text NOT NULL,
    ix_name_long          text,
    ix_aka                text,
    ix_city               text,
    ix_country            char(2) NOT NULL,
    ix_region_continent   text,
    ix_website            text,
    ix_net_count          integer,
    ix_fac_count          integer,
    ix_created_at         timestamptz,
    ix_updated_at         timestamptz,

    ixfac_id              integer,
    ixfac_updated_at      timestamptz,

    fac_id                integer,
    fac_name              text,
    fac_org_name          text,
    fac_address1          text,
    fac_address2          text,
    fac_city              text,
    fac_state             text,
    fac_zipcode           text,
    fac_country           char(2),
    fac_clli              text,
    fac_updated_at        timestamptz,

    node_city             text,
    node_country          char(2),
    cross_border          boolean NOT NULL DEFAULT false,

    latitude              double precision CHECK (latitude BETWEEN -90 AND 90),
    longitude             double precision CHECK (longitude BETWEEN -180 AND 180),
    geom                  geometry(Point, 4326) GENERATED ALWAYS AS (
                              CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL
                                   THEN ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) END
                          ) STORED,
    native_crs            text NOT NULL DEFAULT 'EPSG:4326',
    coord_source          text NOT NULL CHECK (coord_source IN
                              ('peeringdb_facility', 'geocoded_address', 'geocoded_city', 'none')),
    coord_accuracy        text NOT NULL,
    coord_accuracy_m      integer,
    source_latitude       double precision,      -- as supplied by PeeringDB, even if rejected
    source_longitude      double precision,
    geocode_query         text,
    geocode_provider      text,
    geocode_osm_ref       text,
    geocode_display_name  text,
    geocode_retrieved_at  timestamptz,
    geocode_licence       text,

    quality_flags         text[] NOT NULL DEFAULT '{{}}',
    source_name           text NOT NULL,
    source_url            text,
    source_licence        text NOT NULL,
    source_record_hash    char(64) NOT NULL,
    raw                   jsonb NOT NULL,        -- untouched ix/ixfac/fac objects

    is_active             boolean NOT NULL DEFAULT true,
    first_seen_run_id     uuid NOT NULL REFERENCES {schema}.peeringdb_ingest_runs(run_id),
    last_seen_run_id      uuid NOT NULL REFERENCES {schema}.peeringdb_ingest_runs(run_id),
    first_seen_at         timestamptz NOT NULL DEFAULT now(),
    last_seen_at          timestamptz NOT NULL DEFAULT now(),
    last_changed_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT {table}_coords_pair CHECK ((latitude IS NULL) = (longitude IS NULL)),
    CONSTRAINT {table}_located_has_source CHECK ((latitude IS NULL) = (coord_source = 'none'))
);

CREATE INDEX IF NOT EXISTS {table}_geom_gix ON {schema}.{table} USING gist (geom);
CREATE INDEX IF NOT EXISTS {table}_ix_id_idx ON {schema}.{table} (ix_id);
CREATE INDEX IF NOT EXISTS {table}_country_idx ON {schema}.{table} (node_country);
CREATE INDEX IF NOT EXISTS {table}_coord_source_idx ON {schema}.{table} (coord_source);

CREATE TABLE IF NOT EXISTS {schema}.peeringdb_rejects (
    id          bigserial PRIMARY KEY,
    run_id      uuid NOT NULL REFERENCES {schema}.peeringdb_ingest_runs(run_id),
    entity      text NOT NULL,                   -- ix | ixfac | fac
    source_id   text,
    reason      text NOT NULL,
    raw         jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS peeringdb_rejects_run_idx ON {schema}.peeringdb_rejects (run_id);
