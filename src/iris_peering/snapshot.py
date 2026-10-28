"""Raw API response snapshots, written to disk before any transformation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ENTITIES = ("ix", "ixfac", "fac")


class SnapshotError(RuntimeError):
    pass


@dataclass
class Snapshot:
    scope: dict[str, Any]
    responses: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {e: [] for e in ENTITIES}
    )
    manifest: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    def add(self, entity: str, url: str, fetched_at: str, http_status: int, body: Any) -> None:
        self.responses[entity].append(
            {"url": url, "fetched_at": fetched_at, "http_status": http_status, "body": body}
        )

    def records(self, entity: str) -> list[Any]:
        """Flatten the data arrays for one entity in fetch order, without validating items."""
        out: list[Any] = []
        for resp in self.responses[entity]:
            body = resp.get("body")
            data = body.get("data") if isinstance(body, dict) else None
            if isinstance(data, list):
                out.extend(data)
        return out

    def malformed_responses(self) -> list[dict[str, Any]]:
        bad = []
        for entity in ENTITIES:
            for resp in self.responses[entity]:
                body = resp.get("body")
                if not (isinstance(body, dict) and isinstance(body.get("data"), list)):
                    bad.append({"entity": entity, "url": resp.get("url")})
        return bad

    def write(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        files = {}
        for entity in ENTITIES:
            p = directory / f"{entity}.json"
            blob = json.dumps(self.responses[entity], ensure_ascii=False, indent=1, sort_keys=True)
            p.write_text(blob + "\n", encoding="utf-8")
            files[p.name] = _sha256(p)
        self.manifest = {"scope": self.scope, "files": files}
        (directory / "manifest.json").write_text(
            json.dumps(self.manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.path = directory
        return directory

    @classmethod
    def read(cls, directory: Path, verify: bool = True) -> "Snapshot":
        directory = Path(directory)
        mpath = directory / "manifest.json"
        if not mpath.exists():
            raise SnapshotError(f"no manifest.json in {directory}")
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
        snap = cls(scope=manifest.get("scope", {}), manifest=manifest, path=directory)
        for entity in ENTITIES:
            p = directory / f"{entity}.json"
            if not p.exists():
                raise SnapshotError(f"missing {p.name} in {directory}")
            if verify:
                expected = manifest.get("files", {}).get(p.name)
                actual = _sha256(p)
                if expected != actual:
                    raise SnapshotError(
                        f"checksum mismatch for {p}: manifest {expected}, file {actual}"
                    )
            snap.responses[entity] = json.loads(p.read_text(encoding="utf-8"))
        return snap

    def digest(self) -> str:
        """sha256 over the per-file digests."""
        files = self.manifest.get("files") or {}
        h = hashlib.sha256()
        for name in sorted(files):
            h.update(f"{name}:{files[name]}\n".encode())
        return h.hexdigest()


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()
