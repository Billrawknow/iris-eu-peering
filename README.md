# IRIS-EU-PEERING — Internet Exchange nodes from PeeringDB

**Task 149 · IRIS-EU-PEERING** · Python 3.11 · PostGIS staging target `iris_staging.peeringdb_ix_nodes`

Ingests public PeeringDB Internet Exchange (IX) records, resolves each IX to the
facilities it operates in, attaches a coordinate with explicit provenance and accuracy,
and loads the result into an isolated staging schema as a connectivity-screening
feature for site selection. Nothing is written to `iris_core`.

```
PeeringDB /api/ix?country=DE ─┐
          /api/ixfac?ix_id__in=…├─► raw snapshot (sha256) ─► normalise + validate ─► locate ─► iris_staging
          /api/fac?id__in=…    ─┘                                │                    │        + rejects
                                                                   └──── rejects ◄─────┘        + run log
                                                       Nominatim (only for gaps, cached) ┘   + quality report
```

## Contents

- [IRIS-EU-PEERING — Internet Exchange nodes from PeeringDB](#iris-eu-peering--internet-exchange-nodes-from-peeringdb)
  - [Contents](#contents)
  - [Quick start](#quick-start)
    - [Docker (recommended)](#docker-recommended)
    - [Local (no Docker)](#local-no-docker)
    - [CLI](#cli)
  - [Data provenance and licensing](#data-provenance-and-licensing)
  - [What a "node" is](#what-a-node-is)
  - [How coordinates are assigned](#how-coordinates-are-assigned)
  - [Schema mapping](#schema-mapping)
  - [CRS and reprojection](#crs-and-reprojection)
  - [Reproducibility and audit trail](#reproducibility-and-audit-trail)
  - [Fault handling and logging](#fault-handling-and-logging)
  - [Tests](#tests)
  - [Assumptions and limitations](#assumptions-and-limitations)
  - [Repository layout](#repository-layout)
  - [Regenerating reference data and fixtures](#regenerating-reference-data-and-fixtures)

---

## Quick start

### Docker (recommended)

```bash
cp .env.example .env                     # set POSTGRES_PASSWORD; optional PEERINGDB_API_KEY, NOMINATIM_EMAIL
docker compose up -d postgis             # isolated PostGIS, creates only schema iris_staging

docker compose run --rm ingest           # live run: Germany (the task endpoint), --strict gates
docker compose run --rm ingest --config config/config.example.yaml run --countries DE,NL,FR,AT,CH,PL --strict
docker compose run --rm ingest --config config/config.example.yaml run --region Europe

docker compose --profile demo run --rm demo     # offline replay of the SYNTHETIC fixture into PostGIS
docker compose --profile test run --rm tests    # full test suite, incl. PostGIS integration tests
```

Outputs land in `./data` on the host:

| path                       | content                                                                               |
| -------------------------- | ------------------------------------------------------------------------------------- |
| `data/snapshots/<run_id>/` | raw API responses (`ix.json`, `ixfac.json`, `fac.json`) + `manifest.json` with sha256 |
| `data/geocode_cache.json`  | every Nominatim lookup made, with OSM reference and timestamp                         |
| `data/reports/<run_id>/`   | `quality_report.{json,md}`, `peeringdb_ix_nodes.{geojson,csv}`                        |

### Local (no Docker)

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt && pip install --no-deps -e .

pytest -q                                                   # unit tests (PostGIS tests skip)
iris-peering --log-format text --config tests/fixtures/demo_config.yaml \
    run --snapshot tests/fixtures/synthetic_snapshot --offline --no-db --strict

export DATABASE_URL=postgresql://user:pass@localhost:5434/iris
iris-peering --config config/config.example.yaml run --countries DE --strict
psql "$DATABASE_URL" -f sql/validation_queries.sql
```

### CLI

```
iris-peering [--config FILE] [--log-format json|text] run   [--countries DE,NL | --region Europe]
                                                             [--snapshot DIR] [--offline] [--no-db]
                                                             [--strict] [--run-id UUID]
iris-peering [--config FILE]                          fetch [--countries … | --region …] --out DIR
```

- `--snapshot DIR` replays saved raw responses instead of calling PeeringDB.
- `--offline` forbids all network access (needs `--snapshot`; geocoding from cache only).
- `--strict` exits with code 2 if any acceptance gate in the quality report fails.

---

## Data provenance and licensing

| source                                                                               | used for                                                                                                          | access                                                                                             | terms                                                                                                                                                     |
| ------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **PeeringDB API v2** — `ix`, `ixfac`, `fac` objects, `https://www.peeringdb.com/api` | IX identity, IX↔facility links, facility address and coordinates                                                  | public read API; optional API key (`PEERINGDB_API_KEY`) for higher rate limits                     | [PeeringDB Acceptable Use Policy](https://www.peeringdb.com/aup). Records are operator-maintained. Licence string stored on every row (`source_licence`). |
| **OpenStreetMap Nominatim**                                                          | coordinates only where PeeringDB supplies none or supplies unusable ones                                          | public API, ≤1 req/s, identifying User-Agent, results cached (all enforced)                        | © OpenStreetMap contributors, ODbL 1.0. Stored per row in `geocode_licence`.                                                                              |
| **Natural Earth 1:50m Admin-0 countries**                                            | coarse country bounding boxes for coordinate sanity checks (`src/iris_peering/reference/country_bbox_ne50m.json`) | GitHub `nvkelso/natural-earth-vector` @ `ca96624a`, regenerated by `scripts/build_country_bbox.py` | public domain                                                                                                                                             |
| **Synthetic fixtures** (`tests/fixtures/`)                                           | tests and the example result                                                                                      | generated by `tests/fixtures/make_fixtures.py`                                                     | invented data; every name starts with `SYNTH-`; geocode entries say `provider: synthetic-fixture`                                                         |

Cross-border handling: the IX country (`ix_country`) and the facility country
(`fac_country` → `node_country`) are stored separately. An IX listed under DE whose
facility is in NL keeps `node_country = NL` and `cross_border = true`, and its
coordinate is validated against NL, not DE.

Review the PeeringDB AUP before using the data outside internal screening; it restricts
uses such as unsolicited marketing to listed contacts. No contact fields (emails,
phones) are copied into typed columns; they remain only inside `raw` for lineage.

---

## What a "node" is

PeeringDB has no coordinates on the IX object itself; physical location lives on the
facilities an IX is present in. One IX therefore becomes several nodes:

| `node_type`   | `node_id`          | when                                     | location from                    |
| ------------- | ------------------ | ---------------------------------------- | -------------------------------- |
| `ix_facility` | `ixfac:<ixfac.id>` | the IX has a facility link               | the facility                     |
| `ix_city`     | `ix:<ix.id>`       | the IX has no facility link in PeeringDB | the IX city (city-level geocode) |

Identifiers are PeeringDB's own stable integer ids, so a node keeps its id across runs.

## How coordinates are assigned

First usable option wins; every attempt leaves a flag on the row.

| order | `coord_source`       | how                                                                         | `coord_accuracy`                                                                                                     | `coord_accuracy_m` (nominal)       |
| ----- | -------------------- | --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | ---------------------------------- |
| 1     | `peeringdb_facility` | `fac.latitude` / `fac.longitude` as published                               | `facility_reported`                                                                                                  | 100 (operator-entered, unverified) |
| 2     | `geocoded_address`   | Nominatim structured query: street + postcode + city + country              | from Nominatim `place_rank`: `building` 50 · `street` 250 · `neighbourhood` 1 500 · `locality` 5 000 · `city` 15 000 |                                    |
| 3     | `geocoded_city`      | Nominatim city + country                                                    | `city`                                                                                                               | ≥ 5 000                            |
| 4     | `none`               | nothing usable — row kept, `latitude/longitude/geom` NULL, flag `unlocated` | `unknown`                                                                                                            | NULL                               |

A coordinate (source or geocoded) is only accepted if it passes:

- both values present (`coordinates_partial` otherwise), numeric and finite;
- inside WGS84 ranges (`coordinates_out_of_range`);
- not `0,0` (`coordinates_null_island`);
- inside the Natural Earth bounding box of the node's country ±0.1° (`outside_country_bbox`).
  If the swapped pair would fit, the row is also flagged `lat_lon_swap_suspected` — the
  value is **not** silently swapped; it falls through to geocoding and the original is
  kept in `source_latitude/source_longitude`.
- For countries that extend beyond the European reference window (RU, overseas FR/NL)
  a miss is a warning (`outside_reference_extent`), not a rejection.

Geocodes typed by Nominatim as a country, state, region or county are refused
(`geocode_too_coarse:*`), so no node can end up on a country or state centroid. Results
typed as a settlement (city, town, municipality, …) are accepted even when their
`place_rank` is low, because rank follows the administrative hierarchy: German
district-free cities such as Frankfurt are rank 12 and the city-states Berlin, Hamburg
and Bremen rank 8. Untyped results below rank 13 are refused. There is no hard-coded
fallback coordinate anywhere in the code.

---

## Schema mapping

Target: `iris_staging.peeringdb_ix_nodes` (DDL: `src/iris_peering/sql/001_staging_schema.sql`).
Companion tables: `iris_staging.peeringdb_ingest_runs`, `iris_staging.peeringdb_rejects`.

| column                                                                                                                    | type                  | source / rule                                                                                                |
| ------------------------------------------------------------------------------------------------------------------------- | --------------------- | ------------------------------------------------------------------------------------------------------------ |
| `node_id` (PK)                                                                                                            | text                  | `ixfac:<ixfac.id>` or `ix:<ix.id>`                                                                           |
| `node_type`                                                                                                               | text                  | `ix_facility` \| `ix_city`                                                                                   |
| `ix_id`, `ix_name`, `ix_name_long`, `ix_aka`                                                                              | int, text             | `ix.id`, `ix.name`, `ix.name_long`, `ix.aka` (whitespace collapsed, empty → NULL)                            |
| `ix_city`, `ix_country`                                                                                                   | text, char(2)         | `ix.city`, `ix.country` (upper-cased ISO-3166 alpha-2; invalid → record rejected)                            |
| `ix_region_continent`, `ix_website`                                                                                       | text                  | `ix.region_continent`, `ix.website`                                                                          |
| `ix_net_count`, `ix_fac_count`                                                                                            | int                   | `ix.net_count`, `ix.fac_count` (non-negative ints only)                                                      |
| `ix_created_at`, `ix_updated_at`                                                                                          | timestamptz           | `ix.created`, `ix.updated` → UTC                                                                             |
| `ixfac_id`, `ixfac_updated_at`                                                                                            | int, timestamptz      | `ixfac.id`, `ixfac.updated`                                                                                  |
| `fac_id`, `fac_name`, `fac_org_name`                                                                                      | int, text             | `ixfac.fac_id`, `fac.name`, `fac.org_name`                                                                   |
| `fac_address1`, `fac_address2`, `fac_city`, `fac_state`, `fac_zipcode`, `fac_country`, `fac_clli`                         | text                  | `fac.*`; if the facility record is absent, `name/city/country` from `ixfac` (flag `facility_record_missing`) |
| `fac_updated_at`                                                                                                          | timestamptz           | `fac.updated` → UTC                                                                                          |
| `node_city`, `node_country`                                                                                               | text, char(2)         | facility city/country, else IX city/country                                                                  |
| `cross_border`                                                                                                            | bool                  | `fac_country` ≠ `ix_country`                                                                                 |
| `latitude`, `longitude`                                                                                                   | float8                | accepted coordinate, WGS84 decimal degrees, rounded to 7 dp (~1 cm)                                          |
| `geom`                                                                                                                    | geometry(Point, 4326) | **generated** in PostgreSQL from `longitude, latitude` — cannot drift from them                              |
| `native_crs`                                                                                                              | text                  | `EPSG:4326`                                                                                                  |
| `coord_source`, `coord_accuracy`, `coord_accuracy_m`                                                                      | text, text, int       | see table above                                                                                              |
| `source_latitude`, `source_longitude`                                                                                     | float8                | PeeringDB's values as published, even when rejected                                                          |
| `geocode_query`, `geocode_provider`, `geocode_osm_ref`, `geocode_display_name`, `geocode_retrieved_at`, `geocode_licence` | text / timestamptz    | exact query, provider, OSM object (`node/123`), when, licence                                                |
| `quality_flags`                                                                                                           | text[]                | all checks that fired for the row                                                                            |
| `source_name`, `source_url`, `source_licence`                                                                             | text                  | `PeeringDB`, API URL of the defining object, AUP reference                                                   |
| `source_record_hash`                                                                                                      | char(64)              | sha256 of the canonical raw ix+ixfac+fac payload (change detection)                                          |
| `raw`                                                                                                                     | jsonb                 | the untouched source objects — no data loss                                                                  |
| `is_active`                                                                                                               | bool                  | false once the node disappears upstream (never deleted)                                                      |
| `first_seen_run_id`, `last_seen_run_id`, `first_seen_at`, `last_seen_at`, `last_changed_at`                               | uuid / timestamptz    | lineage across runs                                                                                          |

Constraints: lat/lon ranges, both-or-neither coordinates, `coord_source = 'none'` ⇔ no
coordinate, GiST index on `geom`.

Units: coordinates in decimal degrees; accuracy in metres; timestamps in UTC; counts
are plain integers. PeeringDB carries no other physical units for these objects.

## CRS and reprojection

- **Native CRS:** PeeringDB publishes facility coordinates as decimal latitude/longitude
  without declaring a datum. They are treated as WGS84 / EPSG:4326, which is what every
  web-map-derived entry implies. Nominatim returns EPSG:4326. Stored as EPSG:4326.
- **Axis order:** columns are explicit (`latitude`, `longitude`); `geom` is `POINT(lon lat)`.
- **Reprojection limits:** for metric work, use `geom::geography` (as in
  `sql/validation_queries.sql`) or project to ETRS89-based CRSs such as EPSG:25832
  (Germany, UTM 32N — used by `iris_core.land_values`) or EPSG:3035 (pan-European LAEA).
  The WGS84↔ETRS89 offset (decimetres) is far below the source accuracy (100 m to 15 km),
  so it is not modelled. EPSG:25832 distorts increasingly outside ~6–12°E; use 3035 or
  geography for continental distances. A projected value is never more accurate than
  `coord_accuracy_m` — carry that column into any distance-based score.

## Reproducibility and audit trail

- Raw responses are written to disk **before** any transformation, with a sha256 per file
  and a combined `snapshot_digest` recorded in `peeringdb_ingest_runs`.
- `--snapshot DIR --offline` replays a run with no network: same inputs + same geocode
  cache ⇒ identical `output_digest` (sha256 of all normalised nodes). This is tested,
  including with shuffled upstream record order.
- Duplicate resolution is deterministic: identical copies keep the first; otherwise the
  newest `updated` wins, ties broken by canonical hash; duplicate `(ix_id, fac_id)` links
  keep the lowest `ixfac.id`.
- Every geocoded coordinate can be traced to a cache entry (query → OSM object).

## Fault handling and logging

| situation                                                                | behaviour                                                                                                                                                                                                                |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| HTTP 429 / 5xx / connection error                                        | retry with exponential backoff, honour `Retry-After`; give up after `max_retries` and fail the run (nothing loaded)                                                                                                      |
| HTTP 4xx                                                                 | fail fast, no retry                                                                                                                                                                                                      |
| non-JSON body or body without a `data` array                             | stored verbatim in the snapshot, reported as `malformed_response_no_data_array`; the run still loads what it has but **skips deactivation** so missing pages can't mark live nodes inactive; gate `fetch_complete` fails |
| record not an object / bad id / missing name / bad country / status ≠ ok | rejected to `peeringdb_rejects` with reason and raw payload                                                                                                                                                              |
| duplicate records / duplicate links / orphan links                       | rejected with `duplicate_*` / `orphan_unknown_ix`, raw kept                                                                                                                                                              |
| facility missing for a link                                              | node kept from the link's denormalised name/city/country                                                                                                                                                                 |
| geocoder error                                                           | not cached (retried next run), node falls to next option or stays unlocated                                                                                                                                              |
| DB error during load                                                     | single transaction rolled back, run marked `failed`                                                                                                                                                                      |
| schema not `iris_staging*`                                               | refused at config load **and** in the loader constructor                                                                                                                                                                 |

Logs are JSON lines on stderr (`--log-format text` for humans). Every line has a
`source` field (`peeringdb`, `nominatim`, `transform`, `loader`, `pipeline`) and, where
relevant, `entity`, `source_id`, `url`, `status`, `reason`, `run_id`.

## Tests

```bash
pytest -q                                   # 87 tests, no network, no DB
IRIS_TEST_DATABASE_URL=postgresql://…  pytest -q -m postgis    # +6 PostGIS integration tests
```

| file                   | covers                                                                                                                                                                          |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_normalize.py`    | text/country/int/timestamp/coordinate parsing; swap, null-island, partial, range, wrong-country, partial-extent checks                                                          |
| `test_client.py`       | ix→ixfac→fac chaining and batching, 429 + `Retry-After`, 5xx backoff, 4xx no-retry, non-JSON bodies, API key only in header                                                     |
| `test_geocode.py`      | cache-first, null-result caching, errors not cached, rate limit, too-coarse rejection, low-rank big cities accepted, stale cache refresh, offline mode                          |
| `test_snapshot.py`     | round-trip, checksum tamper detection, malformed response detection                                                                                                             |
| `test_transform.py`    | every fixture edge case, duplicate resolution, record accounting (no loss), determinism under shuffling, single-coordinate gate                                                 |
| `test_cli.py`          | end-to-end offline run, exports, error paths                                                                                                                                    |
| `test_config.py`       | staging-only guard, identifier injection, secrets from env only                                                                                                                 |
| `test_load_postgis.py` | idempotent upsert, generated `geom` SRID/axis order, rejects stored, deactivation/reactivation, no deactivation on incomplete fetch, change detection (source and geocode-only) |

The example result in `examples/synthetic_fixture_run/` was produced by loading the
synthetic snapshot into PostGIS and running `sql/validation_queries.sql`. See
[`QUALITY_REPORT.md`](QUALITY_REPORT.md).

## Assumptions and limitations

- **Scope.** Default scope is the task endpoint, `country=DE`. "Continental Europe" is
  supported via `--countries …` or `--region Europe` (PeeringDB's `region_continent`).
- **Population.** Only PeeringDB records with `status = ok` (the API default) are ingested.
- **Accuracy of PeeringDB coordinates.** They are self-reported by facility operators and
  not verified by PeeringDB; the bbox check catches gross errors only (wrong country,
  swapped axes, 0/0), not a point placed on the wrong street.
- **Bounding boxes are coarse.** A wrong coordinate inside the right country's bbox (e.g.
  near a border, or in a bbox corner over the sea) passes. A point-in-polygon test against
  1:10m borders would be stricter; it was left out to keep the job dependency-light.
- **City-level nodes share coordinates.** Several IXs in one city with no facility data get
  the same city coordinate; they are labelled `coord_accuracy = city`, and the report gate
  fails if any single point holds more than 50% of located nodes.
- **Geocoding depends on Nominatim's index** and on address quality in PeeringDB; misses are
  reported, not guessed. Results are cached indefinitely for reproducibility; delete the
  cache file to refresh.
- **Facility outside scope.** IXs are selected by IX country; their facilities are fetched
  whatever their country, so cross-border facilities appear (flagged) in the output. In the
  live DE run these included facilities of distributed/remote-peering IXs in AU, CA, JP, KR
  and SG; outside Europe there is no reference bbox, so they carry
  `country_bbox_unavailable`. Filter on `node_country` for country-level screening.
- **Deactivation** is scoped to the requested countries/region and only applied after a
  complete fetch.
- **Live verification.** Run against the live API for `country=DE` on 2026-10-28; the
  results, and a geocoding bug that run exposed and that is now fixed, are in
  `QUALITY_REPORT.md`.

## Repository layout

```
├── Dockerfile, docker-compose.yml, docker/initdb/   container build + isolated PostGIS
├── requirements.txt, requirements-dev.txt          pinned dependencies
├── pyproject.toml                                  package + `iris-peering` entry point
├── config/config.example.yaml, .env.example        example configuration (no secrets)
├── src/iris_peering/
│   ├── client.py        PeeringDB API client (retries, batching)
│   ├── snapshot.py      raw-response snapshots + sha256 manifest
│   ├── normalize.py     field normalisers, coordinate checks
│   ├── geocode.py       cache-first Nominatim geocoder
│   ├── transform.py     snapshot → validated nodes + rejects
│   ├── load.py          PostGIS staging loader (upsert, rejects, run log)
│   ├── report.py        quality report, GeoJSON/CSV export, acceptance gates
│   ├── pipeline.py      orchestration
│   ├── cli.py, logs.py, config.py
│   ├── sql/001_staging_schema.sql
│   └── reference/country_bbox_ne50m.json
├── sql/validation_queries.sql
├── scripts/build_country_bbox.py
├── tests/  (+ fixtures/ — SYNTHETIC)
├── examples/synthetic_fixture_run/                 example validated result
└── QUALITY_REPORT.md
```

## Regenerating reference data and fixtures

```bash
# Synthetic test fixtures (snapshot + geocode cache)
python tests/fixtures/make_fixtures.py

# Country bounding boxes from Natural Earth 1:50m
python scripts/build_country_bbox.py ne_50m_admin_0_countries.geojson \
    src/iris_peering/reference/country_bbox_ne50m.json
```
