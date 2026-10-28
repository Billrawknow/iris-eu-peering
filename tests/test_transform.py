"""Transform tests on the SYNTHETIC snapshot with offline geocoding."""

import random

import pytest

from iris_peering.report import build_report, output_digest
from iris_peering.snapshot import Snapshot
from iris_peering.transform import build_nodes


@pytest.fixture
def result(snapshot_dir, offline_geocoder):
    return build_nodes(Snapshot.read(snapshot_dir), offline_geocoder, ("DE",))


def by_id(result):
    return {n.node_id: n for n in result.nodes}


def test_node_inventory(result):
    assert sorted(by_id(result)) == [
        "ix:9003", "ix:9007", "ixfac:8001", "ixfac:8002", "ixfac:8003", "ixfac:8004",
        "ixfac:8005", "ixfac:8006", "ixfac:8007", "ixfac:8008", "ixfac:8009",
    ]


def test_facility_coordinates_used_with_provenance(result):
    n = by_id(result)["ixfac:8001"]
    assert (n.latitude, n.longitude) == (50.1188, 8.7356)
    assert n.coord_source == "peeringdb_facility" and n.coord_accuracy == "facility_reported"
    assert n.native_crs == "EPSG:4326" and n.source_url.endswith("/ixfac/8001")
    assert n.raw["fac"]["id"] == 7001 and len(n.source_record_hash) == 64


def test_numeric_string_coordinates_accepted(result):
    n = by_id(result)["ixfac:8002"]
    assert (n.latitude, n.longitude) == (50.099, 8.642)


def test_missing_coordinates_geocoded_from_address(result):
    n = by_id(result)["ixfac:8003"]
    assert n.coord_source == "geocoded_address" and n.coord_accuracy == "building"
    assert n.geocode_osm_ref == "synthetic/1" and "Synthstrasse 1" in n.geocode_query
    assert "source_coordinates_missing" in n.quality_flags


def test_swapped_coordinates_flagged_not_silently_fixed(result):
    n = by_id(result)["ixfac:8004"]
    assert "lat_lon_swap_suspected" in n.quality_flags
    assert (n.source_latitude, n.source_longitude) == (13.405, 52.52)  # original kept
    assert n.coord_source == "geocoded_address" and (n.latitude, n.longitude) == (52.507, 13.39)


def test_null_island_falls_back_to_city(result):
    n = by_id(result)["ixfac:8005"]
    assert "coordinates_null_island" in n.quality_flags
    assert n.coord_source == "geocoded_city" and n.coord_accuracy == "city" and n.coord_accuracy_m >= 5000


def test_out_of_country_coords_rejected_then_address_miss_then_city(result):
    n = by_id(result)["ixfac:8009"]
    assert "outside_country_bbox" in n.quality_flags and "geocode_miss:address" in n.quality_flags
    assert n.coord_source == "geocoded_city" and n.node_city == "Stuttgart"


def test_partial_coordinates_rejected(result):
    n = by_id(result)["ixfac:8008"]
    assert "coordinates_partial" in n.quality_flags and n.coord_source == "geocoded_city"


def test_cross_border_facility_keeps_its_own_country(result):
    n = by_id(result)["ixfac:8006"]
    assert n.cross_border and n.ix_country == "DE" and n.node_country == "NL"
    assert n.coord_source == "peeringdb_facility"


def test_missing_facility_uses_ixfac_denormalised_city(result):
    n = by_id(result)["ixfac:8007"]
    assert "facility_record_missing" in n.quality_flags and n.fac_name == "SYNTH-DC Dusseldorf Gone"
    assert n.coord_source == "geocoded_city"


def test_ix_without_facility_is_city_level_node(result):
    n = by_id(result)["ix:9003"]
    assert n.node_type == "ix_city" and n.coord_accuracy == "city" and "no_facility_link" in n.quality_flags


def test_ungeocodable_node_kept_unlocated(result):
    n = by_id(result)["ix:9007"]
    assert n.latitude is None and n.coord_source == "none" and "unlocated" in n.quality_flags


def test_duplicates_resolved_deterministically(result):
    ber = by_id(result)["ixfac:8004"]
    assert ber.ix_net_count == 42  # newer `updated` wins
    reasons = {(r.entity, r.source_id, r.reason) for r in result.rejects}
    assert ("ix", 9001, "duplicate_identical") in reasons
    assert ("ix", 9002, "duplicate_superseded") in reasons
    assert ("ixfac", 8099, "duplicate_ix_fac_pair") in reasons


def test_malformed_records_rejected_with_raw_payload(result):
    reasons = {r.reason for r in result.rejects}
    assert {
        "missing_or_invalid_id", "missing_required:name", "invalid_country_code",
        "status_pending", "not_an_object", "orphan_unknown_ix", "missing_required:ix_id",
    } <= reasons
    assert all(r.raw is not None for r in result.rejects)


def test_whitespace_normalised(result):
    n = by_id(result)["ixfac:8009"]
    assert n.ix_city == "Stuttgart" and n.ix_website is None


def test_no_data_loss_accounting(result):
    c = result.counts
    ix_rejects = sum(1 for r in result.rejects if r.entity == "ix")
    assert c["ix_received"] == c["ix_valid"] + ix_rejects
    ixfac_rejects = sum(1 for r in result.rejects if r.entity == "ixfac")
    assert c["ixfac_received"] == c["ixfac_valid"] + ixfac_rejects


def test_not_collapsed_to_single_coordinate(result):
    located = [(n.latitude, n.longitude) for n in result.nodes if n.latitude is not None]
    assert len(set(located)) == len(located) > 1


def test_output_is_deterministic_and_order_independent(snapshot_dir, offline_geocoder):
    snap = Snapshot.read(snapshot_dir)
    first = output_digest(build_nodes(snap, offline_geocoder, ("DE",)).nodes)
    for seed in range(3):  # shuffle upstream record order: same output
        shuffled = Snapshot.read(snapshot_dir)
        for entity in ("ix", "ixfac", "fac"):
            for resp in shuffled.responses[entity]:
                random.Random(seed).shuffle(resp["body"]["data"])
        assert output_digest(build_nodes(shuffled, offline_geocoder, ("DE",)).nodes) == first


def test_report_gates_pass_on_fixture(result):
    rep = build_report(result, run_id="t", scope={}, snapshot_digest="x",
                       geocode_stats={}, load_stats=None, fetch_complete=True)
    assert rep["all_gates_pass"], rep["acceptance_gates"]
    assert rep["coverage"]["nodes_located"] == 10 and rep["coverage"]["nodes_unlocated"] == 1


def test_report_gate_fails_when_everything_shares_one_point(result):
    for n in result.nodes:
        n.latitude, n.longitude = 50.0, 8.0
    rep = build_report(result, run_id="t", scope={}, snapshot_digest="x",
                       geocode_stats={}, load_stats=None, fetch_complete=True)
    assert not rep["acceptance_gates"]["not_collapsed_to_single_coordinate"]["pass"]
    assert not rep["all_gates_pass"]


def test_geocoding_disabled_leaves_nodes_unlocated(snapshot_dir):
    res = build_nodes(Snapshot.read(snapshot_dir), None, ("DE",))
    n = {x.node_id: x for x in res.nodes}["ixfac:8003"]
    assert n.latitude is None and "geocoding_disabled" in n.quality_flags
