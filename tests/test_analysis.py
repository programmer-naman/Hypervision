"""Recommender and comparator tests."""
import unittest

import _bootstrap  # noqa: F401
from mini_dbms.settings import ExecutionSettings
from analysis.recommender import recommend, recommend_actions, RECOMMENDATIONS
from analysis import comparator


def ctx(**over):
    base = {
        "settings": ExecutionSettings(), "latency_ms": 100.0,
        "operator_stats": [
            {"op": "SCAN sales", "rows_in": 100000, "rows_out": 100000, "ms": 1.0},
            {"op": "FILTER", "rows_in": 100000, "rows_out": 80000, "ms": 10.0},
            {"op": "HASH_GROUP_BY", "rows_in": 80000, "rows_out": 6, "ms": 80.0},
        ],
        "has_where": True, "has_join": False, "has_group": True, "has_aggregate": True,
        "has_order": False, "table_rows": {"sales": 100000}, "left_table": "sales",
        "right_table": None, "pushable_conditions": 0, "where_count": 1,
    }
    base.update(over)
    return base


JOIN_CTX = dict(has_join=True, has_group=False, has_aggregate=False,
                table_rows={"orders": 10000, "sales": 100000},
                left_table="orders", right_table="sales",
                pushable_conditions=1,
                operator_stats=[{"op": "HASH_JOIN", "rows_in": 110000, "rows_out": 5000, "ms": 40.0}])


class Recommender(unittest.TestCase):
    def ids(self, classification, context):
        return [a["id"] for a in recommend_actions(classification, context)]

    def test_old_one_line_api_still_works(self):
        for name in RECOMMENDATIONS:
            self.assertEqual(recommend(name), RECOMMENDATIONS[name])
        self.assertTrue(recommend("unknown class"))

    def test_group_query_gets_streaming_and_compiled(self):
        ids = self.ids("CPU-bound", ctx())
        self.assertIn("streaming_aggregation", ids)
        self.assertIn("compiled_predicates", ids)

    def test_join_query_gets_join_actions(self):
        ids = self.ids("CPU-bound", ctx(**JOIN_CTX))
        self.assertIn("pushdown_filter", ids)
        self.assertIn("build_smaller_side", ids)

    def test_no_build_suggestion_when_right_side_is_already_smaller(self):
        c = dict(JOIN_CTX, table_rows={"orders": 100000, "sales": 10000})
        self.assertNotIn("build_smaller_side", self.ids("CPU-bound", ctx(**c)))

    def test_already_applied_actions_are_not_repeated(self):
        s = ExecutionSettings(streaming_aggregation=True, compiled_predicates=True)
        ids = self.ids("CPU-bound", ctx(settings=s))
        self.assertNotIn("streaming_aggregation", ids)
        self.assertNotIn("compiled_predicates", ids)

    def test_every_action_names_the_metrics_it_reduces(self):
        for a in recommend_actions("CPU-bound", ctx(**JOIN_CTX)):
            self.assertTrue(a["reduces"])
            self.assertTrue(set(a["reduces"]) <= {"CPU", "RAM", "Latency"})
            if a["kind"] == "setting":
                self.assertTrue(a["changes"])
            else:
                self.assertIsNone(a["changes"])

    def test_ranking_follows_the_bottleneck(self):
        mem_first = recommend_actions("Memory pressure", ctx())[0]
        self.assertIn("RAM", mem_first["reduces"])
        cpu = recommend_actions("CPU-bound", ctx(settings=ExecutionSettings(), has_group=False,
                                                 has_aggregate=False))
        self.assertIn("CPU", cpu[0]["reduces"])

    def test_saturation_puts_thread_reduction_first(self):
        c = ctx(settings=ExecutionSettings(thread_count=8))
        first = recommend_actions("Parallel/resource saturation", c)[0]
        self.assertEqual(first["id"], "reduce_threads")
        self.assertEqual(first["changes"], {"thread_count": 1})

    def test_experiment_thread_action_is_last_and_flagged(self):
        actions = recommend_actions("CPU-bound", ctx())
        exp = [a for a in actions if a["id"] == "try_threads"]
        self.assertEqual(len(exp), 1)
        self.assertTrue(exp[0]["experimental"])
        self.assertEqual(actions[-1]["id"], "try_threads")

    def test_manual_advice_for_weak_filter_and_memory(self):
        ids = self.ids("Memory pressure", ctx())
        self.assertIn("narrow_where", ids)       # filter keeps 80% of rows
        self.assertIn("reduce_result_size", ids)

    def test_ranks_are_consecutive(self):
        actions = recommend_actions("CPU-bound", ctx(**JOIN_CTX))
        self.assertEqual([a["rank"] for a in actions], list(range(1, len(actions) + 1)))

    def test_detail_uses_the_query_numbers(self):
        a = [x for x in recommend_actions("CPU-bound", ctx()) if x["id"] == "streaming_aggregation"][0]
        self.assertIn("HASH_GROUP_BY took 88%", a["detail"])


class Comparator(unittest.TestCase):
    def metrics(self, **kw):
        base = {"latency_ms": 100.0, "cpu_time_ms": 100.0, "heap_peak_mb": 10.0,
                "peak_cpu_percent": 50.0, "peak_memory_mb": 100.0,
                "result_fingerprint": "5:abc"}
        base.update(kw)
        return base

    def test_better_worse_same(self):
        c = comparator.compare(self.metrics(),
                               self.metrics(latency_ms=50.0, cpu_time_ms=103.0, heap_peak_mb=15.0))
        self.assertEqual(c["summary"], {"Latency": "better", "CPU": "same", "RAM": "worse"})
        self.assertEqual(c["overall"], "mixed")
        self.assertTrue(c["results_match"])

    def test_percentages(self):
        c = comparator.compare(self.metrics(), self.metrics(latency_ms=75.0))
        lat = [m for m in c["metrics"] if m["key"] == "latency_ms"][0]
        self.assertEqual(lat["change_pct"], -25.0)

    def test_different_results_detected(self):
        c = comparator.compare(self.metrics(), self.metrics(result_fingerprint="4:zzz"))
        self.assertFalse(c["results_match"])

    def test_missing_numbers_are_na(self):
        c = comparator.compare(self.metrics(heap_peak_mb=None), self.metrics())
        self.assertEqual(c["summary"]["RAM"], "n/a")
        self.assertIsNone(comparator.compare(self.metrics(result_fingerprint=None),
                                             self.metrics())["results_match"])

    def test_overall_verdicts(self):
        better = comparator.compare(self.metrics(), self.metrics(latency_ms=10, cpu_time_ms=10, heap_peak_mb=1))
        same = comparator.compare(self.metrics(), self.metrics())
        worse = comparator.compare(self.metrics(), self.metrics(latency_ms=300))
        self.assertEqual((better["overall"], same["overall"], worse["overall"]), ("better", "same", "worse"))

    def test_summarize_runs_uses_medians(self):
        runs = [{"latency_ms": v, "cpu_time_ms": v, "resource": {}, "row_count": 1,
                 "result_fingerprint": "x"} for v in (30.0, 10.0, 20.0)]
        s = comparator.summarize_runs(runs, heap_peak_mb=2.0)
        self.assertEqual(s["latency_ms"], 20.0)
        self.assertEqual(s["runs"], 3)


if __name__ == "__main__":
    unittest.main()
