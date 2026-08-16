from __future__ import annotations

import unittest
from pathlib import Path

class DashboardRouteSmokeTests(unittest.TestCase):
    def test_dashboard_static_mount_and_entrypoints_exist(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")
        self.assertIn('app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard-ui")', main_source)
        self.assertIn('app.mount("/roadmap", StaticFiles(directory=ROADMAP_DIR, html=True), name="roadmap-ui")', main_source)
        self.assertIn('@app.get("/roadmap.html", include_in_schema=False)', main_source)
        self.assertIn('@app.get("/projectoverview.html", include_in_schema=False)', main_source)
        self.assertIn('@app.get("/projectoverview-data.js", include_in_schema=False)', main_source)

        expected_files = [
            Path("ui/dashboard-ui/index.html"),
            Path("ui/dashboard-ui/rag/index.html"),
            Path("ui/dashboard-ui/rag/styles.css"),
            Path("ui/dashboard-ui/rag/app.js"),
            Path("ui/dashboard-ui/vendor/chart.umd.min.js"),
            Path("docs/roadmap.html"),
            Path("docs/roadmap/phase1.html"),
            Path("docs/projectoverview.html"),
            Path("docs/projectoverview-data.js"),
        ]
        for file_path in expected_files:
            self.assertTrue(file_path.exists(), str(file_path))

    def test_dashboard_html_references_nested_assets_correctly(self) -> None:
        root_source = Path("ui/dashboard-ui/index.html").read_text(encoding="utf-8")
        rag_source = Path("ui/dashboard-ui/rag/index.html").read_text(encoding="utf-8")

        self.assertIn("./styles.css", root_source)
        self.assertIn("./app.js", root_source)

        self.assertIn("./styles.css", rag_source)
        self.assertIn("./app.js", rag_source)
        self.assertIn("../vendor/chart.umd.min.js", rag_source)


if __name__ == "__main__":
    unittest.main()
