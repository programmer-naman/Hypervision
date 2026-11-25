"""Tests for the HyperVision Flask Web App & REST API."""
import json
import os
import sys
import unittest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from web_app import app, storage, manager


class TestWebAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()

    def test_health_endpoint(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("sales", data["tables"])

    def test_overview_endpoint(self):
        resp = self.client.get("/api/overview")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("metrics", data)
        self.assertIn("vm_status", data)

    def test_query_select(self):
        resp = self.client.post("/api/query", json={
            "sql": "SELECT department, AVG(salary) FROM sales WHERE salary > 50000 GROUP BY department"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["statement_type"], "SELECT")
        self.assertIn("rows", data)
        self.assertGreater(data["row_count"], 0)
        self.assertIn("plan", data)
        self.assertIn("classification", data)

    def test_query_insert_update_delete(self):
        # Clean up any leftover test row
        self.client.post("/api/query", json={"sql": "DELETE FROM sales WHERE employee_id = 88888"})

        # 1. INSERT
        ins_resp = self.client.post("/api/query", json={
            "sql": "INSERT INTO sales (employee_id, department, region, salary) VALUES (88888, 'Research', 'Global', 125000)"
        })
        self.assertEqual(ins_resp.status_code, 200)
        ins_data = ins_resp.get_json()
        self.assertEqual(ins_data["rows_affected"], 1)

        # 2. UPDATE
        upd_resp = self.client.post("/api/query", json={
            "sql": "UPDATE sales SET salary = 135000 WHERE employee_id = 88888"
        })
        self.assertEqual(upd_resp.status_code, 200)
        upd_data = upd_resp.get_json()
        self.assertEqual(upd_data["rows_affected"], 1)

        # Verify update
        sel_resp = self.client.post("/api/query", json={
            "sql": "SELECT salary FROM sales WHERE employee_id = 88888"
        })
        self.assertEqual(sel_resp.status_code, 200)
        sel_data = sel_resp.get_json()
        self.assertEqual(sel_data["rows"][0][0], 135000)

        # 3. DELETE
        del_resp = self.client.post("/api/query", json={
            "sql": "DELETE FROM sales WHERE employee_id = 88888"
        })
        self.assertEqual(del_resp.status_code, 200)
        del_data = del_resp.get_json()
        self.assertEqual(del_data["rows_affected"], 1)

    def test_tables_and_detail(self):
        resp = self.client.get("/api/tables")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertGreater(data["total_tables"], 0)

        detail = self.client.get("/api/tables/sales?limit=5")
        self.assertEqual(detail.status_code, 200)
        detail_data = detail.get_json()
        self.assertEqual(detail_data["name"], "sales")
        self.assertEqual(len(detail_data["rows"]), 5)

    def test_index_endpoints(self):
        # Create
        c_resp = self.client.post("/api/indexes", json={
            "table": "orders",
            "column": "amount",
            "index_type": "BTREE",
            "name": "idx_test_orders_amount"
        })
        self.assertEqual(c_resp.status_code, 200)

        # List
        l_resp = self.client.get("/api/indexes")
        self.assertEqual(l_resp.status_code, 200)
        idx_names = [i["name"] for i in l_resp.get_json()["indexes"]]
        self.assertIn("idx_test_orders_amount", idx_names)

        # Drop
        d_resp = self.client.post("/api/indexes/drop", json={
            "name": "idx_test_orders_amount",
            "table": "orders"
        })
        self.assertEqual(d_resp.status_code, 200)

    def test_metrics_and_processes(self):
        m = self.client.get("/api/metrics")
        self.assertEqual(m.status_code, 200)
        p = self.client.get("/api/processes")
        self.assertEqual(p.status_code, 200)

    def test_workload_and_csv_export(self):
        wl = self.client.post("/api/experiments/workload", json={
            "queries": ["SELECT * FROM sales WHERE salary > 95000 LIMIT 10"],
            "iterations": 1
        })
        self.assertEqual(wl.status_code, 200)
        self.assertEqual(wl.get_json()["workload"]["queries_executed"], 1)

        exp_csv = self.client.get("/api/history/export")
        self.assertEqual(exp_csv.status_code, 200)
        self.assertIn("experiment_id", exp_csv.data.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
