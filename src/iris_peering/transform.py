"""Build validated IX node records from a raw PeeringDB snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import normalize as n
from .geocode import GeocodeQuery, Geocoder
from .logs import get_logger
from .snapshot import Snapshot

log = get_logger("transform")

PEERINGDB_LICENCE = (
    "PeeringDB data, subject to the PeeringDB Acceptable Use Policy "
    "(https://www.peeringdb.com/aup)"
)
FACILITY_REPORTED_ACCURACY_M = 100  # nominal: operator-entered, not verified by PeeringDB
NATIVE_CRS = "EPSG:4326"


@dataclass
class Reject:
    entity: str
    source_id: Any
    reason: str
    raw: Any

    def as_dict(self) -> dict[str, Any]:
        return {"entity": self.entity, "source_id": self.source_id, "reason": self.reason, "raw": self.raw}


@dataclass
class Node:
    node_id: str
    node_type: str
    ix_id: int
    ix_name: str
    ix_name_long: str | None
    ix_aka: str | None
    ix_city: str | None
    ix_country: str
    ix_region_continent: str | None
    ix_website: str | None
    ix_net_count: int | None
    ix_fac_count: int | None
    ix_created_at: str | None
    ix_updated_at: str | None
    ixfac_id: int | None = None
    ixfac_updated_at: str | None = None
    fac_id: int | None = None
    fac_name: str | None = None
    fac_org_name: str | None = None
    fac_address1: str | None = None
    fac_address2: str | None = None
    fac_city: str | None = None
    fac_state: str | None = None
    fac_zipcode: str | None = None
    fac_country: str | None = None
    fac_clli: str | None = None
    fac_updated_at: str | None = None
    node_city: str | None = None
    node_country: str | None = None
    cross_border: bool = False
    latitude: float | None = None
    longitude: float | None = None
    native_crs: str = NATIVE_CRS
    coord_source: str = "none"
    coord_accuracy: str = "unknown"
    coord_accuracy_m: int | None = None
    source_latitude: float | None = None
    source_longitude: float | None = None
    geocode_query: str | None = None
    geocode_provider: str | None = None
    geocode_osm_ref: str | None = None
    geocode_display_name: str | None = None
    geocode_retrieved_at: str | None = None
    geocode_licence: str | None = None
    quality_flags: list[str] = field(default_factory=list)
    source_name: str = "PeeringDB"
    source_url: str | None = None
    source_licence: str = PEERINGDB_LICENCE
    source_record_hash: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def flag(self, *flags: str) -> None:
        for f in flags:
            if f not in self.quality_flags:
                self.quality_flags.append(f)


@dataclass
class TransformResult:
    nodes: list[Node]
    rejects: list[Reject]
    counts: dict[str, Any]


def _parse_entities(snap: Snapshot, rejects: list[Reject]) -> dict[str, dict[int, dict]]:
    for bad in snap.malformed_responses():
        rejects.append(Reject(bad["entity"], None, "malformed_response_no_data_array", bad))

    required = {
        "ix": ("name", "country"),
        "ixfac": ("ix_id", "fac_id"),
        "fac": ("name", "country"),
    }
    out: dict[str, dict[int, dict]] = {}
    for entity in ("ix", "ixfac", "fac"):
        kept: dict[int, dict] = {}
        for raw in snap.records(entity):
            if not isinstance(raw, dict):
                rejects.append(Reject(entity, None, "not_an_object", raw))
                continue
            rid = n.pos_int(raw.get("id"))
            if rid is None:
                rejects.append(Reject(entity, raw.get("id"), "missing_or_invalid_id", raw))
                continue
            status = raw.get("status", "ok")
            if status != "ok":
                rejects.append(Reject(entity, rid, f"status_{status}", raw))
                continue
            missing = [
                f
                for f in required[entity]
                if (n.pos_int(raw.get(f)) if f.endswith("_id") else n.text(raw.get(f))) is None
            ]
            if missing:
                rejects.append(Reject(entity, rid, "missing_required:" + ",".join(missing), raw))
                continue
            if entity in ("ix", "fac") and n.country(raw.get("country")) is None:
                rejects.append(Reject(entity, rid, "invalid_country_code", raw))
                continue
            if rid in kept:
                winner, loser, reason = _resolve_duplicate(kept[rid], raw)
                kept[rid] = winner
                rejects.append(Reject(entity, rid, reason, loser))
                log.warning("duplicate record", extra={"entity": entity, "source_id": f"{entity}:{rid}", "reason": reason})
                continue
            kept[rid] = raw
        out[entity] = kept
    return out


def _resolve_duplicate(a: dict, b: dict) -> tuple[dict, dict, str]:
    """Identical keeps the first; otherwise newest ``updated`` wins, ties broken by hash."""
    if n.canonical_hash(a) == n.canonical_hash(b):
        return a, b, "duplicate_identical"
    ka = (n.timestamp(a.get("updated")) or "", n.canonical_hash(a))
    kb = (n.timestamp(b.get("updated")) or "", n.canonical_hash(b))
    return (a, b, "duplicate_superseded") if ka >= kb else (b, a, "duplicate_superseded")


def build_nodes(
    snap: Snapshot,
    geocoder: Geocoder | None,
    scope_countries: tuple[str, ...] = (),
) -> TransformResult:
    rejects: list[Reject] = []
    ents = _parse_entities(snap, rejects)
    ixs, ixfacs, facs = ents["ix"], ents["ixfac"], ents["fac"]
    src_base = snap.scope.get("base_url", "https://www.peeringdb.com/api").rstrip("/")

    links_by_pair: dict[tuple[int, int], dict] = {}
    for fid in sorted(ixfacs):
        link = ixfacs[fid]
        ix_id, fac_id = n.pos_int(link["ix_id"]), n.pos_int(link["fac_id"])
        if ix_id not in ixs:
            rejects.append(Reject("ixfac", fid, "orphan_unknown_ix", link))
            continue
        pair = (ix_id, fac_id)
        if pair in links_by_pair:  # ids are sorted, so the lower ixfac id wins
            rejects.append(Reject("ixfac", fid, "duplicate_ix_fac_pair", link))
            continue
        links_by_pair[pair] = link

    links_by_ix: dict[int, list[dict]] = {}
    for (ix_id, _), link in sorted(links_by_pair.items()):
        links_by_ix.setdefault(ix_id, []).append(link)

    nodes: list[Node] = []
    for ix_id in sorted(ixs):
        ix = ixs[ix_id]
        links = links_by_ix.get(ix_id, [])
        if not links:
            node = _base_node(ix, f"ix:{ix_id}", "ix_city", f"{src_base}/ix/{ix_id}")
            node.raw = {"ix": ix}
            node.node_city, node.node_country = node.ix_city, node.ix_country
            node.flag("no_facility_link")
            nodes.append(node)
            continue
        for link in links:
            fac = facs.get(n.pos_int(link["fac_id"]))
            node = _base_node(ix, f"ixfac:{link['id']}", "ix_facility", f"{src_base}/ixfac/{link['id']}")
            node.ixfac_id = link["id"]
            node.ixfac_updated_at = n.timestamp(link.get("updated"))
            node.fac_id = n.pos_int(link["fac_id"])
            node.raw = {"ix": ix, "ixfac": link}
            if fac is not None:
                node.raw["fac"] = fac
                node.fac_name = n.text(fac.get("name"), 255)
                node.fac_org_name = n.text(fac.get("org_name"), 255)
                node.fac_address1 = n.text(fac.get("address1"), 255)
                node.fac_address2 = n.text(fac.get("address2"), 255)
                node.fac_city = n.text(fac.get("city"), 150)
                node.fac_state = n.text(fac.get("state"), 150)
                node.fac_zipcode = n.text(fac.get("zipcode"), 20)
                node.fac_country = n.country(fac.get("country"))
                node.fac_clli = n.text(fac.get("clli"), 20)
                node.fac_updated_at = n.timestamp(fac.get("updated"))
            else:
                # facility not returned: fall back to the fields denormalised onto the ixfac link
                node.fac_name = n.text(link.get("name"), 255)
                node.fac_city = n.text(link.get("city"), 150)
                node.fac_country = n.country(link.get("country"))
                node.flag("facility_record_missing")
            node.node_city = node.fac_city or node.ix_city
            node.node_country = node.fac_country or node.ix_country
            node.cross_border = bool(node.fac_country and node.fac_country != node.ix_country)
            if node.cross_border:
                node.flag("cross_border_facility")
            nodes.append(node)

    for node in nodes:
        if scope_countries and node.ix_country not in scope_countries:
            node.flag("ix_country_outside_requested_scope")
        _locate(node, geocoder)
        node.source_record_hash = n.canonical_hash(node.raw)

    counts = {
        "ix_received": len(snap.records("ix")),
        "ixfac_received": len(snap.records("ixfac")),
        "fac_received": len(snap.records("fac")),
        "ix_valid": len(ixs),
        "ixfac_valid": len(links_by_pair),
        "fac_valid": len(facs),
        "nodes": len(nodes),
        "rejects": len(rejects),
    }
    return TransformResult(nodes=nodes, rejects=rejects, counts=counts)


def _base_node(ix: dict, node_id: str, node_type: str, source_url: str) -> Node:
    return Node(
        node_id=node_id,
        node_type=node_type,
        ix_id=ix["id"],
        ix_name=n.text(ix.get("name"), 255) or "",
        ix_name_long=n.text(ix.get("name_long"), 255),
        ix_aka=n.text(ix.get("aka"), 255),
        ix_city=n.text(ix.get("city"), 150),
        ix_country=n.country(ix.get("country")) or "",
        ix_region_continent=n.text(ix.get("region_continent"), 50),
        ix_website=n.text(ix.get("website"), 255),
        ix_net_count=n.non_neg_int(ix.get("net_count")),
        ix_fac_count=n.non_neg_int(ix.get("fac_count")),
        ix_created_at=n.timestamp(ix.get("created")),
        ix_updated_at=n.timestamp(ix.get("updated")),
        source_url=source_url,
    )


def _locate(node: Node, geocoder: Geocoder | None) -> None:
    fac = node.raw.get("fac") or {}
    if fac:
        lat, lon = n.coord(fac.get("latitude")), n.coord(fac.get("longitude"))
        node.source_latitude, node.source_longitude = lat, lon
        if lat is not None or lon is not None:
            ok, flags, _ = n.check_point(lat, lon, node.node_country)
            node.flag(*flags)
            if ok:
                node.latitude, node.longitude = round(lat, 7), round(lon, 7)
                node.coord_source = "peeringdb_facility"
                node.coord_accuracy = "facility_reported"
                node.coord_accuracy_m = FACILITY_REPORTED_ACCURACY_M
                return
            node.flag("source_coordinates_rejected")
        else:
            node.flag("source_coordinates_missing")

    if not node.node_country:
        node.flag("unlocated", "no_country_for_geocoding")
        return
    if geocoder is None:
        node.flag("unlocated", "geocoding_disabled")
        return

    queries: list[tuple[str, GeocodeQuery]] = []
    if node.fac_address1 and node.node_city:
        queries.append(
            (
                "geocoded_address",
                GeocodeQuery(
                    kind="address",
                    country=node.node_country,
                    city=node.node_city,
                    street=node.fac_address1,
                    postalcode=node.fac_zipcode,
                ),
            )
        )
    if node.node_city:
        queries.append(("geocoded_city", GeocodeQuery(kind="city", country=node.node_country, city=node.node_city)))
    if not queries:
        node.flag("unlocated", "no_city_for_geocoding")
        return

    for source, q in queries:
        res, outcome = geocoder.geocode(q)
        if res is None:
            node.flag(f"geocode_{outcome}:{q.kind}")
            continue
        ok, flags, _ = n.check_point(res.latitude, res.longitude, node.node_country)
        if not ok:
            node.flag(f"geocode_rejected:{q.kind}", *flags)
            continue
        node.latitude, node.longitude = round(res.latitude, 7), round(res.longitude, 7)
        node.coord_source = source
        # a city-only query is never better than city level
        node.coord_accuracy = res.accuracy if q.kind == "address" else "city"
        node.coord_accuracy_m = res.accuracy_m if q.kind == "address" else max(res.accuracy_m, 5_000)
        node.geocode_query = q.key()
        node.geocode_provider = res.provider
        node.geocode_osm_ref = res.osm_ref
        node.geocode_display_name = res.display_name
        node.geocode_retrieved_at = res.retrieved_at
        node.geocode_licence = res.licence
        node.flag("geocoded")
        return
    node.flag("unlocated")
