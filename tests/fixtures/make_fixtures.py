"""Generate the SYNTHETIC test fixtures; every record is invented, not real PeeringDB or Nominatim data."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from iris_peering.geocode import GeocodeQuery  # noqa: E402
from iris_peering.snapshot import Snapshot  # noqa: E402

BASE = "https://www.peeringdb.com/api"
T0, T1, T2 = "2024-01-15T09:00:00Z", "2025-03-01T12:30:00Z", "2026-06-30T08:00:00Z"


def ix(i, name, city, country="DE", **kw):
    d = {
        "id": i, "org_id": 100 + i, "name": name, "aka": "", "name_long": f"{name} Exchange Point",
        "city": city, "country": country, "region_continent": "Europe", "media": "Ethernet",
        "website": f"https://{name.lower()}.invalid", "net_count": 10, "fac_count": 1,
        "proto_unicast": True, "proto_ipv6": True, "status": "ok", "created": T0, "updated": T1,
    }
    d.update(kw)
    return d


def ixfac(i, ix_id, fac_id, name, city, country="DE", **kw):
    d = {"id": i, "ix_id": ix_id, "fac_id": fac_id, "name": name, "city": city, "country": country,
         "status": "ok", "created": T0, "updated": T1}
    d.update(kw)
    return d


def fac(i, name, city, lat, lon, country="DE", address1="", zipcode="", **kw):
    d = {
        "id": i, "org_id": 500 + i, "org_name": f"SYNTH Colo {i}", "name": name, "aka": "",
        "address1": address1, "address2": "", "city": city, "state": "", "zipcode": zipcode,
        "country": country, "latitude": lat, "longitude": lon, "clli": "", "website": "",
        "region_continent": "Europe", "status": "ok", "created": T0, "updated": T1,
    }
    d.update(kw)
    return d


IX = [
    ix(9001, "SYNTH-IX-FRA", "Frankfurt", fac_count=3, net_count=250),
    ix(9001, "SYNTH-IX-FRA", "Frankfurt", fac_count=3, net_count=250),            # exact duplicate
    ix(9002, "SYNTH-IX-BER", "Berlin", updated=T0, net_count=40),                 # older version...
    ix(9002, "SYNTH-IX-BER", "Berlin", updated=T2, net_count=42),                 # ...superseded by newer
    ix(9003, "SYNTH-IX-MUC", "Munich", fac_count=0),                              # no facility link
    ix(9004, "SYNTH-IX-HAM", "Hamburg"),
    ix(9005, "SYNTH-IX-DUS", "Dusseldorf", fac_count=2),
    ix(9006, "SYNTH-IX-LEJ", "Leipzig"),
    ix(9007, "SYNTH-IX-NOWHERE", "Synthville", fac_count=0),                       # not geocodable
    ix(9008, "SYNTH-IX-STR", "  Stuttgart  ", website="   "),                      # whitespace noise
    {"id": "abc", "name": "SYNTH-BAD-ID", "country": "DE", "status": "ok"},       # malformed id
    ix(9010, "", "Bremen"),                                                        # missing name
    ix(9011, "SYNTH-IX-BADCC", "Kiel", country="Germany"),                         # bad country code
    ix(9012, "SYNTH-IX-PENDING", "Bonn", status="pending"),                        # not 'ok'
    "not-an-object",                                                               # junk element
]

IXFAC = [
    ixfac(8001, 9001, 7001, "SYNTH-DC Frankfurt East", "Frankfurt"),
    ixfac(8002, 9001, 7002, "SYNTH-DC Frankfurt West", "Frankfurt"),
    ixfac(8003, 9001, 7003, "SYNTH-DC Frankfurt North", "Frankfurt"),
    ixfac(8099, 9001, 7001, "SYNTH-DC Frankfurt East", "Frankfurt"),               # duplicate pair
    ixfac(8004, 9002, 7004, "SYNTH-DC Berlin", "Berlin"),
    ixfac(8005, 9004, 7005, "SYNTH-DC Hamburg", "Hamburg"),
    ixfac(8006, 9005, 7006, "SYNTH-DC Amsterdam", "Amsterdam", country="NL"),      # cross-border
    ixfac(8007, 9005, 7999, "SYNTH-DC Dusseldorf Gone", "Dusseldorf"),             # fac not returned
    ixfac(8008, 9006, 7007, "SYNTH-DC Leipzig", "Leipzig"),
    ixfac(8009, 9008, 7008, "SYNTH-DC Stuttgart", "Stuttgart"),
    ixfac(8050, 9999, 7001, "SYNTH-DC Frankfurt East", "Frankfurt"),               # orphan (unknown ix)
    ixfac(8051, None, 7001, "SYNTH-broken link", "Frankfurt"),                     # missing ix_id
]

FAC = [
    fac(7001, "SYNTH-DC Frankfurt East", "Frankfurt", 50.1188, 8.7356),
    fac(7002, "SYNTH-DC Frankfurt West", "Frankfurt", "50.0990", "8.6420"),      # numeric strings
    fac(7003, "SYNTH-DC Frankfurt North", "Frankfurt", None, None,
        address1="Synthstrasse 1", zipcode="60001"),                              # needs address geocode
    fac(7004, "SYNTH-DC Berlin", "Berlin", 13.4050, 52.5200,
        address1="Beispielweg 2", zipcode="10001"),                               # lat/lon swapped
    fac(7005, "SYNTH-DC Hamburg", "Hamburg", 0.0, 0.0),                           # null island
    fac(7006, "SYNTH-DC Amsterdam", "Amsterdam", 52.3500, 4.9400, country="NL"),
    fac(7007, "SYNTH-DC Leipzig", "Leipzig", 51.3400, None),                      # partial coords
    fac(7008, "SYNTH-DC Stuttgart", "Stuttgart", 40.4168, -3.7038,
        address1="Musterallee 3", zipcode="70001"),                               # coords in Spain
]


def cache_entry(lat, lon, rank, ref):
    return {"lat": lat, "lon": lon, "place_rank": rank, "osm_ref": ref,
            "display_name": "SYNTHETIC FIXTURE", "retrieved_at": "2026-10-28T00:00:00+00:00",
            "provider": "synthetic-fixture"}


CACHE = {
    GeocodeQuery("address", "DE", city="Frankfurt", street="Synthstrasse 1", postalcode="60001").key():
        cache_entry(50.1302, 8.6900, 30, "synthetic/1"),
    GeocodeQuery("address", "DE", city="Berlin", street="Beispielweg 2", postalcode="10001").key():
        cache_entry(52.5070, 13.3900, 30, "synthetic/2"),
    GeocodeQuery("address", "DE", city="Stuttgart", street="Musterallee 3", postalcode="70001").key():
        None,                                                                     # provider found nothing
    GeocodeQuery("city", "DE", city="Stuttgart").key(): cache_entry(48.7758, 9.1829, 16, "synthetic/3"),
    GeocodeQuery("city", "DE", city="Munich").key(): cache_entry(48.1374, 11.5755, 16, "synthetic/4"),
    GeocodeQuery("city", "DE", city="Hamburg").key(): cache_entry(53.5511, 9.9937, 16, "synthetic/5"),
    GeocodeQuery("city", "DE", city="Dusseldorf").key(): cache_entry(51.2277, 6.7735, 16, "synthetic/6"),
    GeocodeQuery("city", "DE", city="Leipzig").key(): cache_entry(51.3397, 12.3731, 16, "synthetic/7"),
    # Synthville deliberately absent -> offline miss -> node stays unlocated.
}


def main() -> None:
    snap = Snapshot(scope={"base_url": BASE, "countries": ["DE"], "region_continent": None,
                           "requested_at": "2026-10-28T00:00:00+00:00", "synthetic": True})
    snap.add("ix", f"{BASE}/ix?country=DE", "2026-10-28T00:00:00+00:00", 200, {"data": IX, "meta": {}})
    snap.add("ixfac", f"{BASE}/ixfac?ix_id__in=9001,9002", "2026-10-28T00:00:01+00:00", 200,
             {"data": IXFAC, "meta": {}})
    snap.add("fac", f"{BASE}/fac?id__in=7001,7002", "2026-10-28T00:00:02+00:00", 200, {"data": FAC, "meta": {}})
    snap.write(HERE / "synthetic_snapshot")
    doc = {"_about": "SYNTHETIC test fixture - not Nominatim output.", "licence": "n/a (synthetic)",
           "entries": dict(sorted(CACHE.items()))}
    (HERE / "synthetic_geocode_cache.json").write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print("fixtures written")


if __name__ == "__main__":
    main()
