"""Read-only PeeringDB API client for ix, ixfac and fac."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

import requests

from .config import SourceConfig
from .logs import get_logger
from .snapshot import Snapshot

log = get_logger("peeringdb")

RETRY_STATUS = {429, 500, 502, 503, 504}


class PeeringDBError(RuntimeError):
    pass


class PeeringDBClient:
    def __init__(
        self,
        cfg: SourceConfig,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cfg = cfg
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": cfg.user_agent, "Accept": "application/json"})
        if cfg.api_key:
            self.session.headers["Authorization"] = f"Api-Key {cfg.api_key}"
        self._sleep = sleep

    def get(self, path: str, params: dict[str, Any]) -> tuple[str, int, Any]:
        url = f"{self.cfg.base_url.rstrip('/')}/{path.lstrip('/')}"
        last_err: str = ""
        for attempt in range(1, self.cfg.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.cfg.timeout_s)
            except requests.RequestException as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                wait = self.cfg.backoff_s * 2 ** (attempt - 1)
                log.warning("request failed, retrying", extra={"url": url, "reason": last_err})
                self._sleep(wait)
                continue
            full_url = resp.url or url
            if resp.status_code in RETRY_STATUS:
                wait = _retry_after(resp) or self.cfg.backoff_s * 2 ** (attempt - 1)
                last_err = f"HTTP {resp.status_code}"
                log.warning(
                    "retryable HTTP status",
                    extra={"url": full_url, "status": resp.status_code, "reason": f"wait {wait:.1f}s"},
                )
                self._sleep(wait)
                continue
            if resp.status_code >= 400:
                raise PeeringDBError(f"HTTP {resp.status_code} for {full_url}: {resp.text[:300]}")
            try:
                body = resp.json()
            except ValueError:
                # keep the raw text so the snapshot records what came back
                log.error("response is not JSON", extra={"url": full_url, "status": resp.status_code})
                body = {"_raw_text": resp.text[:10000]}
            return full_url, resp.status_code, body
        raise PeeringDBError(f"giving up on {url} after {self.cfg.max_retries} attempts ({last_err})")

    def fetch_scope(self) -> Snapshot:
        """Fetch IXs in scope, then their ixfac links, then the referenced facilities."""
        c = self.cfg
        scope = {
            "base_url": c.base_url,
            "countries": list(c.countries) if not c.region_continent else [],
            "region_continent": c.region_continent,
            "requested_at": _now(),
        }
        snap = Snapshot(scope=scope)

        if c.region_continent:
            self._fetch_into(snap, "ix", "ix", {"region_continent": c.region_continent})
        else:
            for country in c.countries:
                self._fetch_into(snap, "ix", "ix", {"country": country})

        ix_ids = sorted({r["id"] for r in snap.records("ix") if isinstance(r, dict) and _is_int(r.get("id"))})
        for chunk in _chunks(ix_ids, c.batch_size):
            self._fetch_into(snap, "ixfac", "ixfac", {"ix_id__in": ",".join(map(str, chunk))})

        fac_ids = sorted(
            {r["fac_id"] for r in snap.records("ixfac") if isinstance(r, dict) and _is_int(r.get("fac_id"))}
        )
        for chunk in _chunks(fac_ids, c.batch_size):
            self._fetch_into(snap, "fac", "fac", {"id__in": ",".join(map(str, chunk))})

        log.info(
            "fetch complete",
            extra={"count": {e: len(snap.records(e)) for e in ("ix", "ixfac", "fac")}},
        )
        return snap

    def _fetch_into(self, snap: Snapshot, entity: str, path: str, params: dict[str, Any]) -> None:
        url, status, body = self.get(path, params)
        n = len(body.get("data", [])) if isinstance(body, dict) and isinstance(body.get("data"), list) else None
        log.info("fetched", extra={"entity": entity, "url": url, "status": status, "count": n})
        snap.add(entity, url, _now(), status, body)


def _retry_after(resp: requests.Response) -> float | None:
    val = resp.headers.get("Retry-After")
    if not val:
        return None
    try:
        return max(0.0, float(val))
    except ValueError:
        return None


def _chunks(items: list[int], size: int) -> Iterable[list[int]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
