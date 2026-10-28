"""Geocoder tests with Nominatim mocked."""

import json

import pytest

import responses

from iris_peering.config import GeocodingConfig
from iris_peering.geocode import GeocodeQuery, Geocoder, accuracy_from_rank

URL = "https://nominatim.openstreetmap.org/search"
Q = GeocodeQuery("city", "DE", city="Kassel")


def _geocoder(tmp_path, **kw):
    sleeps = []
    clock = iter(range(0, 1000)).__next__  # monotonic seconds 0,1,2,...
    cfg = GeocodingConfig(cache_path=str(tmp_path / "c.json"), min_interval_s=1.5, **kw)
    return Geocoder(cfg, "ua", sleep=sleeps.append, clock=lambda: float(clock())), sleeps


@responses.activate
def test_network_hit_is_cached_with_provenance(tmp_path):
    responses.get(URL, json=[{"lat": "51.31", "lon": "9.49", "place_rank": 16, "osm_type": "relation", "osm_id": 62499, "display_name": "Kassel"}])
    g, _ = _geocoder(tmp_path)
    res, outcome = g.geocode(Q)
    assert outcome == "network" and res.latitude == 51.31 and res.osm_ref == "relation/62499"
    assert res.accuracy == "city" and "ODbL" in res.licence
    res2, outcome2 = g.geocode(Q)
    assert outcome2 == "cache_hit" and res2.latitude == res.latitude
    assert len(responses.calls) == 1
    g.close()
    saved = json.loads((tmp_path / "c.json").read_text())
    assert saved["entries"][Q.key()]["osm_ref"] == "relation/62499"
    params = responses.calls[0].request.params
    assert params["countrycodes"] == "de" and params["city"] == "Kassel"


@responses.activate
def test_empty_result_cached_as_null_and_error_not_cached(tmp_path):
    responses.get(URL, json=[])
    g, _ = _geocoder(tmp_path)
    assert g.geocode(Q) == (None, "miss")
    assert g.cache.entries[Q.key()] is None
    q2 = GeocodeQuery("city", "DE", city="Fulda")
    responses.get(URL, status=500)
    assert g.geocode(q2) == (None, "error")
    assert q2.key() not in g.cache.entries


@responses.activate
def test_too_coarse_result_rejected(tmp_path):
    responses.get(URL, json=[{"lat": "51", "lon": "10", "place_rank": 4}])  # country level
    g, _ = _geocoder(tmp_path)
    assert g.geocode(Q)[1] == "too_coarse"


@responses.activate
def test_rate_limit_enforced_between_calls(tmp_path):
    responses.get(URL, json=[])
    g, sleeps = _geocoder(tmp_path)
    g.geocode(GeocodeQuery("city", "DE", city="A"))
    g.geocode(GeocodeQuery("city", "DE", city="B"))
    assert sleeps and sleeps[0] > 0


def test_offline_never_calls_network(tmp_path):
    g, _ = _geocoder(tmp_path)
    g.offline = True
    with responses.RequestsMock() as rsps:  # any request would raise ConnectionError
        assert g.geocode(Q) == (None, "offline_miss")
        assert len(rsps.calls) == 0


def test_accuracy_classes_are_monotonic():
    radii = [accuracy_from_rank(r)[1] for r in (30, 26, 22, 18, 14)]
    assert radii == sorted(radii)


@responses.activate
@pytest.mark.parametrize(
    "city,rank,atype",
    [("Frankfurt", 12, "city"), ("Berlin", 8, "city"), ("Hamburg", 8, "city")],
)
def test_big_cities_with_low_admin_rank_are_accepted(tmp_path, city, rank, atype):
    # Nominatim place_rank: German district-free cities are rank 12, city-states rank 8
    responses.get(URL, json=[{"lat": "50.1", "lon": "8.6", "place_rank": rank, "addresstype": atype}])
    g, _ = _geocoder(tmp_path)
    res, outcome = g.geocode(GeocodeQuery("city", "DE", city=city))
    assert outcome == "network" and res.accuracy == "city" and res.accuracy_m == 15_000


@responses.activate
@pytest.mark.parametrize("rank,atype", [(8, "state"), (4, "country"), (14, "county")])
def test_admin_areas_are_refused_whatever_the_rank(tmp_path, rank, atype):
    responses.get(URL, json=[{"lat": "51", "lon": "10", "place_rank": rank, "addresstype": atype}])
    g, _ = _geocoder(tmp_path)
    assert g.geocode(Q)[1] == "too_coarse"


@responses.activate
def test_cache_entry_without_addresstype_is_refreshed_online(tmp_path):
    g, _ = _geocoder(tmp_path)
    g.cache.put(Q.key(), {"lat": 51.3, "lon": 9.5, "place_rank": 12})  # old-format entry
    responses.get(URL, json=[{"lat": "51.31", "lon": "9.49", "place_rank": 12, "addresstype": "city"}])
    res, outcome = g.geocode(Q)
    assert outcome == "network" and res is not None
    assert g.cache.entries[Q.key()]["addresstype"] == "city"
