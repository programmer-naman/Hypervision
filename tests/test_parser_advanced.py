"""Tests for upgraded tokenizer and SQL parser."""
import unittest
import _bootstrap  # noqa: F401
from mini_dbms.parser import (
    parse, ParseError, ParsedQuery, InsertStatement, UpdateStatement,
    DeleteStatement, CreateIndexStatement, DropIndexStatement,
)


class TestSQLParserAdvanced(unittest.TestCase):
    def test_quoted_string_containing_and_or(self):
        sql = "SELECT department, salary FROM sales WHERE department = 'Research AND Development OR Ops' AND salary > 50000"
        query = parse(sql)
        self.assertIsInstance(query, ParsedQuery)
        self.assertEqual(query.table, "sales")
        self.assertEqual(len(query.where), 2)
        self.assertEqual(query.where[0], ("department", "=", "Research AND Development OR Ops"))
        self.assertEqual(query.where[1], ("salary", ">", 50000))

    def test_or_and_parentheses(self):
        sql = "SELECT * FROM sales WHERE (region = 'N' OR region = 'S') AND salary >= 75000"
        query = parse(sql)
        self.assertIsNotNone(query.where_expr)
        # Evaluates row
        self.assertTrue(query.where_expr.evaluate({"region": "N", "salary": 80000}))
        self.assertFalse(query.where_expr.evaluate({"region": "E", "salary": 80000}))
        self.assertFalse(query.where_expr.evaluate({"region": "N", "salary": 50000}))

    def test_aliases_and_distinct(self):
        sql = "SELECT DISTINCT s.department AS dept, AVG(s.salary) AS avg_sal FROM sales AS s GROUP BY dept"
        query = parse(sql)
        self.assertTrue(query.distinct)
        self.assertEqual(query.table, "sales")
        self.assertEqual(query.table_alias, "s")
        self.assertEqual(query.columns[0]["alias"], "dept")
        self.assertEqual(query.columns[1]["agg"], "AVG")
        self.assertEqual(query.columns[1]["alias"], "avg_sal")

    def test_limit_and_offset(self):
        sql = "SELECT * FROM sales ORDER BY salary DESC LIMIT 25 OFFSET 10"
        query = parse(sql)
        self.assertEqual(query.limit, 25)
        self.assertEqual(query.offset, 10)
        self.assertEqual(query.order_by, "salary DESC")

    def test_joins_with_types(self):
        sql = "SELECT sales.department, orders.amount FROM sales LEFT JOIN orders ON sales.employee_id = orders.employee_id"
        query = parse(sql)
        self.assertIsNotNone(query.join)
        self.assertEqual(query.join["type"], "LEFT")
        self.assertEqual(query.join["table"], "orders")
        self.assertEqual(query.join["left"], "sales.employee_id")
        self.assertEqual(query.join["right"], "orders.employee_id")

    def test_insert_statement(self):
        sql = "INSERT INTO sales (employee_id, department, salary) VALUES (999, 'Engineering', 85000), (1000, 'Marketing', 62000)"
        stmt = parse(sql)
        self.assertIsInstance(stmt, InsertStatement)
        self.assertEqual(stmt.table, "sales")
        self.assertEqual(stmt.columns, ["employee_id", "department", "salary"])
        self.assertEqual(len(stmt.values), 2)
        self.assertEqual(stmt.values[0], [999, "Engineering", 85000])

    def test_update_statement(self):
        sql = "UPDATE sales SET salary = 90000, region = 'E' WHERE employee_id = 999"
        stmt = parse(sql)
        self.assertIsInstance(stmt, UpdateStatement)
        self.assertEqual(stmt.table, "sales")
        self.assertEqual(stmt.assignments, {"salary": 90000, "region": "E"})
        self.assertEqual(stmt.where, [("employee_id", "=", 999)])

    def test_delete_statement(self):
        sql = "DELETE FROM sales WHERE employee_id = 999"
        stmt = parse(sql)
        self.assertIsInstance(stmt, DeleteStatement)
        self.assertEqual(stmt.table, "sales")
        self.assertEqual(stmt.where, [("employee_id", "=", 999)])

    def test_index_statements(self):
        c_stmt = parse("CREATE INDEX idx_salary ON sales (salary) USING BTREE")
        self.assertIsInstance(c_stmt, CreateIndexStatement)
        self.assertEqual(c_stmt.index_name, "idx_salary")
        self.assertEqual(c_stmt.table, "sales")
        self.assertEqual(c_stmt.column, "salary")
        self.assertEqual(c_stmt.index_type, "BTREE")

        d_stmt = parse("DROP INDEX idx_salary ON sales")
        self.assertIsInstance(d_stmt, DropIndexStatement)
        self.assertEqual(d_stmt.index_name, "idx_salary")

    def test_invalid_syntax_raises_parse_error(self):
        with self.assertRaises(ParseError):
            parse("SELECT FROM WHERE")
        with self.assertRaises(ParseError):
            parse("NOT A VALID SQL STATEMENT")
        with self.assertRaises(ParseError):
            parse("")
        for unsupported in ("BEGIN", "COMMIT", "ROLLBACK", "CREATE TRIGGER t AFTER INSERT ON orders EXECUTE SELECT 1"):
            with self.assertRaises(ParseError):
                parse(unsupported)


if __name__ == "__main__":
    unittest.main()
