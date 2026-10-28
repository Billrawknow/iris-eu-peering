"""Field normalisers and coordinate sanity checks."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from functools import lru_cache
from importlib import resources
from typing import Any

_WS = re.compile(r"\s+")
_ISO2 = re.compile(r"^[A-Z]{2}$")


def text(v: Any, max_len: int | None = None) -> str | None:
    """Trim, collapse internal whitespace, map ''/None/non-strings to None."""
    if v is None:
        return None
    if not isinstance(v, str):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            v = str(v)
        else:
            return None
    s = _WS.sub(" ", v).strip()
    if not s:
        return None
    return s[:max_len] if max_len else s


def country(v: Any) -> str | None:
    s = text(v)
    if not s:
        return None
    s = s.upper()
    return s if _ISO2.match(s) else None


def pos_int(v: Any) -> int | None:
    """Strict positive integer (rejects bools, floats with fractions, junk strings)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if v > 0 else None
    if isinstance(v, str) and v.strip().isdigit():
        n = int(v.strip())
        return n if n > 0 else None
    return None


def non_neg_int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int) and v >= 0:
        return v
    return None


def timestamp(v: Any) -> str | None:
    """Parse a PeeringDB ISO-8601 timestamp to a UTC ISO string."""
    s = text(v)
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:  # PeeringDB timestamps are UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def coord(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def canonical_hash(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def _bbox_ref() -> dict[str, Any]:
    ref = resources.files("iris_peering").joinpath("reference/country_bbox_ne50m.json")
    return json.loads(ref.read_text(encoding="utf-8"))


BBOX_TOLERANCE_DEG = 0.1  # ~10 km slack for coastline generalisation at 1:50m


def in_country_bbox(lat: float, lon: float, iso2: str | None) -> bool | None:
    """Return None when there is no reference box for the country."""
    if not iso2:
        return None
    box = _bbox_ref()["bbox"].get(iso2)
    if not box:
        return None
    t = BBOX_TOLERANCE_DEG
    return (box[0] - t) <= lon <= (box[2] + t) and (box[1] - t) <= lat <= (box[3] + t)


def partial_extent(iso2: str | None) -> bool:
    return bool(iso2) and iso2 in set(_bbox_ref().get("partial_extent", []))


def check_point(lat: float | None, lon: float | None, iso2: str | None) -> tuple[bool, list[str], bool]:
    """Validate a WGS84 point; returns (usable, flags, swap_suspected)."""
    flags: list[str] = []
    if lat is None and lon is None:
        return False, ["coordinates_missing"], False
    if lat is None or lon is None:
        return False, ["coordinates_partial"], False
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        flags.append("coordinates_out_of_range")
        swapped = -90 <= lon <= 90 and in_country_bbox(lon, lat, iso2) is True
        if swapped:
            flags.append("lat_lon_swap_suspected")
        return False, flags, swapped
    if abs(lat) < 1e-6 and abs(lon) < 1e-6:
        return False, ["coordinates_null_island"], False
    inside = in_country_bbox(lat, lon, iso2)
    if inside is None:
        flags.append("country_bbox_unavailable")
        return True, flags, False
    if inside:
        return True, flags, False
    # lat/lon swap is flagged, not auto-corrected
    if in_country_bbox(lon, lat, iso2) is True:
        return False, ["outside_country_bbox", "lat_lon_swap_suspected"], True
    if partial_extent(iso2):
        # reference box only covers the European part of this country
        return True, ["outside_reference_extent"], False
    return False, ["outside_country_bbox"], False
