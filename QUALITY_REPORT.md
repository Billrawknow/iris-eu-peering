# Quality / error report — IRIS-EU-PEERING (task 149)

## 1. What this report is based on

| evidence | status |
|---|---|
| Automated tests (`pytest`) | **93 passed** — 87 unit/offline + 6 PostGIS integration (PostgreSQL 16, PostGIS 3.4) on CPython 3.11, also run inside the Docker test image |
| Example validated result | `examples/synthetic_fixture_run/` — synthetic snapshot loaded into PostGIS as a least-privilege role that owns only `iris_staging`; validation SQL output included |
| Live PeeringDB run | `country=DE`, 2026-10-28, via `docker compose run --rm ingest` — see section 2a. The pipeline writes a full report for every run to `data/reports/<run_id>/quality_report.md`. |

The synthetic fixture is deliberately hostile: 15 IX records, 12 links and 8 facilities,
of which most exercise a failure mode. Coverage numbers below therefore describe the
handling of edge cases, **not** the expected coverage of real PeeringDB data.

## 2. Example result (synthetic fixture, run `00000000-0000-4000-8000-000000000149`)

| metric | value |
|---|---|
| IX records received → valid | 15 → 8 |
| ixfac links received → valid | 12 → 9 |
| facility records received → valid | 8 → 8 |
| nodes produced | 11 (9 `ix_facility`, 2 `ix_city`) across 8 IXs |
| nodes located | 10 (90.9 %) — 1 left unlocated on purpose |
| distinct coordinates | 10 for 10 located nodes; largest shared point = 1 node (10 %) |
| location source | 3 PeeringDB facility · 2 address geocode · 5 city geocode · 1 none |
| rejected source records | 10 (all stored with raw payload in `iris_staging.peeringdb_rejects`) |
| idempotency | 2nd load: 0 inserted, 11 unchanged, 0 duplicates |
| output digest | `5fd004a1…67c6b3`, identical across reruns and shuffled input order |

Acceptance gates (all PASS): not collapsed to a single coordinate · every location has
provenance · unique node ids · coordinates inside country reference · fetch complete.

## 2a. Live run — Germany (`/api/ix?country=DE`, 2026-10-28)

**First run** (`24f1d55d-…`):

| metric | value |
|---|---|
| IX / ixfac / fac records fetched | 59 / 233 / 117 (fac in 2 batches) |
| rejected source records | 0 |
| nodes | 248 |
| located | 235 (94.8 %) — 232 from PeeringDB facility coordinates, 3 city geocodes |
| unlocated | 13 |
| distinct coordinates | 112; largest shared point 14 nodes (6 %) at 50.119 N 8.735 E, a Frankfurt data-centre campus hosting many IXs — a genuine co-location, not a default |
| cross-border facilities | 13 (AT, NL, PL, and remote-peering facilities in AU, CA, JP, KR, SG) |
| acceptance gates | all pass |

**Finding.** All 13 unlocated nodes carried `geocode_too_coarse:city`: 12 IXs with no
facility link (e.g. in Frankfurt, Berlin, Hamburg, Düsseldorf, Nürnberg, Bremen) and
one facility without coordinates. Nominatim's `place_rank` follows the administrative
hierarchy, so district-free German cities come back at rank 12 and city-states at rank
8; the original "rank ≥ 13" rule refused them as state-level. The quality flags made
the cause visible directly.

**Fix.** Acceptance now uses the result's own type: settlements (city, town,
municipality, …) are accepted at any rank with `coord_accuracy = city` (15 km nominal);
country/state/region/county results are always refused. Old cache entries without the
type are refreshed once. Regression tests: `test_big_cities_with_low_admin_rank_are_accepted`,
`test_admin_areas_are_refused_whatever_the_rank`, `test_cache_entry_without_addresstype_is_refreshed_online`.

**Re-run after the fix** (`556da34a-…`, same day, same 59 / 233 / 117 source records):

| metric | first run | re-run |
|---|---|---|
| nodes located | 235 / 248 (94.8 %) | **248 / 248 (100 %)** |
| IXs with at least one located node | 47 / 59 | **59 / 59** |
| from PeeringDB facility coordinates | 232 | 232 |
| city geocodes (`coord_accuracy = city`) | 3 | 16 |
| distinct coordinates | 112 | 119 |
| largest shared point | 14 nodes (6 %) | 14 nodes (5.6 %), unchanged campus |
| rejects | 0 | 0 |
| acceptance gates | all pass | all pass |

The re-run also exposed a reporting gap: because the raw PeeringDB payload was
identical, the loader counted the 13 newly located nodes as `unchanged` (the rows *were*
updated, but `last_changed_at` did not move). Change detection now also compares the
derived location (`latitude`, `longitude`, `coord_source`, `coord_accuracy`,
`quality_flags`); covered by `test_geocode_only_change_is_counted_as_changed`.

Screening note: 16 of the 248 nodes (6.5 %) are city-level only, and 13 nodes sit in
facilities outside Germany (remote-peering / distributed IXs). Distance-based scoring
should use `coord_accuracy_m` and filter on `node_country`.

