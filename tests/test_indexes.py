"""Tests for Hash and B-Tree database indexes and query optimization."""
import unittest
import tempfile
import os
import _bootstrap  # noqa: F401

from mini_dbms.storage import Storage
from mini_dbms.parser import parse
from mini_dbms.executor import execute, execute_statement
from mini_dbms.indexes import HashIndex, BTreeIndex, IndexManager


class TestIndexes(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        with open(os.path.join(self.folder, "customers.csv"), "w") as f:
            f.write("id,name,city,age,score\n")
            for i in range(1, 101):
                city = "CityA" if i % 2 == 0 else "CityB"
                f.write(f"{i},User{i},{city},{20 + (i % 30)},{i * 10}\n")
        self.storage = Storage(self.folder)

    def test_hash_index_lookup_and_maintenance(self):
        table = self.storage.get_table("customers")
        idx = HashIndex("idx_city", "customers", "city")
        idx.rebuild(table.rows)

        # Lookup
        res = idx.lookup_equal("CityA")
        self.assertEqual(len(res), 50)

        # Insert maintenance
        new_row = {"id": 101, "name": "User101", "city": "CityA", "age": 25, "score": 100}
        table.insert_row(new_row, self.storage.index_manager)
        idx.insert(new_row)
        self.assertEqual(len(idx.lookup_equal("CityA")), 51)

        # Update maintenance
        updated_row = {"id": 101, "name": "User101", "city": "CityB", "age": 25, "score": 100}
        idx.update(new_row, updated_row)
        self.assertEqual(len(idx.lookup_equal("CityA")), 50)
        self.assertEqual(len(idx.lookup_equal("CityB")), 51)

        # Delete maintenance
        idx.delete(updated_row)
        self.assertEqual(len(idx.lookup_equal("CityB")), 50)

    def test_btree_index_range_lookups(self):
        table = self.storage.get_table("customers")
        idx = BTreeIndex("idx_score", "customers", "score")
        idx.rebuild(table.rows)

        # Exact equality
        eq_res = idx.lookup_equal(500)
        self.assertEqual(len(eq_res), 1)
        self.assertEqual(eq_res[0]["id"], 50)

        # Greater than
        gt_res = idx.lookup_range(">", 800)
        self.assertEqual(len(gt_res), 20)  # scores 810 to 1000

        # Greater than or equal
        gte_res = idx.lookup_range(">=", 800)
        self.assertEqual(len(gte_res), 21)

        # Less than
        lt_res = idx.lookup_range("<", 200)
        self.assertEqual(len(lt_res), 19)

        # Less than or equal
        lte_res = idx.lookup_range("<=", 200)
        self.assertEqual(len(lte_res), 20)

    def test_index_creation_and_dropping_via_sql(self):
        # CREATE INDEX
        c_res = execute_statement("CREATE INDEX idx_age ON customers (age) USING BTREE", self.storage)
        self.assertIn("created successfully", c_res.message)
        self.assertIsNotNone(self.storage.index_manager.get_index("customers", "idx_age"))

        # Query using index
        q = parse("SELECT * FROM customers WHERE age = 25")
        res = execute(q, self.storage, use_index=True)
        self.assertEqual(res.index_used, "idx_age")
        self.assertTrue(any("INDEX_SCAN customers" in s["op"] for s in res.stats.steps))

        # DROP INDEX
        d_res = execute_statement("DROP INDEX idx_age ON customers", self.storage)
        self.assertIn("dropped successfully", d_res.message)
        self.assertIsNone(self.storage.index_manager.get_index("customers", "idx_age"))

        # Query falls back to SCAN
        res_no_idx = execute(q, self.storage, use_index=True)
        self.assertIsNone(res_no_idx.index_used)
        self.assertTrue(any("SCAN customers" in s["op"] for s in res_no_idx.stats.steps))


if __name__ == "__main__":
    unittest.main()
