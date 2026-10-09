"""Verify the raw, server-owned D01-D12 knowledge snapshot."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


def verify(root: Path) -> list[dict]:
    manifest = json.loads((root / "source-manifest.json").read_text(encoding="utf-8"))
    records = manifest.get("documents", [])
    expected = {f"D{i:02}" for i in range(1, 13)}
    actual = {record.get("doc_id") for record in records}
    if actual != expected or len(records) != 12:
        raise ValueError("manifest must contain exactly D01-D12")
    result = []
    for record in records:
        path = (root / record["path"]).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError(f"{record['doc_id']}: path outside knowledge root") from exc
        if not path.is_file():
            raise ValueError(f"{record['doc_id']}: file missing")
        raw = path.read_bytes()
        if len(raw) != record["size_bytes"] or hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise ValueError(f"{record['doc_id']}: hash or size mismatch")
        result.append({"doc_id": record["doc_id"], "status": "verified", "doc_type": record["doc_type"]})
    return result


def main() -> int:
    root = Path(__file__).resolve().parents[1] / "data" / "knowledge"
    try:
        result = verify(root)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error": "KNOWLEDGE_SNAPSHOT_INVALID", "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "verified", "documents": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
