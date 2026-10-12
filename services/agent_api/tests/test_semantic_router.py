import json
import unittest

from services.agent_api.app.agent.semantic_router import parse_route_proposal


class SemanticRouterContractTests(unittest.TestCase):
    def test_accepts_only_high_confidence_registered_route(self):
        proposal = parse_route_proposal(json.dumps({
            "route": "general",
            "intent": "general_knowledge",
            "confidence": 0.91,
        }))

        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.route, "general")
        self.assertEqual(proposal.intent, "general_knowledge")

    def test_rejects_low_confidence_or_unknown_route(self):
        self.assertIsNone(parse_route_proposal(json.dumps({
            "route": "general", "intent": "general_knowledge", "confidence": 0.79,
        })))
        self.assertIsNone(parse_route_proposal(json.dumps({
            "route": "web", "intent": "web_search", "confidence": 0.99,
        })))

    def test_rejects_extra_fields_and_invalid_json(self):
        self.assertIsNone(parse_route_proposal(json.dumps({
            "route": "general", "intent": "general_knowledge", "confidence": 0.91,
            "tool": "database",
        })))
        self.assertIsNone(parse_route_proposal("not-json"))


if __name__ == "__main__":
    unittest.main()
