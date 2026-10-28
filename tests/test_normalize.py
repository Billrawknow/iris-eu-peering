import math

import pytest

from iris_peering import normalize as n


@pytest.mark.parametrize(
    "raw,expected",
    [("  Frankfurt   am  Main ", "Frankfurt am Main"), ("", None), ("   ", None), (None, None), (12, "12"), ([], None)],
)
def test_text(raw, expected):
    assert n.text(raw) == expected


@pytest.mark.parametrize("raw,expected", [("de", "DE"), (" NL ", "NL"), ("Germany", None), ("D", None), (None, None)])
def test_country(raw, expected):
    assert n.country(raw) == expected


@pytest.mark.parametrize("raw,expected", [(5, 5), ("17", 17), (0, None), (-3, None), (True, None), ("abc", None), (1.5, None)])
def test_pos_int(raw, expected):
    assert n.pos_int(raw) == expected


def test_timestamp_normalised_to_utc():
    assert n.timestamp("2024-05-01T10:00:00Z") == "2024-05-01T10:00:00+00:00"
    assert n.timestamp("2024-05-01T12:00:00+02:00") == "2024-05-01T10:00:00+00:00"
    assert n.timestamp("2024-05-01T10:00:00") == "2024-05-01T10:00:00+00:00"  # naive -> UTC
    assert n.timestamp("yesterday") is None


@pytest.mark.parametrize("raw,expected", [("50.1", 50.1), (8, 8.0), ("x", None), (None, None), (True, None), ("nan", None)])
def test_coord(raw, expected):
    got = n.coord(raw)
    assert got == expected or (expected is None and got is None)
    assert got is None or math.isfinite(got)


def test_check_point_valid_inside_country():
    ok, flags, swap = n.check_point(50.11, 8.68, "DE")
    assert ok and flags == [] and not swap


def test_check_point_swapped_detected_not_corrected():
    ok, flags, swap = n.check_point(13.405, 52.52, "DE")
    assert not ok and swap and "lat_lon_swap_suspected" in flags


def test_check_point_null_island_partial_and_range():
    assert n.check_point(0.0, 0.0, "DE")[1] == ["coordinates_null_island"]
    assert n.check_point(51.0, None, "DE")[1] == ["coordinates_partial"]
    assert n.check_point(None, None, "DE")[1] == ["coordinates_missing"]
    ok, flags, _ = n.check_point(95.0, 8.0, "DE")
    assert not ok and "coordinates_out_of_range" in flags


def test_check_point_wrong_country_rejected():
    ok, flags, _ = n.check_point(40.4168, -3.7038, "DE")  # Madrid
    assert not ok and flags == ["outside_country_bbox"]


def test_check_point_partial_extent_country_is_warning_only():
    # Novosibirsk: outside the European reference window for RU, but legitimately Russian.
    ok, flags, _ = n.check_point(55.03, 82.92, "RU")
    assert ok and flags == ["outside_reference_extent"]


def test_check_point_unknown_country_reference():
    ok, flags, _ = n.check_point(35.68, 139.69, "JP")
    assert ok and flags == ["country_bbox_unavailable"]


def test_canonical_hash_is_key_order_independent():
    assert n.canonical_hash({"a": 1, "b": 2}) == n.canonical_hash({"b": 2, "a": 1})
