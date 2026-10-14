"""Experiment manager + terminal console tests (use a small temporary dataset)."""
import contextlib
import io
import json
import os
import tempfile
import unittest

import _bootstrap  # noqa: F401
from test_engine import make_storage
from experiments.experiment_manager import ExperimentManager, result_fingerprint
from mini_dbms.settings import ExecutionSettings
from mini_dbms.parser import ParseError
from cli.console import HyperVisionCLI

GROUP_SQL = "SELECT department, AVG(salary) FROM sales WHERE salary > 50000 GROUP BY department"
JOIN_SQL = ("SELECT orders.amount, sales.department FROM orders JOIN sales "
            "ON orders.employee_id = sales.employee_id WHERE sales.salary > 60000")


def make_manager():
    folder = tempfile.mkdtemp()
    return ExperimentManager(make_storage(), os.path.join(folder, "logs", "experiments.json"))


class Manager(unittest.TestCase):
    def test_record_has_new_fields(self):
        r = make_manager().run_query(GROUP_SQL)
        for key in ("settings", "cpu_time_ms", "result_fingerprint", "recommendations", "recommendation"):
            self.assertIn(key, r)
        self.assertEqual(r["settings"]["thread_count"], 1)
        self.assertTrue(r["experiment_id"].startswith("EXP-"))

    def test_failed_query_does_not_burn_an_id(self):
        m = make_manager()
        m.run_query(GROUP_SQL)
        with self.assertRaises(ParseError):
            m.run_query("SELECT bogus")
        self.assertEqual(m.run_query(GROUP_SQL)["experiment_id"], "EXP-1002")

    def test_ids_continue_after_restart_without_duplicates(self):
        m = make_manager()
        for _ in range(3):
            m.run_query(GROUP_SQL)
        m2 = ExperimentManager(m.storage, m.log_path)
        self.assertEqual(m2.run_query(GROUP_SQL)["experiment_id"], "EXP-1004")

    def test_unsaved_run_has_no_id_and_is_not_logged(self):
        m = make_manager()
        r = m.run_query(GROUP_SQL, save=False)
        self.assertIsNone(r["experiment_id"])
        self.assertEqual(m.list_experiments(), [])

    def test_fingerprint_ignores_row_order(self):
        a, b = [{"x": 1}, {"x": 2}], [{"x": 2}, {"x": 1}]
        self.assertEqual(result_fingerprint(a), result_fingerprint(b))
        self.assertNotEqual(result_fingerprint(a), result_fingerprint([{"x": 1}, {"x": 3}]))

    def test_compare_settings_saves_candidate_and_matches_results(self):
        m = make_manager()
        base = m.run_query(JOIN_SQL)
        new = ExecutionSettings(pushdown_filter=True, build_smaller_side=True)
        out = m.compare_settings(base["experiment_id"], new, runs=2, applied=["x"])
        cand, comp = out["candidate"], out["comparison"]
        self.assertEqual(cand["compared_to"], base["experiment_id"])
        self.assertEqual(cand["settings"]["pushdown_filter"], True)
        self.assertTrue(comp["results_match"])
        self.assertEqual(comp["before_id"], base["experiment_id"])
        self.assertEqual(comp["after_id"], cand["experiment_id"])
        self.assertIsNotNone(comp["metrics"][2]["before"])      # heap numbers measured
        self.assertLess(comp["metrics"][2]["after"], comp["metrics"][2]["before"])
        saved = m.get_experiment(cand["experiment_id"])
        self.assertIn("comparison", saved)                       # persisted
        json.dumps(saved)

    def test_environment_change_runs_between_before_and_after_with_wider_noise_band(self):
        m = make_manager()
        base = m.run_query(GROUP_SQL)
        calls = []
        out = m.compare_settings(base["experiment_id"], ExecutionSettings(), runs=1,
                                 environment_change=lambda: calls.append("resized") or "VM resized",
                                 simulated_only=True)
        self.assertEqual(calls, ["resized"])
        self.assertEqual(out["comparison"]["runs_after"], 5)       # 1 requested, raised to the minimum
        self.assertEqual(out["comparison"]["noise_pct"], 15.0)
        self.assertEqual(out["comparison"]["environment_note"], "VM resized")
        self.assertTrue(out["comparison"]["simulated_only"])
        self.assertEqual(out["candidate"]["environment_change"], "VM resized")

    def test_engine_only_comparison_keeps_tight_noise_band(self):
        m = make_manager()
        base = m.run_query(GROUP_SQL)
        out = m.compare_settings(base["experiment_id"], ExecutionSettings(streaming_aggregation=True), runs=1)
        self.assertEqual(out["comparison"]["noise_pct"], 5.0)
        self.assertFalse(out["comparison"]["simulated_only"])

    def test_compare_settings_unknown_id(self):
        with self.assertRaises(ValueError):
            make_manager().compare_settings("EXP-9999", ExecutionSettings())

    def test_compare_saved(self):
        m = make_manager()
        a, b = m.run_query(GROUP_SQL), m.run_query(GROUP_SQL)
        comp = m.compare_saved(a["experiment_id"], b["experiment_id"])
        self.assertTrue(comp["results_match"])
        with self.assertRaises(ValueError):
            m.compare_saved("EXP-1001", "EXP-9999")

    def test_rerun_keeps_other_settings(self):
        m = make_manager()
        first = m.run_query(GROUP_SQL, settings=ExecutionSettings(streaming_aggregation=True))
        again = m.rerun(first["experiment_id"], 4)
        self.assertEqual(again["settings"]["thread_count"], 4)
        self.assertTrue(again["settings"]["streaming_aggregation"])

    def test_old_records_without_new_fields_still_load(self):
        m = make_manager()
        with open(m.log_path, "w") as f:
            json.dump([{"experiment_id": "EXP-1001", "sql": GROUP_SQL, "thread_count": 2}], f)
        m2 = ExperimentManager(m.storage, m.log_path)
        again = m2.rerun("EXP-1001", 1)
        self.assertEqual(again["experiment_id"], "EXP-1002")


