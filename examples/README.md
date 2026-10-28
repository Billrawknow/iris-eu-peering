# Example result — SYNTHETIC fixture

`synthetic_fixture_run/` is the output of:

```bash
iris-peering --config tests/fixtures/demo_config.yaml \
    run --snapshot tests/fixtures/synthetic_snapshot --offline --strict \
    --run-id 00000000-0000-4000-8000-000000000149
psql "$DATABASE_URL" -f sql/validation_queries.sql
```

against PostGIS 3.4 / PostgreSQL 16, connected as a role that owns only `iris_staging`.

**All records are synthetic** (names start with `SYNTH-`, geocodes are labelled
`provider: synthetic-fixture`). They exist to show the handling of malformed, missing,
duplicate and cross-border records, not real IXP locations. A live run writes the same
set of files to `data/reports/<run_id>/`.

| file | content |
|---|---|
| `quality_report.md` / `.json` | coverage, provenance mix, flags, rejects, acceptance gates |
| `peeringdb_ix_nodes.geojson` / `.csv` | the 11 validated nodes (EPSG:4326) |
| `validation_queries_output.txt` | output of `sql/validation_queries.sql` against the loaded staging table |
| `run_log.jsonl` | the structured, source-aware run log |
