"""Smoke tests for the public dashboard health endpoint."""
import os
import tempfile
import unittest

# Keep test writes out of the checked-out repository and developer data.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="bloodfang-test-data-"))

import dashboard  # noqa: E402


class DashboardHealthTests(unittest.TestCase):
    def test_health_endpoint_reports_web_and_storage(self):
        client = dashboard.app.test_client()
        response = client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")
        self.assertEqual(response.get_json()["checks"], {"web": "ok", "storage": "ok"})


if __name__ == "__main__":
    unittest.main()