class Console(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = make_storage().data_dir      # temp folder holding the small test CSVs

    def cli(self):
        folder = tempfile.mkdtemp()
        cli = HyperVisionCLI(data_dir=self.folder, log_path=os.path.join(folder, "logs", "e.json"))
        return cli

    def run_lines(self, cli, *lines):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            for line in lines:
                cli.handle(line)
        return out.getvalue()

    def test_query_shows_ranked_recommendations(self):
        cli = self.cli()
        text = self.run_lines(cli, GROUP_SQL)
        self.assertIn("Recommendations", text)
        self.assertIn("[1]", text)
        self.assertIn("apply 1", text)

    def test_apply_runs_comparison_and_changes_settings(self):
        cli = self.cli()
        cli.compare_runs = 2
        text = self.run_lines(cli, JOIN_SQL, "apply 1")
        self.assertIn("Comparison: EXP-1001", text)
        self.assertIn("Query results are identical", text)
        self.assertNotEqual(cli.settings.to_dict(), ExecutionSettings().to_dict())

    def test_apply_all_and_undo_and_reset(self):
        cli = self.cli()
        cli.compare_runs = 1
        self.run_lines(cli, JOIN_SQL, "apply all")
        applied = cli.settings.to_dict()
        self.run_lines(cli, "undo")
        self.assertEqual(cli.settings.to_dict(), ExecutionSettings().to_dict())
        self.run_lines(cli, "undo")
        self.assertEqual(cli.settings.to_dict(), applied)
        self.run_lines(cli, "reset")
        self.assertEqual(cli.settings.to_dict(), ExecutionSettings().to_dict())

    def test_apply_manual_item_explains_instead_of_applying(self):
        cli = self.cli()
        self.run_lines(cli, GROUP_SQL)
        cli.last_record["recommendations"].append({
            "id": "narrow_where", "kind": "manual", "title": "Edit the SQL: add a WHERE",
            "detail": "x", "reduces": ["CPU"], "changes": None, "experimental": False, "rank": 99})
        before = cli.settings.to_dict()
        text = self.run_lines(cli, "apply 99")
        self.assertIn("change to your SQL", text)
        self.assertEqual(cli.settings.to_dict(), before)
        self.assertEqual(len(cli.manager.list_experiments()), 1)     # nothing was rerun

    def test_bad_apply_input(self):
        cli = self.cli()
        self.assertIn("Run a query first", self.run_lines(cli, "apply 1"))
        self.run_lines(cli, GROUP_SQL)
        self.assertIn("No recommendation numbered '99'", self.run_lines(cli, "apply 99"))
        self.assertIn("Usage", self.run_lines(cli, "apply"))

    def test_set_settings_and_errors(self):
        cli = self.cli()
        self.run_lines(cli, "set pushdown_filter on", "threads 4")
        self.assertTrue(cli.settings.pushdown_filter)
        self.assertEqual(cli.settings.thread_count, 4)
        self.assertIn("Error", self.run_lines(cli, "set pushdown_filter maybe"))
        self.assertIn("Error", self.run_lines(cli, "set nonsense on"))

    def test_compare_command(self):
        cli = self.cli()
        self.run_lines(cli, GROUP_SQL, GROUP_SQL)
        text = self.run_lines(cli, "compare EXP-1001 EXP-1002")
        self.assertIn("Comparison: EXP-1001", text)
        self.assertIn("Error", self.run_lines(cli, "compare EXP-1001 EXP-9"))

    def test_history_view_txn_help(self):
        cli = self.cli()
        text = self.run_lines(cli, GROUP_SQL, "history", "view EXP-1001", "txn", "help", "tables")
        for needle in ("Experiment History", "Concurrency demo", "DEADLOCK DETECTED", "Commands", "sales"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
