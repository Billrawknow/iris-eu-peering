"""PeeringDB client tests with HTTP mocked."""

import re

import pytest
import responses

from iris_peering.client import PeeringDBClient, PeeringDBError
from iris_peering.config import SourceConfig

BASE = "https://www.peeringdb.com/api"


def _client(**kw):
    sleeps = []
    cfg = SourceConfig(base_url=BASE, max_retries=3, backoff_s=0.5, batch_size=2, **kw)
    return PeeringDBClient(cfg, sleep=sleeps.append), sleeps


@responses.activate
def test_fetch_scope_chains_ix_ixfac_fac_in_batches():
    responses.get(f"{BASE}/ix", json={"data": [{"id": 1}, {"id": 2}, {"id": 3}], "meta": {}})
    responses.get(re.compile(rf"{BASE}/ixfac\?ix_id__in=1%2C2"), json={"data": [{"id": 10, "ix_id": 1, "fac_id": 100}]})
    responses.get(re.compile(rf"{BASE}/ixfac\?ix_id__in=3"), json={"data": [{"id": 11, "ix_id": 3, "fac_id": 101}]})
    responses.get(re.compile(rf"{BASE}/fac\?id__in=100%2C101"), json={"data": [{"id": 100}, {"id": 101}]})
    client, _ = _client(countries=("DE",))
    snap = client.fetch_scope()
    assert [r["id"] for r in snap.records("ix")] == [1, 2, 3]
    assert len(snap.responses["ixfac"]) == 2  # batch_size=2 -> two requests
    assert [r["id"] for r in snap.records("fac")] == [100, 101]
    assert "country=DE" in responses.calls[0].request.url
    assert snap.scope["countries"] == ["DE"]


@responses.activate
def test_retries_429_honouring_retry_after_then_succeeds():
    responses.get(f"{BASE}/ix", status=429, headers={"Retry-After": "7"})
    responses.get(f"{BASE}/ix", json={"data": []})
    client, sleeps = _client()
    url, status, body = client.get("ix", {"country": "DE"})
    assert status == 200 and body == {"data": []}
    assert sleeps == [7.0]


@responses.activate
def test_gives_up_after_max_retries_on_5xx():
    for _ in range(3):
        responses.get(f"{BASE}/ix", status=503)
    client, sleeps = _client()
    with pytest.raises(PeeringDBError, match="giving up"):
        client.get("ix", {})
    assert sleeps == [0.5, 1.0, 2.0]  # exponential backoff


@responses.activate
def test_client_error_is_not_retried():
    responses.get(f"{BASE}/ix", status=404, body="nope")
    client, sleeps = _client()
    with pytest.raises(PeeringDBError, match="HTTP 404"):
        client.get("ix", {})
    assert sleeps == []


@responses.activate
def test_non_json_body_is_kept_for_audit():
    responses.get(f"{BASE}/ix", body="<html>maintenance</html>", status=200)
    client, _ = _client()
    _, _, body = client.get("ix", {})
    assert body == {"_raw_text": "<html>maintenance</html>"}


@responses.activate
def test_api_key_sent_as_header_never_in_url():
    responses.get(f"{BASE}/ix", json={"data": []})
    client, _ = _client(api_key="k123")
    client.get("ix", {"country": "DE"})
    req = responses.calls[0].request
    assert req.headers["Authorization"] == "Api-Key k123"
    assert "k123" not in req.url
