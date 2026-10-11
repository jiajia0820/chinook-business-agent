import unittest
from dataclasses import replace

from services.agent_api.app.knowledge.documents import ChunkRecord, spatial_neighbours


class SpatialContextTests(unittest.TestCase):
    def test_close_column_value_beats_enumeration_neighbour(self):
        def chunk(identity, box):
            return ChunkRecord(identity, "D11", "poster", "png", "V1.0", "hash", "OCR", identity, 1, box, "poster#" + identity)
        title = chunk("B1", [0, 0, 100, 20])
        unrelated = chunk("B2", [400, 0, 500, 20])
        value = chunk("B3", [0, 30, 100, 50])
        distant = chunk("B4", [0, 1000, 100, 1020])
        result = spatial_neighbours(title, [title, unrelated, value, distant])
        self.assertEqual([c.chunk_id for c in result], ["B1", "B3"])
        self.assertEqual(spatial_neighbours(title, [title, replace(value, page=2)]), [title])
