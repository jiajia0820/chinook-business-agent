import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3] / 'data' / 'knowledge'

class SnapshotTests(unittest.TestCase):
    def test_all_twelve_documents_match_raw_bytes(self):
        manifest = json.loads((ROOT / 'source-manifest.json').read_text(encoding='utf-8'))
        self.assertEqual({d['doc_id'] for d in manifest['documents']}, {f'D{i:02}' for i in range(1,13)})
        for d in manifest['documents']:
            raw = (ROOT / d['path']).read_bytes()
            self.assertEqual(len(raw), d['size_bytes'])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), d['sha256'])

if __name__ == '__main__': unittest.main()
