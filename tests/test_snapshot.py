import json
import shutil

import pytest

from iris_peering.snapshot import Snapshot, SnapshotError


def test_roundtrip_preserves_responses_and_digest(tmp_path, snapshot_dir):
    snap = Snapshot.read(snapshot_dir)
    out = snap.write(tmp_path / "copy")
    again = Snapshot.read(out)
    assert again.responses == snap.responses
    assert again.digest() == snap.digest()


def test_tampered_snapshot_is_refused(tmp_path, snapshot_dir):
    d = tmp_path / "s"
    shutil.copytree(snapshot_dir, d)
    data = json.loads((d / "fac.json").read_text())
    data[0]["body"]["data"][0]["latitude"] = 1.0
    (d / "fac.json").write_text(json.dumps(data))
    with pytest.raises(SnapshotError, match="checksum mismatch"):
        Snapshot.read(d)


def test_malformed_response_detected():
    snap = Snapshot(scope={})
    snap.add("ix", "u", "t", 200, {"data": [{"id": 1}]})
    snap.add("ixfac", "u2", "t", 200, {"error": "throttled"})
    assert snap.malformed_responses() == [{"entity": "ixfac", "url": "u2"}]
    assert snap.records("ixfac") == []
