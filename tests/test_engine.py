"""Engine tests: every optimisation must return the same data as the default engine."""
import itertools
import os
import tempfile
import unittest

import _bootstrap  # noqa: F401
from mini_dbms.storage import Storage
from mini_dbms.parser import parse
from mini_dbms.executor import execute
from mini_dbms.optimizer import build_plan, split_where_conditions
from mini_dbms.settings import ExecutionSettings, parse_setting_value


def make_storage():
    folder = tempfile.mkdtemp()
    depts, regions = ["HR", "Sales", "IT"], ["N", "S"]
    with open(os.path.join(folder, "sales.csv"), "w") as f:
        f.write("employee_id,department,region,salary\n")
        for i in range(1, 301):
            f.write("%d,%s,%s,%d\n" % (i, depts[i % 3], regions[i % 2], 30000 + i * 217 % 90000))
    with open(os.path.join(folder, "orders.csv"), "w") as f:
        f.write("order_id,employee_id,amount\n")
        for i in range(1, 81):
            f.write("%d,%d,%.2f\n" % (i, (i * 7) % 300 + 1, (i * 53 % 500) + 0.25))
    return Storage(folder)


QUERIES = [
    "SELECT department, AVG(salary) FROM sales WHERE salary > 50000 GROUP BY department",
    "SELECT region, COUNT(*) FROM sales GROUP BY region",
    "SELECT sales.department, orders.amount FROM sales JOIN orders ON sales.employee_id = orders.employee_id WHERE orders.amount > 200",
    "SELECT orders.amount, sales.department FROM orders JOIN sales ON orders.employee_id = sales.employee_id WHERE sales.salary > 60000 AND orders.amount > 50",
    "SELECT department, SUM(salary), MIN(salary), MAX(salary), COUNT(salary) FROM sales GROUP BY department ORDER BY department",
    "SELECT MIN(department), MAX(region), COUNT(*) FROM sales",
    "SELECT * FROM sales WHERE region = 'N' AND salary >= 60000",
    "SELECT * FROM sales WHERE salary < 0",
]


def normalised(rows):
    return sorted(repr(sorted(r.items())) for r in rows)


class OptimisationsKeepResultsIdentical(unittest.TestCase):
    def test_all_setting_combinations(self):
        storage = make_storage()
        for sql in QUERIES:
            query = parse(sql)
            expected = normalised(execute(query, storage).rows)
            for combo in itertools.product([False, True], repeat=4):
                result = execute(query, storage, settings=ExecutionSettings(1, *combo))
                self.assertEqual(normalised(result.rows), expected, "%s %s" % (sql, combo))

    def test_threads_do_not_change_results(self):
        storage = make_storage()
        query = parse(QUERIES[0])
        expected = normalised(execute(query, storage).rows)
        for threads in (2, 4, 8):
            result = execute(query, storage, settings=ExecutionSettings(threads, True, True, True, True))
            self.assertEqual(normalised(result.rows), expected)


class PushdownAndPlan(unittest.TestCase):
    def test_split_conditions(self):
        left, right, after = split_where_conditions(
            [("salary", ">", 1), ("amount", ">", 2), ("employee_id", "=", 3), ("ghost", "=", 4)],
            ["employee_id", "salary"], ["employee_id", "amount"])
        self.assertEqual(left, [("salary", ">", 1)])
        self.assertEqual(right, [("amount", ">", 2)])
        self.assertEqual(len(after), 2)

    def test_pushdown_filters_before_join(self):
        storage = make_storage()
        query = parse(QUERIES[2])
        plain = [s["op"] for s in execute(query, storage).stats.steps]
        pushed = execute(query, storage, settings=ExecutionSettings(pushdown_filter=True)).stats.steps
        self.assertEqual(plain, ["SCAN sales", "SCAN orders", "HASH_JOIN", "FILTER", "PROJECTION"])
        ops = [s["op"] for s in pushed]
        self.assertLess(ops.index("FILTER orders"), ops.index("HASH_JOIN"))
        join = [s for s in pushed if s["op"].startswith("HASH_JOIN")][0]
        self.assertLess(join["rows_in"], 380)   # fewer rows reach the join

    def test_plan_matches_execution_order(self):
        storage = make_storage()
        query = parse(QUERIES[2])
        settings = ExecutionSettings(pushdown_filter=True)
        plan = build_plan(query, settings, storage)
        executed = [s["op"] for s in execute(query, storage, settings=settings).stats.steps]
        self.assertEqual(plan, ["SCAN sales", "SCAN orders", "FILTER orders", "HASH_JOIN", "PROJECTION"])
        self.assertEqual(plan[:4], executed[:4])

    def test_default_plan_is_unchanged(self):
        self.assertEqual(build_plan(parse(QUERIES[0])), ["SCAN sales", "FILTER", "HASH_GROUP_BY"])


class SettingsObject(unittest.TestCase):
    def test_defaults_are_the_original_engine(self):
        s = ExecutionSettings()
        self.assertEqual(s.to_dict(), {"thread_count": 1, "pushdown_filter": False,
                                       "compiled_predicates": False, "streaming_aggregation": False,
                                       "build_smaller_side": False})

    def test_with_changes_returns_new_object(self):
        base = ExecutionSettings()
        new = base.with_changes({"pushdown_filter": True, "thread_count": 4})
        self.assertFalse(base.pushdown_filter)
        self.assertTrue(new.pushdown_filter)
        self.assertEqual(new.thread_count, 4)
        self.assertEqual(set(base.diff(new)), {"pushdown_filter", "thread_count"})

    def test_unknown_setting_rejected(self):
        with self.assertRaises(ValueError):
            ExecutionSettings().with_changes({"turbo": True})

    def test_from_old_or_partial_dict(self):
        self.assertEqual(ExecutionSettings.from_dict({"thread_count": 3, "junk": 1}).thread_count, 3)
        self.assertEqual(ExecutionSettings.from_dict(None).thread_count, 1)

    def test_parse_values(self):
        self.assertTrue(parse_setting_value("pushdown_filter", "ON"))
        self.assertEqual(parse_setting_value("thread_count", "4"), 4)
        for bad in (("thread_count", "0"), ("pushdown_filter", "maybe"), ("nope", "1")):
            with self.assertRaises(ValueError):
                parse_setting_value(*bad)


if __name__ == "__main__":
    unittest.main()
