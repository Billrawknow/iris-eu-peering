"""Build the country bounding-box reference from Natural Earth 1:50m Admin-0 countries."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

# min_lon, min_lat, max_lon, max_lat; keeps overseas territories out of e.g. the FR/NL boxes
EURO_WINDOW = (-32.0, 27.0, 45.0, 82.0)
EXTRA_CODES = {"CY", "TR", "GE", "AM", "AZ", "IL"}  # neighbours worth checking
NE_COMMIT = "ca96624a56bd078437bca8184e78163e5039ad19"  # natural-earth-vector HEAD used


def _ring_bbox(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _polygons(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    return []


def main(src: str, dst: str) -> None:
    raw = Path(src).read_bytes()
    data = json.loads(raw)
    out: dict[str, list[float]] = {}
    partial: set[str] = set()
    for feat in data["features"]:
        props = feat["properties"]
        code = props.get("ISO_A2_EH") or props.get("ISO_A2")
        if not code or code == "-99":
            continue
        if props.get("CONTINENT") != "Europe" and code not in EXTRA_CODES:
            continue
        boxes = []
        for poly in _polygons(feat["geometry"]):
            b = _ring_bbox(poly[0])
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            w = EURO_WINDOW
            if w[0] <= cx <= w[2] and w[1] <= cy <= w[3]:
                boxes.append(b)
                if b[0] < w[0] or b[1] < w[1] or b[2] > w[2] or b[3] > w[3]:
                    partial.add(code)
            else:
                partial.add(code)
        if not boxes:
            continue
        bbox = [
            round(min(b[0] for b in boxes), 4),
            round(min(b[1] for b in boxes), 4),
            round(max(b[2] for b in boxes), 4),
            round(max(b[3] for b in boxes), 4),
        ]
        if code in out:  # merge duplicate features of one ISO code
            o = out[code]
            bbox = [min(o[0], bbox[0]), min(o[1], bbox[1]), max(o[2], bbox[2]), max(o[3], bbox[3])]
        out[code] = bbox
    doc = {
        "_provenance": {
            "source": "Natural Earth 1:50m Admin-0 Countries (public domain)",
            "url": "https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_50m_admin_0_countries.geojson",
            "source_commit": NE_COMMIT,
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "crs": "EPSG:4326",
            "bbox_order": "min_lon, min_lat, max_lon, max_lat",
            "method": "bbox of polygons whose centre lies in lon -32..45 / lat 27..82; "
            "coarse sanity check only, not a border test",
            "generator": "scripts/build_country_bbox.py",
        },
        "partial_extent": sorted(partial),
        "bbox": dict(sorted(out.items())),
    }
    Path(dst).write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(out)} country boxes to {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
