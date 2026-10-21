import tempfile
import unittest

from src.state.ai_access import AIAnalysisAccessStore


class TestAIAnalysisAccessStore(unittest.TestCase):

    def test_approval_and_denial_survive_reloading_store(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/ai-access.json"
            store = AIAnalysisAccessStore(path)
            store.request("200", 200, "new_user", "New", "User")

            self.assertTrue(store.decide("200", approved=True))
            self.assertTrue(AIAnalysisAccessStore(path).is_approved("200"))

            store.request("300", 300, "other_user", "Other", "User")
            self.assertTrue(store.decide("300", approved=False))
            reloaded = AIAnalysisAccessStore(path)
            self.assertFalse(reloaded.is_approved("300"))
            self.assertEqual(reloaded.get_request("300")["status"], "rejected")

    def test_pending_request_is_not_duplicated_and_can_be_reapplied_after_rejection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AIAnalysisAccessStore(f"{temp_dir}/ai-access.json")
            self.assertEqual(
                store.request("200", 200, "new_user", "New", "User"),
                "created",
            )
            store.request("200", 200, "new_user", "New", "User")

            self.assertEqual(len(store.pending_requests()), 1)
            self.assertTrue(store.decide("200", approved=False))
            self.assertEqual(store.request("200", 200, "new_user", "New", "User"), "created")
            self.assertEqual(len(store.pending_requests()), 1)


if __name__ == "__main__":
    unittest.main()
