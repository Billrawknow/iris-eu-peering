"""Quality report and GeoJSON/CSV exports for a run."""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import normalize as n
from .transform import Node, TransformResult

MAX_SHARED_COORD_SHARE = 0.5

CSV_COLUMNS = [
    "node_id", "node_type", "ix_id", "ix_name", "ix_country", "fac_id", "fac_name", "node_city",
    "node_country", "cross_border", "latitude", "longitude", "native_crs", "coord_source",
    "coord_accuracy", "coord_accuracy_m", "geocode_osm_ref", "quality_flags", "source_url",
    "source_record_hash",
]


def output_digest(nodes: list[Node]) -> str:
    return n.canonical_hash([asdict(x) for x in sorted(nodes, key=lambda x: x.node_id)])


def build_report(
    result: TransformResult,
    *,
    run_id: str,
    scope: dict[str, Any],
    snapshot_digest: str | None,
    geocode_stats: dict[str, int],
    load_stats: dict[str, int] | None,
    fetch_complete: bool,
) -> dict[str, Any]:
    nodes = result.nodes
    located = [x for x in nodes if x.latitude is not None]
    coord_counter = Counter((x.latitude, x.longitude) for x in located)
    top_coord, top_n = (coord_counter.most_common(1)[0] if coord_counter else ((None, None), 0))
    share = (top_n / len(located)) if located else 0.0
    ixs = {x.ix_id for x in nodes}
    ixs_located = {x.ix_id for x in located}
    flags = Counter(f for x in nodes for f in x.quality_flags)
    reject_reasons = Counter(f"{r.entity}:{r.reason}" for r in result.rejects)

    per_country: dict[str, dict[str, int]] = {}
    for x in nodes:
        c = per_country.setdefault(x.node_country or "??", {"nodes": 0, "located": 0})
        c["nodes"] += 1
        c["located"] += x.latitude is not None

    provenance_ok = all(
        x.coord_source == "peeringdb_facility" or (x.geocode_query and x.geocode_provider)
        for x in located
    )
    bbox_violations = [
        x.node_id
        for x in located
        if n.in_country_bbox(x.latitude, x.longitude, x.node_country) is False
        and "outside_reference_extent" not in x.quality_flags
    ]
    ids = [x.node_id for x in nodes]

    gates = {
        "not_collapsed_to_single_coordinate": {
            "pass": len(located) <= 1 or (len(coord_counter) > 1 and share <= MAX_SHARED_COORD_SHARE),
            "detail": f"{len(coord_counter)} distinct coordinates for {len(located)} located nodes; "
            f"largest shared point holds {top_n} ({share:.1%}), limit {MAX_SHARED_COORD_SHARE:.0%}",
        },
        "every_location_has_provenance": {"pass": provenance_ok, "detail": "coord_source + accuracy + query/provider"},
        "unique_node_ids": {"pass": len(ids) == len(set(ids)), "detail": f"{len(ids)} nodes"},
        "coordinates_inside_country_reference": {
            "pass": not bbox_violations,
            "detail": f"{len(bbox_violations)} violations" + (f": {bbox_violations[:10]}" if bbox_violations else ""),
        },
        "fetch_complete": {"pass": fetch_complete, "detail": "all API responses had a data array"},
    }

    return {
        "run_id": run_id,
        "scope": scope,
        "snapshot_digest": snapshot_digest,
        "output_digest": output_digest(nodes),
        "counts": result.counts,
        "coverage": {
            "ix_total": len(ixs),
            "ix_with_located_node": len(ixs_located),
            "nodes_total": len(nodes),
            "nodes_located": len(located),
            "nodes_unlocated": len(nodes) - len(located),
            "location_coverage_pct": round(100 * len(located) / len(nodes), 1) if nodes else 0.0,
            "distinct_coordinates": len(coord_counter),
            "largest_shared_coordinate": {"lat": top_coord[0], "lon": top_coord[1], "nodes": top_n},
        },
        "by_node_type": dict(Counter(x.node_type for x in nodes)),
        "by_coord_source": dict(Counter(x.coord_source for x in nodes)),
        "by_coord_accuracy": dict(Counter(x.coord_accuracy for x in nodes)),
        "cross_border_nodes": sum(x.cross_border for x in nodes),
        "per_country": dict(sorted(per_country.items())),
        "quality_flags": dict(sorted(flags.items())),
        "rejects_by_reason": dict(sorted(reject_reasons.items())),
        "rejects": [
            {"entity": r.entity, "source_id": r.source_id, "reason": r.reason} for r in result.rejects
        ],
        "geocoding": geocode_stats,
        "load": load_stats,
        "acceptance_gates": gates,
        "all_gates_pass": all(g["pass"] for g in gates.values()),
    }