## 3. Rejected data

| entity | reason | example (synthetic) | handling |
|---|---|---|---|
| ix | `duplicate_identical` | IX 9001 delivered twice | first copy kept |
| ix | `duplicate_superseded` | IX 9002 with `updated` 2024 and 2026 | newest kept (net_count 42), older stored as reject |
| ix | `missing_or_invalid_id` | `"id": "abc"` | rejected |
| ix | `missing_required:name` | empty name | rejected |
| ix | `invalid_country_code` | `"country": "Germany"` | rejected (no guessing of codes) |
| ix | `status_pending` | status ≠ ok | rejected |
| ix | `not_an_object` | a bare string in `data` | rejected |
| ixfac | `duplicate_ix_fac_pair` | links 8001 and 8099 for the same IX+facility | lowest link id kept |
| ixfac | `orphan_unknown_ix` | link to IX 9999 not in scope | rejected |
| ixfac | `missing_required:ix_id` | `ix_id: null` | rejected |

Accounting check (tested): `received = valid + rejected` for every entity, so no record
disappears without a trace.

## 4. Edge cases kept, with flags

| case | node | outcome | evidence |
|---|---|---|---|
| coordinates as numeric strings | ixfac:8002 | parsed, accepted | `test_numeric_string_coordinates_accepted` |
| facility without coordinates | ixfac:8003 | address geocode, `building` | `test_missing_coordinates_geocoded_from_address` |
| latitude/longitude swapped | ixfac:8004 | flagged `lat_lon_swap_suspected`, **not** auto-swapped; address geocode; original kept in `source_*` | `test_swapped_coordinates_flagged_not_silently_fixed` |
| `0,0` | ixfac:8005 | `coordinates_null_island` → city geocode | `test_null_island_falls_back_to_city` |
| coordinates in the wrong country (Madrid for a DE facility) | ixfac:8009 | `outside_country_bbox`; address geocode miss; city geocode | `test_out_of_country_coords_rejected_then_address_miss_then_city` |
| only latitude present | ixfac:8008 | `coordinates_partial` → city geocode | `test_partial_coordinates_rejected` |
| facility in another country (DE IX, NL facility) | ixfac:8006 | `cross_border = true`, validated against NL | `test_cross_border_facility_keeps_its_own_country` |
| facility record not returned | ixfac:8007 | built from link's denormalised fields, `facility_record_missing` | `test_missing_facility_uses_ixfac_denormalised_city` |
| IX with no facility link | ix:9003 | `ix_city` node, city accuracy | `test_ix_without_facility_is_city_level_node` |
| city not geocodable | ix:9007 | kept, coordinates NULL, `unlocated` | `test_ungeocodable_node_kept_unlocated` |
| whitespace-only / padded strings | ixfac:8009 | trimmed; blank → NULL | `test_whitespace_normalised` |
| API returns HTML / no `data` array | — | stored verbatim, gate `fetch_complete` fails, deactivation skipped | `test_non_json_body_is_kept_for_audit`, `test_incomplete_fetch_does_not_deactivate` |
| node disappears upstream | ix:9003 | `is_active = false`, row kept; reactivated if it returns | `test_vanished_node_deactivated_not_deleted` |
| everything on one point | — | gate `not_collapsed_to_single_coordinate` fails, `--strict` exits 2 | `test_report_gate_fails_when_everything_shares_one_point` |

## 5. Acceptance criteria

| criterion | how it is met |
|---|---|
| Do not hard-code all IXPs to a single coordinate | Coordinates come from facility records or per-node geocodes; no fallback constant exists. Gate + SQL check 3 measure distinct points and the largest shared point. |
| Report accuracy and provenance | Per row: `coord_source`, `coord_accuracy`, `coord_accuracy_m`, `source_url`, `source_licence`, `geocode_query/provider/osm_ref/retrieved_at/licence`, `source_record_hash`, `raw`. |
| Deterministic or auditable | Both: raw snapshot with sha256 manifest, cached geocodes, `--offline` replay gives an identical `output_digest`. |
| Uses the documented source | `GET /api/ix?country=DE` (task endpoint), then `/api/ixfac` and `/api/fac` for locations. |
| Staging only, no data loss | Schema guard in config and loader; upsert by stable id; deactivate, never delete; rejects stored with raw. |

## 6. Known gaps

1. **Only Germany has been run live.** Other countries / `--region Europe` use the same
   code path but their coverage has not been observed yet.
2. **Coordinate checks are bbox-based.** A wrong point inside the correct country's
   bounding box is not detected; PeeringDB facility coordinates are operator-entered.
3. **City-level nodes are coarse** (nominal ≥5 km). They are labelled, but scoring
   should weight by `coord_accuracy_m`.
4. **Nominatim throughput** is capped at 1 request/s, so a cold run over all of Europe
   with many missing coordinates takes minutes; the cache makes later runs fast.
5. **Accuracy radii are nominal** class values, not measured errors.
6. **No scheduler** is included; the job is a single idempotent command suitable for cron
   or an orchestrator.
