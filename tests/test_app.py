from __future__ import annotations

import sqlite3
import tempfile
import unittest

from pagesense import create_app
from pagesense.services.extractor import extract_clean_text


class PageSenseAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = f"{self.tmpdir.name}/requests-test.db"
        self.app = create_app({
            "request_log_db_path": self.db_path,
            "request_logging_enabled": True,
            "request_log_api_enabled": True,
            "request_log_api_token": "secret-token",
            "public_base_url": "https://pagesense.example.com",
        })
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_home_page_renders(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("PageSense", response.get_data(as_text=True))

    def test_extract_rejects_video_platform_url(self) -> None:
        response = self.client.get("/api/extract", query_string={"url": "https://www.youtube.com/watch?v=test"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["error"], "Video platform URLs are not supported.")

    def test_extract_missing_url_returns_400(self) -> None:
        response = self.client.get("/api/extract")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "Missing 'url' parameter")

    def test_openapi_and_docs_routes_exist(self) -> None:
        openapi_response = self.client.get("/openapi.json")
        docs_response = self.client.get("/docs")

        self.assertEqual(openapi_response.status_code, 200)
        self.assertEqual(docs_response.status_code, 200)
        self.assertIn("/api/extract", openapi_response.get_json()["paths"])
        metrics = openapi_response.get_json()["components"]["schemas"]["ExtractMetrics"]["properties"]
        self.assertIn("page_count", metrics)
        self.assertIn("content_kind", metrics)
        self.assertIn("swagger-ui", docs_response.get_data(as_text=True).lower())

    def test_logs_api_requires_token(self) -> None:
        unauthorized = self.client.get("/api/logs")
        authorized = self.client.get("/api/logs", headers={"Authorization": "Bearer secret-token"})

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(authorized.status_code, 200)
        self.assertTrue(authorized.get_json()["ok"])

    def test_request_logging_writes_rows_to_sqlite(self) -> None:
        self.client.get("/", headers={"X-Forwarded-For": "203.0.113.99"})

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT source, method, path, client_ip, forwarded_for, response_status, ok
                FROM request_logs
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], "ui")
        self.assertEqual(row[1], "GET")
        self.assertEqual(row[2], "/")
        self.assertEqual(row[3], "203.0.113.99")
        self.assertEqual(row[4], "203.0.113.99")
        self.assertEqual(row[5], 200)
        self.assertEqual(row[6], 1)

    def test_cleanup_keeps_content_sidebar_but_removes_real_sidebar(self) -> None:
        html = """
        <html><body>
          <div class="content__sidebar">
            <p>Back</p>
            <p>PDF [EN]</p>
            <h1>Main title</h1>
            <p>Main body text.</p>
            <p>Related links</p>
            <p>https://example.com/doc</p>
          </div>
          <div class="sidebar">
            <p>Sidebar content</p>
            <p>Applicability</p>
          </div>
        </body></html>
        """
        text = extract_clean_text(html)
        self.assertIn("Main title", text)
        self.assertIn("Main body text.", text)
        self.assertIn("Related links", text)
        self.assertIn("https://example.com/doc", text)
        self.assertNotIn("Back", text)
        self.assertNotIn("PDF [EN]", text)
        self.assertNotIn("Sidebar content", text)
        self.assertNotIn("Applicability", text)


if __name__ == "__main__":
    unittest.main()
