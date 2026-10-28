"""YAML config loading; secrets come only from the environment."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

_ISO2 = re.compile(r"^[A-Z]{2}$")
_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SourceConfig:
    base_url: str = "https://www.peeringdb.com/api"
    countries: tuple[str, ...] = ("DE",)
    region_continent: str | None = None
    batch_size: int = 100
    timeout_s: float = 30.0
    max_retries: int = 5
    backoff_s: float = 2.0
    user_agent: str = "iris-eu-peering/0.1"
    api_key: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class GeocodingConfig:
    provider: str = "nominatim"  # nominatim | none
    base_url: str = "https://nominatim.openstreetmap.org"
    min_interval_s: float = 1.1
    contact_email: str | None = None
    cache_path: str = "data/geocode_cache.json"
    min_place_rank: int = 13


@dataclass(frozen=True)
class StorageConfig:
    schema: str = "iris_staging"
    table: str = "peeringdb_ix_nodes"
    database_url: str | None = field(default=None, repr=False)

    @property
    def qualified_table(self) -> str:
        return f"{self.schema}.{self.table}"


@dataclass(frozen=True)
class PathsConfig:
    snapshot_dir: str = "data/snapshots"
    report_dir: str = "data/reports"


@dataclass(frozen=True)
class Config:
    source: SourceConfig = field(default_factory=SourceConfig)
    geocoding: GeocodingConfig = field(default_factory=GeocodingConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)

    def with_overrides(self, **kw: Any) -> "Config":
        return replace(self, **kw)


def _section(cls, data: dict[str, Any] | None):
    data = dict(data or {})
    known = {f for f in cls.__dataclass_fields__}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown keys in {cls.__name__}: {sorted(unknown)}")
    if "countries" in data and data["countries"] is not None:
        data["countries"] = tuple(str(c).strip().upper() for c in data["countries"])
    return cls(**data)


def validate(cfg: Config) -> Config:
    s = cfg.source
    if not s.countries and not s.region_continent:
        raise ConfigError("set source.countries or source.region_continent")
    for c in s.countries:
        if not _ISO2.match(c):
            raise ConfigError(f"invalid ISO-3166 alpha-2 country code: {c!r}")
    if not 1 <= s.batch_size <= 500:
        raise ConfigError("source.batch_size must be between 1 and 500")
    if cfg.geocoding.provider not in {"nominatim", "none"}:
        raise ConfigError("geocoding.provider must be 'nominatim' or 'none'")
    st = cfg.storage
    # staging-only schema guard
    if not (_IDENT.match(st.schema) and _IDENT.match(st.table)):
        raise ConfigError("storage.schema/table must be plain lower-case SQL identifiers")
    if not st.schema.startswith("iris_staging"):
        raise ConfigError(
            f"refusing to write to schema {st.schema!r}: only iris_staging* is allowed"
        )
    return cfg


def load_config(path: str | os.PathLike | None = None, env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ if env is None else env)
    raw: dict[str, Any] = {}
    if path:
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"config file not found: {p}")
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    unknown = set(raw) - {"source", "geocoding", "storage", "paths"}
    if unknown:
        raise ConfigError(f"unknown top-level config keys: {sorted(unknown)}")

    source = _section(SourceConfig, raw.get("source"))
    geocoding = _section(GeocodingConfig, raw.get("geocoding"))
    storage = _section(StorageConfig, raw.get("storage"))
    paths = _section(PathsConfig, raw.get("paths"))

    source = replace(source, api_key=env.get("PEERINGDB_API_KEY") or None)
    geocoding = replace(
        geocoding, contact_email=env.get("NOMINATIM_EMAIL") or geocoding.contact_email
    )
    storage = replace(storage, database_url=env.get("DATABASE_URL") or None)
    return validate(Config(source=source, geocoding=geocoding, storage=storage, paths=paths))
