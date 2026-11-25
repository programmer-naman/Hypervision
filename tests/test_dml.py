"""Tests for DML statements: INSERT, UPDATE, DELETE, DISTINCT, LIMIT, OFFSET, and JOINs."""
import unittest
import tempfile
import os
import _bootstrap  # noqa: F401

from mini_dbms.storage import Storage
from mini_dbms.executor import execute_statement
from mini_dbms.parser import parse, ParseError


class TestDMLAndAdvancedSQL(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        with open(os.path.join(self.folder, "employees.csv"), "w") as f:
            f.write("id,name,dept,salary\n")
            f.write("1,Alice,HR,50000\n")
            f.write("2,Bob,IT,70000\n")
            f.write("3,Charlie,IT,80000\n")
            f.write("4,Diana,HR,55000\n")
        with open(os.path.join(self.folder, "projects.csv"), "w") as f:
            f.write("project_id,emp_id,project_name\n")
            f.write("101,2,Database\n")
            f.write("102,3,Platform\n")
            f.write("103,99,Unassigned\n")
        self.storage = Storage(self.folder)

    def test_insert_and_select(self):
        res = execute_statement(
            "INSERT INTO employees (id, name, dept, salary) VALUES (5, 'Eve', 'Finance', 65000), (6, 'Frank', 'IT', 72000)",
            self.storage
        )
        self.assertEqual(res.rows_affected, 2)

        sel = execute_statement("SELECT * FROM employees WHERE id >= 5 ORDER BY id", self.storage)
        self.assertEqual(sel.row_count, 2)
        self.assertEqual(sel.rows[0]["name"], "Eve")
        self.assertEqual(sel.rows[1]["name"], "Frank")

    def test_update_and_select(self):
        res = execute_statement("UPDATE employees SET salary = 95000 WHERE name = 'Charlie'", self.storage)
        self.assertEqual(res.rows_affected, 1)

        sel = execute_statement("SELECT salary FROM employees WHERE name = 'Charlie'", self.storage)
        self.assertEqual(sel.rows[0]["salary"], 95000)

    def test_delete_and_select(self):
        res = execute_statement("DELETE FROM employees WHERE dept = 'HR'", self.storage)
        self.assertEqual(res.rows_affected, 2)

        sel = execute_statement("SELECT * FROM employees", self.storage)
        self.assertEqual(sel.row_count, 2)
        self.assertTrue(all(r["dept"] == "IT" for r in sel.rows))

    def test_distinct(self):
        sel = execute_statement("SELECT DISTINCT dept FROM employees", self.storage)
        self.assertEqual(sel.row_count, 2)
        depts = {r["dept"] for r in sel.rows}
        self.assertEqual(depts, {"HR", "IT"})

    def test_limit_and_offset(self):
        sel = execute_statement("SELECT * FROM employees ORDER BY id LIMIT 2 OFFSET 1", self.storage)
        self.assertEqual(sel.row_count, 2)
        self.assertEqual(sel.rows[0]["id"], 2)
        self.assertEqual(sel.rows[1]["id"], 3)

    def test_left_and_right_joins(self):
        # LEFT JOIN: all employees, matching projects
        left_res = execute_statement(
            "SELECT employees.name, projects.project_name FROM employees LEFT JOIN projects ON employees.id = projects.emp_id",
            self.storage
        )
        self.assertEqual(left_res.row_count, 4)
        alice_row = [r for r in left_res.rows if r["name"] == "Alice"][0]
        self.assertIsNone(alice_row.get("project_name"))

        # RIGHT JOIN: all projects, matching employees
        right_res = execute_statement(
            "SELECT employees.name, projects.project_name FROM employees RIGHT JOIN projects ON employees.id = projects.emp_id",
            self.storage
        )
        self.assertEqual(right_res.row_count, 3)
        unassigned = [r for r in right_res.rows if r.get("project_name") == "Unassigned"][0]
        self.assertIsNone(unassigned.get("name"))

    def test_aggregates_and_having(self):
        res = execute_statement(
            "SELECT dept, AVG(salary) AS avg_sal, COUNT(*) AS emp_cnt FROM employees GROUP BY dept HAVING avg_sal > 60000",
            self.storage
        )
        self.assertEqual(res.row_count, 1)
        self.assertEqual(res.rows[0]["dept"], "IT")


if __name__ == "__main__":
    unittest.main()
