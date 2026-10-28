"""Shared fixtures; all data under tests/fixtures is SYNTHETIC."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from iris_peering.config import Config, GeocodingConfig, PathsConfig
from iris_peering.geocode import Geocoder

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOT = FIXTURES / "synthetic_snapshot"
CACHE = FIXTURES / "synthetic_geocode_cache.json"


@pytest.fixture
def cache_copy(tmp_path) -> Path:
    dst = tmp_path / "geocode_cache.json"
    shutil.copy(CACHE, dst)
    return dst


@pytest.fixture
def cfg(tmp_path, cache_copy) -> Config:
    return Config(
        geocoding=GeocodingConfig(cache_path=str(cache_copy)),
        paths=PathsConfig(snapshot_dir=str(tmp_path / "snaps"), report_dir=str(tmp_path / "reports")),
    )


@pytest.fixture
def offline_geocoder(cfg) -> Geocoder:
    return Geocoder(cfg.geocoding, "test-agent", offline=True)


@pytest.fixture
def snapshot_dir() -> Path:
    return SNAPSHOT


def with_db(cfg: Config, url: str, schema: str = "iris_staging_test") -> Config:
    return replace(cfg, storage=replace(cfg.storage, database_url=url, schema=schema))
