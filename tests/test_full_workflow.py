"""End-to-End Workflow & Integration Tests for HyperVision.

Tests the full lifecycle:
1. Executing SQL query through web API
2. Extracting bottleneck classification and recommendations
3. Applying single recommendation (e.g., worker threads or hash index)
4. Applying all compatible recommendations
5. Applying reviewed/edited SQL modification and getting before/after comparison
6. Validating before/after comparison metrics table (Latency, CPU time, Query heap peak, Peak CPU, Process RSS, Rows, Threads, vCPU, RAM)
7. VM configuration management, resource application, verification, and history tracking
8. Overview dashboard statistics aggregation
"""
import json
import os
import sys
import unittest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from web_app import app, storage, manager


class TestFullWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()

    def test_01_overview_metrics(self):
        """Test overview dashboard metrics payload."""
        resp = self.client.get("/api/overview")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        self.assertIn("successful_queries", data)
        self.assertIn("failed_queries", data)
        self.assertIn("total_experiments", data)
        self.assertIn("vm_configuration", data)
        self.assertIn("vm_status", data)
        self.assertIn("metrics", data)

    def test_02_query_execution_and_recommendations(self):
        """Execute a query and verify recommendations structure."""
        sql = "SELECT department, AVG(salary), COUNT(*) FROM sales WHERE salary > 30000 GROUP BY department ORDER BY department"
        resp = self.client.post("/api/query", json={"sql": sql})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        self.assertIn("experiment_id", data)
        self.assertIn("recommendations", data)
        self.assertIn("classification", data)

        recs = data["recommendations"]
        self.assertIsInstance(recs, list)
        if len(recs) > 0:
            rec = recs[0]
            self.assertIn("id", rec)
            self.assertIn("title", rec)
            self.assertIn("observation", rec)
            self.assertIn("reason", rec)
            self.assertIn("impact_badges", rec)
            self.assertIn("action_type", rec)

    def test_03_apply_single_recommendation(self):
        """Apply a single recommendation and verify before/after comparison."""
        sql = "SELECT * FROM sales WHERE salary > 80000"
        query_resp = self.client.post("/api/query", json={"sql": sql})
        q_data = query_resp.get_json()
        exp_id = q_data.get("experiment_id")
        recs = q_data.get("recommendations", [])

        # Pick first recommendation or use a simulated action
        action_id = recs[0]["id"] if recs else "add_hash_index"

        resp = self.client.post("/api/recommendations/apply", json={
            "experiment_id": exp_id,
            "action_id": action_id,
            "confirmed": True
        })
        self.assertEqual(resp.status_code, 200)
        comp_data = resp.get_json()
        self.assertTrue(comp_data.get("ok"))
        self.assertIn("comparison", comp_data)
        comp = comp_data["comparison"]
        self.assertIn("metrics", comp)
        self.assertIn("improvement_summary", comp)

        # Verify metrics table has required columns and metrics
        metrics = comp["metrics"]
        metric_keys = [m["metric"] for m in metrics]
        self.assertIn("latency_ms", metric_keys)
        self.assertIn("cpu_time_ms", metric_keys)
        self.assertIn("heap_peak_mb", metric_keys)
        self.assertIn("peak_cpu_percent", metric_keys)
        self.assertIn("peak_memory_mb", metric_keys)
        self.assertIn("rows_processed", metric_keys)
        self.assertIn("worker_threads", metric_keys)
        self.assertIn("vm_vcpus", metric_keys)
        self.assertIn("vm_memory_mb", metric_keys)

        # Check structure of metric items
        for m in metrics:
            self.assertIn("label", m)
            self.assertIn("before", m)
            self.assertIn("after", m)
            self.assertIn("change", m)
            self.assertIn("result", m)
            self.assertIn("verdict", m)

    def test_04_apply_all_recommendations(self):
        """Apply all compatible recommendations at once."""
        sql = "SELECT department, SUM(salary) FROM sales GROUP BY department"
        query_resp = self.client.post("/api/query", json={"sql": sql})
        q_data = query_resp.get_json()
        exp_id = q_data.get("experiment_id")

        resp = self.client.post("/api/recommendations/apply", json={
            "experiment_id": exp_id,
            "apply_all": True
        })
        self.assertEqual(resp.status_code, 200)
        comp_data = resp.get_json()
        self.assertTrue(comp_data.get("ok"))
        self.assertIn("comparison", comp_data)
        comp = comp_data["comparison"]
        self.assertIn("metrics", comp)
        self.assertIn("applied_actions", comp)

    def test_05_apply_modified_sql(self):
        """Test user review and execution of modified SQL."""
        baseline_sql = "SELECT * FROM sales"
        query_resp = self.client.post("/api/query", json={"sql": baseline_sql})
        q_data = query_resp.get_json()
        exp_id = q_data.get("experiment_id")

        # User modifies SQL with a restrictive WHERE clause and LIMIT
        modified_sql = "SELECT employee_id, department, salary FROM sales WHERE salary > 90000 LIMIT 20"
        resp = self.client.post("/api/recommendations/apply_sql", json={
            "baseline_id": exp_id,
            "modified_sql": modified_sql,
            "action_id": "review_and_edit_sql"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        self.assertIn("comparison", data)
        self.assertIn("result", data)
        self.assertIn("improvement_summary", data["comparison"])

    def test_06_vm_configuration_and_history(self):
        """Test VM resource allocation, verification, and history endpoints."""
        # 1. Apply resources
        apply_resp = self.client.post("/api/vm/apply_resources", json={
            "vcpus": 4,
            "memory_mb": 2048
        })
        self.assertEqual(apply_resp.status_code, 200)
        apply_data = apply_resp.get_json()
        self.assertTrue(apply_data.get("ok"))
        self.assertEqual(apply_data["current_configuration"]["vcpus"], 4)
        self.assertEqual(apply_data["current_configuration"]["memory_mb"], 2048)

        # 2. Verify configuration
        ver_resp = self.client.get("/api/vm/verify")
        self.assertEqual(ver_resp.status_code, 200)
        ver_data = ver_resp.get_json()
        self.assertTrue(ver_data.get("ok"))
        self.assertTrue(ver_data.get("verified"))
        self.assertEqual(ver_data["verified_configuration"]["vcpus"], 4)
        self.assertEqual(ver_data["verified_configuration"]["memory_mb"], 2048)

        # 3. History
        hist_resp = self.client.get("/api/vm/history")
        self.assertEqual(hist_resp.status_code, 200)
        hist_data = hist_resp.get_json()
        self.assertTrue(hist_data.get("ok"))
        self.assertIn("history", hist_data)
        self.assertGreater(len(hist_data["history"]), 0)


if __name__ == "__main__":
    unittest.main()
