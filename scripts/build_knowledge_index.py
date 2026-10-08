"""Build/check the D01-D12 lexical index through the same runtime reader."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.agent_api.app.knowledge.index import KNOWLEDGE_ROOT, load_knowledge_index, knowledge_snapshot_version
from services.agent_api.app.profiles import ProfileRegistry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        profile = ProfileRegistry.defaults().get("chinook-music")
        index = load_knowledge_index(KNOWLEDGE_ROOT, profile)
        result = {
            "status": "checked" if args.check else "built",
            "snapshot_version": knowledge_snapshot_version(index),
            "documents": sorted({item.record.doc_id for item in index.chunks}),
            "chunk_count": len(index.chunks),
        }
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": "KNOWLEDGE_INDEX_INVALID", "message": str(exc)}, ensure_ascii=False))
        return 1
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
