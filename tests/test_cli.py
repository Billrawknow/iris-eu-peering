import json

from iris_peering.cli import main


def _cfg_file(tmp_path, cache):
    p = tmp_path / "cfg.yaml"
    p.write_text(
        f"geocoding:\n  cache_path: {cache}\n"
        f"paths:\n  snapshot_dir: {tmp_path / 'snaps'}\n  report_dir: {tmp_path / 'reports'}\n"
    )
    return p


def test_offline_replay_without_db_writes_report_and_exports(tmp_path, cache_copy, snapshot_dir, capsys):
    rc = main([
        "--config", str(_cfg_file(tmp_path, cache_copy)), "run",
        "--snapshot", str(snapshot_dir), "--offline", "--no-db", "--strict",
        "--run-id", "11111111-1111-1111-1111-111111111111",
    ])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["all_gates_pass"] is True
    out = tmp_path / "reports" / "11111111-1111-1111-1111-111111111111"
    for name in ("quality_report.json", "quality_report.md", "peeringdb_ix_nodes.geojson", "peeringdb_ix_nodes.csv"):
        assert (out / name).exists(), name
    gj = json.loads((out / "peeringdb_ix_nodes.geojson").read_text())
    assert len(gj["features"]) == 11
    assert sum(f["geometry"] is None for f in gj["features"]) == 1


def test_offline_without_snapshot_is_an_error(tmp_path, cache_copy):
    import pytest

    with pytest.raises(RuntimeError, match="--offline requires --snapshot"):
        main(["--config", str(_cfg_file(tmp_path, cache_copy)), "run", "--offline", "--no-db"])


def test_bad_country_flag_rejected(tmp_path, cache_copy):
    assert main(["--config", str(_cfg_file(tmp_path, cache_copy)), "run", "--countries", "Germany", "--no-db"]) == 1