def render_markdown(rep: dict[str, Any]) -> str:
    cov = rep["coverage"]
    lines = [
        f"# PeeringDB IX nodes – quality report (run `{rep['run_id']}`)",
        "",
        f"* Scope: `{json.dumps(rep['scope'], sort_keys=True)}`",
        f"* Snapshot digest: `{rep['snapshot_digest']}`",
        f"* Output digest: `{rep['output_digest']}`",
        "",
        "## Coverage",
        "",
        "| metric | value |",
        "|---|---|",
    ]
    for k, v in cov.items():
        if isinstance(v, dict):
            v = ", ".join(f"{a}={b}" for a, b in v.items())
        lines.append(f"| {k} | {v} |")
    for title, key in (
        ("Location source", "by_coord_source"),
        ("Location accuracy", "by_coord_accuracy"),
        ("Node type", "by_node_type"),
        ("Quality flags", "quality_flags"),
        ("Rejected source records", "rejects_by_reason"),
    ):
        lines += ["", f"## {title}", "", "| value | count |", "|---|---|"]
        items = rep[key].items()
        lines += [f"| {k} | {v} |" for k, v in items] or ["| – | 0 |"]
    lines += ["", "## Per country", "", "| country | nodes | located |", "|---|---|---|"]
    lines += [f"| {c} | {v['nodes']} | {v['located']} |" for c, v in rep["per_country"].items()]
    lines += ["", "## Geocoding", "", "| outcome | count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in rep["geocoding"].items()]
    if rep.get("load"):
        lines += ["", "## Staging load", "", "| outcome | count |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in rep["load"].items()]
    lines += ["", "## Acceptance gates", "", "| gate | result | detail |", "|---|---|---|"]
    for name, g in rep["acceptance_gates"].items():
        lines.append(f"| {name} | {'PASS' if g['pass'] else 'FAIL'} | {g['detail']} |")
    lines += ["", f"**All gates pass: {rep['all_gates_pass']}**", ""]
    return "\n".join(lines)


def export_results(nodes: list[Node], out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(nodes, key=lambda x: x.node_id)
    features = []
    for x in ordered:
        props = {c: getattr(x, c) for c in CSV_COLUMNS if c not in ("latitude", "longitude")}
        geom = (
            {"type": "Point", "coordinates": [x.longitude, x.latitude]} if x.latitude is not None else None
        )
        features.append({"type": "Feature", "id": x.node_id, "geometry": geom, "properties": props})
    gj = out_dir / "peeringdb_ix_nodes.geojson"
    gj.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    cp = out_dir / "peeringdb_ix_nodes.csv"
    with cp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for x in ordered:
            row = {c: getattr(x, c) for c in CSV_COLUMNS}
            row["quality_flags"] = "|".join(x.quality_flags)
            w.writerow(row)
    return {"geojson": gj, "csv": cp}


def write_report(rep: dict[str, Any], out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jp, mp = out_dir / "quality_report.json", out_dir / "quality_report.md"
    jp.write_text(json.dumps(rep, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    mp.write_text(render_markdown(rep), encoding="utf-8")
    return {"json": jp, "markdown": mp}
