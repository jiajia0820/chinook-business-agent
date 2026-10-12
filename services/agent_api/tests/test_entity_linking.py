import unittest

from services.agent_api.app.agent.entity_linking import link_entities


class EntityLinkingTests(unittest.TestCase):
    def test_rock_and_rock_and_roll_are_separate(self):
        result = link_entities("Rock，不包括 Rock And Roll", profile_id="chinook-music")
        self.assertEqual(
            [(item.entity_type, item.entity_id) for item in result.entities],
            [("genre", 1)],
        )
        self.assertEqual(result.exclusions, [("genre", 5)])
        self.assertEqual(result.entities[0].canonical_name, "Rock")

    def test_metal_aliases_keep_distinct_ids(self):
        result = link_entities("Metal 和 Heavy Metal 分开统计", profile_id="chinook-music")
        self.assertEqual([item.entity_id for item in result.entities], [3, 13])
        self.assertEqual(result.boundary_status, "separate")

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(KeyError):
            link_entities("Rock", profile_id="missing-profile")


if __name__ == "__main__":
    unittest.main()
