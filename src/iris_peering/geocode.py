"""Cache-first Nominatim geocoding for locations without PeeringDB coordinates."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from .config import GeocodingConfig
from .logs import get_logger

log = get_logger("nominatim")

NOMINATIM_LICENCE = "Data (c) OpenStreetMap contributors, ODbL 1.0. https://osm.org/copyright"

# Nominatim place_rank: German district-free cities are rank 12, city-states rank 8
SETTLEMENT_TYPES = frozenset(
    {"city", "town", "village", "municipality", "borough", "suburb", "city_district",
     "quarter", "neighbourhood", "hamlet"}
)
COARSE_TYPES = frozenset({"country", "state", "region", "province", "county", "state_district"})


@dataclass(frozen=True)
class GeocodeQuery:
    kind: str  # "address" | "city"
    country: str  # ISO alpha-2
    city: str | None = None
    street: str | None = None
    postalcode: str | None = None
    state: str | None = None

    def key(self) -> str:
        d = {k: v for k, v in asdict(self).items() if v}
        return json.dumps(d, sort_keys=True, ensure_ascii=False)

    def params(self) -> dict[str, str]:
        p = {"countrycodes": self.country.lower(), "format": "jsonv2", "limit": "1"}
        if self.city:
            p["city"] = self.city
        if self.kind == "address":
            if self.street:
                p["street"] = self.street
            if self.postalcode:
                p["postalcode"] = self.postalcode
            if self.state:
                p["state"] = self.state
        return p


@dataclass(frozen=True)
class GeocodeResult:
    latitude: float
    longitude: float
    place_rank: int
    accuracy: str
    accuracy_m: int
    osm_ref: str | None
    display_name: str | None
    provider: str
    retrieved_at: str
    licence: str


def accuracy_from_rank(rank: int) -> tuple[str, int]:
    """Return (accuracy class, nominal radius in metres) for a place_rank."""
    if rank >= 28:
        return "building", 50
    if rank >= 26:
        return "street", 250
    if rank >= 21:
        return "neighbourhood", 1_500
    if rank >= 17:
        return "locality", 5_000
    return "city", 15_000


class GeocodeCache:
    def __init__(self, path: Path | None) -> None:
        self.path = Path(path) if path else None
        self.entries: dict[str, Any] = {}
        self.dirty = False
        if self.path and self.path.exists():
            self.entries = json.loads(self.path.read_text(encoding="utf-8")).get("entries", {})

    def get(self, key: str) -> tuple[bool, Any]:
        if key in self.entries:
            return True, self.entries[key]
        return False, None

    def put(self, key: str, value: Any) -> None:
        self.entries[key] = value
        self.dirty = True

    def save(self) -> None:
        if not self.path or not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            "_about": "Nominatim lookups made by iris-peering. Key = exact structured query. "
            "null result = provider returned nothing usable.",
            "licence": NOMINATIM_LICENCE,
            "entries": dict(sorted(self.entries.items())),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(self.path)
        self.dirty = False


class Geocoder:
    def __init__(
        self,
        cfg: GeocodingConfig,
        user_agent: str,
        offline: bool = False,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self.offline = offline or cfg.provider == "none"
        self.cache = GeocodeCache(Path(cfg.cache_path) if cfg.cache_path else None)
        self.session = session or requests.Session()
        ua = user_agent + (f" contact:{cfg.contact_email}" if cfg.contact_email else "")
        self.session.headers.update({"User-Agent": ua})
        self._sleep, self._clock = sleep, clock
        self._last_call: float | None = None
        self.stats = {"cache_hit": 0, "network": 0, "miss": 0, "offline_miss": 0, "error": 0}

    def geocode(self, q: GeocodeQuery) -> tuple[GeocodeResult | None, str]:
        """Return (result, outcome). outcome in {cache_hit, network, miss, offline_miss, error, too_coarse}."""
        key = q.key()
        hit, cached = self.cache.get(key)
        if hit and cached and "addresstype" not in cached and not self.offline:
            # old cache entry without addresstype: refresh it once
            hit = False
        if hit:
            self.stats["cache_hit"] += 1
            return self._accept(cached, q, "cache_hit")
        if self.offline:
            self.stats["offline_miss"] += 1
            return None, "offline_miss"

        self._throttle()
        url = f"{self.cfg.base_url.rstrip('/')}/search"
        try:
            resp = self.session.get(url, params=q.params(), timeout=30)
            resp.raise_for_status()
            items = resp.json()
        except (requests.RequestException, ValueError) as exc:
            self.stats["error"] += 1
            log.warning("geocode request failed", extra={"reason": str(exc), "url": url})
            return None, "error"  # not cached: transient, retry next run
        self.stats["network"] += 1
        entry = None
        if isinstance(items, list) and items:
            it = items[0]
            try:
                entry = {
                    "lat": float(it["lat"]),
                    "lon": float(it["lon"]),
                    "place_rank": int(it.get("place_rank") or 0),
                    "addresstype": it.get("addresstype"),
                    "category": it.get("category"),
                    "type": it.get("type"),
                    "osm_ref": f"{it.get('osm_type')}/{it.get('osm_id')}" if it.get("osm_id") else None,
                    "display_name": it.get("display_name"),
                    "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "provider": "nominatim",
                }
            except (KeyError, TypeError, ValueError):
                entry = None
        self.cache.put(key, entry)
        return self._accept(entry, q, "network")

    def _accept(self, entry: Any, q: GeocodeQuery, outcome: str) -> tuple[GeocodeResult | None, str]:
        if not entry:
            self.stats["miss"] += 1
            return None, "miss"
        rank = int(entry.get("place_rank") or 0)
        atype = entry.get("addresstype")
        if atype in COARSE_TYPES:
            return None, "too_coarse"
        is_settlement = atype in SETTLEMENT_TYPES
        if rank < self.cfg.min_place_rank and not is_settlement:
            return None, "too_coarse"
        acc, acc_m = accuracy_from_rank(rank)
        return (
            GeocodeResult(
                latitude=float(entry["lat"]),
                longitude=float(entry["lon"]),
                place_rank=rank,
                accuracy=acc,
                accuracy_m=acc_m,
                osm_ref=entry.get("osm_ref"),
                display_name=entry.get("display_name"),
                provider=entry.get("provider", "nominatim"),
                retrieved_at=entry.get("retrieved_at", ""),
                licence=NOMINATIM_LICENCE,
            ),
            outcome,
        )

    def _throttle(self) -> None:
        now = self._clock()
        if self._last_call is not None:
            wait = self.cfg.min_interval_s - (now - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()

    def close(self) -> None:
        self.cache.save()
