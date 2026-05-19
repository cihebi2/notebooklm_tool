import unittest
from types import SimpleNamespace

from app.jobs import JobConfig
from app.notebooklm_health import classify_notebooklm_error, no_task_id_failure_details


class NotebookLMHealthTests(unittest.TestCase):
    def test_auth_redirect_is_reported_as_expired(self):
        detail = classify_notebooklm_error(
            ValueError(
                "Authentication expired or invalid. Redirected to: "
                "https://accounts.google.com/v3/signin/identifier?continue=notebooklm"
            )
        )

        self.assertEqual(detail["category"], "AUTH_EXPIRED")
        self.assertTrue(detail["expired"])
        self.assertIn("重新授权", detail["hint"])

    def test_rpc_id_failure_is_reported_as_api_changed(self):
        detail = classify_notebooklm_error(RuntimeError("No result found for RPC ID: abc123"))

        self.assertEqual(detail["category"], "API_CHANGED")
        self.assertFalse(detail["expired"])
        self.assertIn("notebooklm-py", detail["hint"])

    def test_no_task_id_preserves_upstream_generation_error(self):
        status = SimpleNamespace(
            task_id="",
            status="failed",
            error="Quota exceeded for Audio Overview generation",
            error_code="USER_DISPLAYABLE_ERROR",
            metadata={"limit": "daily"},
        )

        details = no_task_id_failure_details(status)

        self.assertEqual(details["error"], "Quota exceeded for Audio Overview generation")
        self.assertEqual(details["error_code"], "USER_DISPLAYABLE_ERROR")
        self.assertEqual(details["status"], "failed")
        self.assertEqual(details["metadata"], {"limit": "daily"})
        self.assertIn("empty task id", details["local_reason"])

    def test_generation_defaults_are_conservative(self):
        cfg = JobConfig(accounts=[{"account_id": "acc-1"}])

        self.assertEqual(cfg.accounts_concurrency, 2)
        self.assertEqual(cfg.per_account_concurrency, 1)


if __name__ == "__main__":
    unittest.main()
