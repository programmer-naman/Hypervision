"""
parser.py
---------
Tokenizer and Recursive-Descent SQL Parser for the HyperVision Mini DBMS.

Supports:
  - SELECT: columns, aggregates (COUNT, SUM, AVG, MIN, MAX), DISTINCT, aliases,
            FROM table [AS alias], [INNER/LEFT/RIGHT] JOIN ... ON ...,
            WHERE with AND / OR / NOT / parentheses / comparison operators,
            GROUP BY, HAVING, ORDER BY [ASC|DESC], LIMIT and OFFSET.
  - INSERT INTO table [(columns)] VALUES (...), (...)
  - UPDATE table SET col1 = val1, ... [WHERE ...]
  - DELETE FROM table [WHERE ...]
  - CREATE INDEX index_name ON table (column) [USING HASH|BTREE]
  - DROP INDEX index_name [ON table]
"""

from __future__ import annotations
import re
from typing import Any, List, Optional, Tuple, Dict, Union


class ParseError(Exception):
    """Raised when the given SQL text does not match supported SQL syntax."""
    pass


# -----------------------------------------------------------------------------
# Tokenizer / Lexer
# -----------------------------------------------------------------------------

TOKEN_KEYWORD = "KEYWORD"
TOKEN_IDENT = "IDENT"
TOKEN_STRING = "STRING"
TOKEN_NUMBER = "NUMBER"
TOKEN_OP = "OP"
TOKEN_PUNCT = "PUNCT"
TOKEN_EOF = "EOF"

KEYWORDS = {
    "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "JOIN", "INNER", "LEFT", "RIGHT",
    "ON", "GROUP", "BY", "HAVING", "ORDER", "ASC", "DESC", "LIMIT", "OFFSET",
    "DISTINCT", "AS", "INSERT", "INTO", "VALUES", "UPDATE", "SET", "DELETE",
    "CREATE", "DROP", "INDEX",
    "USING", "HASH", "BTREE", "TABLE",
    "COUNT", "SUM", "AVG", "MIN", "MAX",
    "NULL", "TRUE", "FALSE", "IS", "IN", "BETWEEN", "LIKE"
}


class Token:
    def __init__(self, kind: str, value: Any, pos: int):
        self.kind = kind
        self.value = value
        self.pos = pos

    def __repr__(self):
        return f"Token({self.kind}, {self.value!r}, pos={self.pos})"


def tokenize(sql: str) -> List[Token]:
    tokens: List[Token] = []
    i = 0
    n = len(sql)

    while i < n:
        c = sql[i]

        # Whitespace
        if c.isspace():
            i += 1
            continue

        # Comments (-- comment to end of line)
        if c == '-' and i + 1 < n and sql[i + 1] == '-':
            i += 2
            while i < n and sql[i] != '\n':
                i += 1
            continue

        # String literals ('...' or "...")
        if c in ("'", '"'):
            quote_char = c
            start = i
            i += 1
            chars = []
            while i < n:
                if sql[i] == quote_char:
                    if i + 1 < n and sql[i + 1] == quote_char:
                        chars.append(quote_char)
                        i += 2
                    else:
                        i += 1
                        break
                elif sql[i] == '\\' and i + 1 < n:
                    chars.append(sql[i + 1])
                    i += 2
                else:
                    chars.append(sql[i])
                    i += 1
            else:
                raise ParseError(f"Unterminated string literal starting at position {start}")
            tokens.append(Token(TOKEN_STRING, "".join(chars), start))
            continue

        # Numbers (integer or float, optional negative handled in expression or here)
        if c.isdigit() or (c == '.' and i + 1 < n and sql[i + 1].isdigit()):
            start = i
            is_float = False
            while i < n and (sql[i].isdigit() or sql[i] == '.'):
                if sql[i] == '.':
                    if is_float:
                        break
                    is_float = True
                i += 1
            num_str = sql[start:i]
            val = float(num_str) if is_float else int(num_str)
            tokens.append(Token(TOKEN_NUMBER, val, start))
            continue

        # Multi-character operators
        two_char = sql[i:i + 2]
        if two_char in (">=", "<=", "!=", "<>"):
            op = "!=" if two_char == "<>" else two_char
            tokens.append(Token(TOKEN_OP, op, i))
            i += 2
            continue

        # Single-character operators
        if c in ("=", ">", "<", "+", "-"):
            tokens.append(Token(TOKEN_OP, c, i))
            i += 1
            continue

        # Single-character punctuation
        if c in ("(", ")", ",", ";", "*"):
            tokens.append(Token(TOKEN_PUNCT, c, i))
            i += 1
            continue

        # Identifiers and keywords (including table.column dotted notation)
        if c.isalpha() or c == '_':
            start = i
            while i < n and (sql[i].isalnum() or sql[i] in ('_', '.')):
                i += 1
            ident_text = sql[start:i]
            upper_ident = ident_text.upper()
            if upper_ident in KEYWORDS and '.' not in ident_text:
                tokens.append(Token(TOKEN_KEYWORD, upper_ident, start))
            else:
                tokens.append(Token(TOKEN_IDENT, ident_text, start))
            continue

        raise ParseError(f"Unexpected character '{c}' at position {i}")

    tokens.append(Token(TOKEN_EOF, None, n))
    return tokens


# -----------------------------------------------------------------------------
# Expression AST Nodes for WHERE & HAVING
# -----------------------------------------------------------------------------

class ExprNode:
    def evaluate(self, row: Dict[str, Any]) -> bool:
        raise NotImplementedError


class ComparisonNode(ExprNode):
    def __init__(self, column: str, op: str, value: Any):
        self.column = column
        self.op = op
        self.value = value

    def to_tuple(self) -> Tuple[str, str, Any]:
        return (self.column, self.op, self.value)

    def evaluate(self, row: Dict[str, Any]) -> bool:
        col_name = self.column.split(".")[-1]
        val = row.get(col_name)
        if val is None and self.column in row:
            val = row[self.column]

        target = self.value
        if self.op == "=":
            return val == target
        if self.op == "!=":
            return val != target
        if val is None:
            return False
        if self.op == ">":
            return val > target
        if self.op == "<":
            return val < target
        if self.op == ">=":
            return val >= target
        if self.op == "<=":
            return val <= target
        return False

    def __repr__(self):
        return f"Comparison({self.column} {self.op} {self.value!r})"


class LogicalNode(ExprNode):
    def __init__(self, op: str, left: ExprNode, right: ExprNode):
        self.op = op.upper()
        self.left = left
        self.right = right

    def evaluate(self, row: Dict[str, Any]) -> bool:
        l = self.left.evaluate(row)
        if self.op == "AND":
            return l and self.right.evaluate(row)
        elif self.op == "OR":
            return l or self.right.evaluate(row)
        return False

    def __repr__(self):
        return f"({self.left!r} {self.op} {self.right!r})"


class NotNode(ExprNode):
    def __init__(self, operand: ExprNode):
        self.operand = operand

    def evaluate(self, row: Dict[str, Any]) -> bool:
        return not self.operand.evaluate(row)

    def __repr__(self):
        return f"(NOT {self.operand!r})"


# -----------------------------------------------------------------------------
# Statement AST Classes
# -----------------------------------------------------------------------------

class ParsedQuery:
    """Holds the pieces of a SELECT query after parsing, ready to be executed."""

    def __init__(self):
        self.statement_type = "SELECT"
        self.columns: List[Dict[str, Any]] = []    # list of {"agg": "SUM"/None, "col": "salary", "alias": "avg_sal"}
        self.distinct: bool = False
        self.table: Optional[str] = None
        self.table_alias: Optional[str] = None
        self.join: Optional[Dict[str, Any]] = None  # {"type": "INNER", "table": ..., "alias": ..., "left": ..., "right": ...}
        self.where: List[Tuple[str, str, Any]] = []  # backward-compatible list of (col, op, val)
        self.where_expr: Optional[ExprNode] = None   # rich AST node
        self.group_by: List[str] = []
        self.having: Optional[ExprNode] = None
        self.order_by: Optional[str] = None
        self.order_by_specs: List[Tuple[str, str]] = []  # [(col, "ASC"|"DESC")]
        self.limit: Optional[int] = None
        self.offset: Optional[int] = None

    def __repr__(self):
        return f"ParsedQuery(table={self.table!r}, cols={self.columns!r}, where={self.where!r})"


class InsertStatement:
    def __init__(self, table: str, columns: Optional[List[str]], values: List[List[Any]]):
        self.statement_type = "INSERT"
        self.table = table
        self.columns = columns
        self.values = values

    def __repr__(self):
        return f"InsertStatement(table={self.table!r}, cols={self.columns!r}, rows={len(self.values)})"


class UpdateStatement:
    def __init__(self, table: str, assignments: Dict[str, Any], where: Optional[ExprNode]):
        self.statement_type = "UPDATE"
        self.table = table
        self.assignments = assignments
        self.where_expr = where
        self.where: List[Tuple[str, str, Any]] = flatten_where_to_tuples(where) if where else []

    def __repr__(self):
        return f"UpdateStatement(table={self.table!r}, set={self.assignments!r})"


class DeleteStatement:
    def __init__(self, table: str, where: Optional[ExprNode]):
        self.statement_type = "DELETE"
        self.table = table
        self.where_expr = where
        self.where: List[Tuple[str, str, Any]] = flatten_where_to_tuples(where) if where else []

    def __repr__(self):
        return f"DeleteStatement(table={self.table!r}, where={self.where!r})"


class CreateIndexStatement:
    def __init__(self, index_name: str, table: str, column: str, index_type: str = "HASH"):
        self.statement_type = "CREATE_INDEX"
        self.index_name = index_name
        self.table = table
        self.column = column
        self.index_type = index_type.upper()

    def __repr__(self):
        return f"CreateIndexStatement({self.index_name} ON {self.table}({self.column}) USING {self.index_type})"


class DropIndexStatement:
    def __init__(self, index_name: str, table: Optional[str] = None):
        self.statement_type = "DROP_INDEX"
        self.index_name = index_name
        self.table = table

    def __repr__(self):
        return f"DropIndexStatement({self.index_name})"


# -----------------------------------------------------------------------------
# Parser Implementation
# -----------------------------------------------------------------------------

def flatten_where_to_tuples(expr: Optional[ExprNode]) -> List[Tuple[str, str, Any]]:
    """Helper to convert pure-AND expression trees into backward-compatible tuples."""
    if expr is None:
        return []
    if isinstance(expr, ComparisonNode):
        return [expr.to_tuple()]
    if isinstance(expr, LogicalNode) and expr.op == "AND":
        left_t = flatten_where_to_tuples(expr.left)
        right_t = flatten_where_to_tuples(expr.right)
        return left_t + right_t
    return []


class Parser:
    def __init__(self, tokens: List[Token], original_sql: str):
        self.tokens = tokens
        self.original_sql = original_sql
        self.pos = 0

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def match(self, kind: str, value: Any = None) -> bool:
        tok = self.peek()
        if tok.kind == kind:
            if value is None:
                return True
            if isinstance(value, (list, tuple, set)):
                return tok.value in value
            return tok.value == value
        return False

    def consume(self, kind: str, value: Any = None) -> Token:
        tok = self.peek()
        if not self.match(kind, value):
            expected = f"{kind}({value})" if value is not None else kind
            got = f"{tok.kind}({tok.value})"
            raise ParseError(f"Syntax error at position {tok.pos}: expected {expected}, got {got}")
        self.pos += 1
        return tok

    def try_consume(self, kind: str, value: Any = None) -> Optional[Token]:
        if self.match(kind, value):
            tok = self.peek()
            self.pos += 1
            return tok
        return None

    def parse_statement(self) -> Any:
        tok = self.peek()
        if tok.kind == TOKEN_EOF:
            raise ParseError("Query is empty.")

        if self.match(TOKEN_KEYWORD, "SELECT"):
            stmt = self.parse_select()
        elif self.match(TOKEN_KEYWORD, "INSERT"):
            stmt = self.parse_insert()
        elif self.match(TOKEN_KEYWORD, "UPDATE"):
            stmt = self.parse_update()
        elif self.match(TOKEN_KEYWORD, "DELETE"):
            stmt = self.parse_delete()
        elif self.match(TOKEN_KEYWORD, "CREATE"):
            stmt = self.parse_create()
        elif self.match(TOKEN_KEYWORD, "DROP"):
            stmt = self.parse_drop()
        else:
            raise ParseError(f"Unsupported SQL statement starting with '{tok.value}' at position {tok.pos}")

        # Optional trailing semicolon
        self.try_consume(TOKEN_PUNCT, ";")
        if not self.match(TOKEN_EOF):
            extra = self.peek()
            raise ParseError(f"Unexpected token '{extra.value}' after end of statement at position {extra.pos}")

        return stmt

    def consume_ident_or_keyword(self) -> Token:
        tok = self.peek()
        if tok.kind in (TOKEN_IDENT, TOKEN_KEYWORD):
            self.pos += 1
            return tok
        raise ParseError(f"Expected identifier at position {tok.pos}, got {tok.value!r}")

    # --- SELECT Parsing ---

    def parse_select(self) -> ParsedQuery:
        self.consume(TOKEN_KEYWORD, "SELECT")
        query = ParsedQuery()

        if self.try_consume(TOKEN_KEYWORD, "DISTINCT"):
            query.distinct = True

        # Columns
        query.columns = self.parse_select_columns()

        # FROM table
        self.consume(TOKEN_KEYWORD, "FROM")
        table_tok = self.consume_ident_or_keyword()
        query.table = table_tok.value

        # Optional table alias
        if self.match(TOKEN_KEYWORD, "AS"):
            self.consume(TOKEN_KEYWORD, "AS")
            query.table_alias = self.consume_ident_or_keyword().value
        elif self.match(TOKEN_IDENT) and not self.peek().value.upper() in ("JOIN", "WHERE", "GROUP", "ORDER", "LIMIT", "OFFSET", "INNER", "LEFT", "RIGHT"):
            query.table_alias = self.consume(TOKEN_IDENT).value

        # Optional JOIN
        join_type = "INNER"
        if self.try_consume(TOKEN_KEYWORD, "INNER"):
            self.consume(TOKEN_KEYWORD, "JOIN")
            query.join = self.parse_join("INNER")
        elif self.try_consume(TOKEN_KEYWORD, "LEFT"):
            self.try_consume(TOKEN_KEYWORD, "OUTER")
            self.consume(TOKEN_KEYWORD, "JOIN")
            query.join = self.parse_join("LEFT")
        elif self.try_consume(TOKEN_KEYWORD, "RIGHT"):
            self.try_consume(TOKEN_KEYWORD, "OUTER")
            self.consume(TOKEN_KEYWORD, "JOIN")
            query.join = self.parse_join("RIGHT")
        elif self.try_consume(TOKEN_KEYWORD, "JOIN"):
            query.join = self.parse_join("INNER")

        # Optional WHERE
        if self.try_consume(TOKEN_KEYWORD, "WHERE"):
            expr = self.parse_or_expr()
            query.where_expr = expr
            query.where = flatten_where_to_tuples(expr)

        # Optional GROUP BY
        if self.try_consume(TOKEN_KEYWORD, "GROUP"):
            self.consume(TOKEN_KEYWORD, "BY")
            query.group_by = self.parse_ident_list()

        # Optional HAVING
        if self.try_consume(TOKEN_KEYWORD, "HAVING"):
            query.having = self.parse_or_expr()

        # Optional ORDER BY
        if self.try_consume(TOKEN_KEYWORD, "ORDER"):
            self.consume(TOKEN_KEYWORD, "BY")
            query.order_by_specs = self.parse_order_by_specs()
            # For backward compatibility, set query.order_by string
            first_col, first_dir = query.order_by_specs[0]
            query.order_by = f"{first_col} {first_dir}" if first_dir == "DESC" else first_col

        # Optional LIMIT and OFFSET
        if self.try_consume(TOKEN_KEYWORD, "LIMIT"):
            lim_tok = self.consume(TOKEN_NUMBER)
            query.limit = int(lim_tok.value)
            if self.try_consume(TOKEN_KEYWORD, "OFFSET"):
                off_tok = self.consume(TOKEN_NUMBER)
                query.offset = int(off_tok.value)
        elif self.try_consume(TOKEN_KEYWORD, "OFFSET"):
            off_tok = self.consume(TOKEN_NUMBER)
            query.offset = int(off_tok.value)
            if self.try_consume(TOKEN_KEYWORD, "LIMIT"):
                lim_tok = self.consume(TOKEN_NUMBER)
                query.limit = int(lim_tok.value)

        return query

    def parse_select_columns(self) -> List[Dict[str, Any]]:
        cols = []
        while True:
            col_spec = self.parse_one_select_column()
            cols.append(col_spec)
            if not self.try_consume(TOKEN_PUNCT, ","):
                break
        return cols

    def parse_one_select_column(self) -> Dict[str, Any]:
        # Check for aggregate function: COUNT, SUM, AVG, MIN, MAX
        tok = self.peek()
        if tok.kind == TOKEN_KEYWORD and tok.value in ("COUNT", "SUM", "AVG", "MIN", "MAX"):
            agg = self.consume(TOKEN_KEYWORD).value
            self.consume(TOKEN_PUNCT, "(")
            inner_col = "*"
            if self.try_consume(TOKEN_PUNCT, "*"):
                inner_col = "*"
            else:
                arg_tok = self.consume_ident_or_keyword()
                inner_col = arg_tok.value.split(".")[-1]
            self.consume(TOKEN_PUNCT, ")")
            alias = None
            if self.try_consume(TOKEN_KEYWORD, "AS"):
                alias = self.consume_ident_or_keyword().value
            elif self.match(TOKEN_IDENT):
                alias = self.consume(TOKEN_IDENT).value
            return {"agg": agg, "col": inner_col, "alias": alias}

        # Check for wildcard *
        if self.try_consume(TOKEN_PUNCT, "*"):
            return {"agg": None, "col": "*", "alias": None}

        # Regular column identifier
        col_tok = self.consume_ident_or_keyword()
        plain_col = col_tok.value.split(".")[-1]
        alias = None
        if self.try_consume(TOKEN_KEYWORD, "AS"):
            alias = self.consume_ident_or_keyword().value
        elif self.match(TOKEN_IDENT):
            tok = self.peek()
            if tok.value.upper() not in ("FROM", "JOIN", "WHERE", "GROUP", "ORDER", "LIMIT"):
                alias = self.consume(TOKEN_IDENT).value

        return {"agg": None, "col": plain_col, "alias": alias}

    def parse_join(self, join_type: str) -> Dict[str, Any]:
        table_tok = self.consume_ident_or_keyword()
        alias = None
        if self.try_consume(TOKEN_KEYWORD, "AS"):
            alias = self.consume_ident_or_keyword().value
        elif self.match(TOKEN_IDENT) and self.peek().value.upper() != "ON":
            alias = self.consume(TOKEN_IDENT).value

        self.consume(TOKEN_KEYWORD, "ON")
        left_tok = self.consume_ident_or_keyword()
        self.consume(TOKEN_OP, "=")
        right_tok = self.consume_ident_or_keyword()

        return {
            "type": join_type,
            "table": table_tok.value,
            "alias": alias,
            "left": left_tok.value,
            "right": right_tok.value
        }

    def parse_order_by_specs(self) -> List[Tuple[str, str]]:
        specs = []
        while True:
            col_tok = self.consume_ident_or_keyword()
            direction = "ASC"
            if self.try_consume(TOKEN_KEYWORD, "DESC"):
                direction = "DESC"
            elif self.try_consume(TOKEN_KEYWORD, "ASC"):
                direction = "ASC"
            specs.append((col_tok.value.split(".")[-1], direction))
            if not self.try_consume(TOKEN_PUNCT, ","):
                break
        return specs

    def parse_ident_list(self) -> List[str]:
        items = []
        while True:
            t = self.consume_ident_or_keyword()
            items.append(t.value.split(".")[-1])
            if not self.try_consume(TOKEN_PUNCT, ","):
                break
        return items

    # --- WHERE Expression Parsing (Precedence: OR < AND < NOT < Comparisons) ---

    def parse_or_expr(self) -> ExprNode:
        expr = self.parse_and_expr()
        while self.try_consume(TOKEN_KEYWORD, "OR"):
            right = self.parse_and_expr()
            expr = LogicalNode("OR", expr, right)
        return expr

    def parse_and_expr(self) -> ExprNode:
        expr = self.parse_not_expr()
        while self.try_consume(TOKEN_KEYWORD, "AND"):
            right = self.parse_not_expr()
            expr = LogicalNode("AND", expr, right)
        return expr

    def parse_not_expr(self) -> ExprNode:
        if self.try_consume(TOKEN_KEYWORD, "NOT"):
            return NotNode(self.parse_not_expr())
        return self.parse_primary_expr()

    def parse_primary_expr(self) -> ExprNode:
        if self.try_consume(TOKEN_PUNCT, "("):
            expr = self.parse_or_expr()
            self.consume(TOKEN_PUNCT, ")")
            return expr

        # Column comparison: col OP literal
        col_tok = self.consume_ident_or_keyword()
        column = col_tok.value.split(".")[-1]

        op_tok = self.consume(TOKEN_OP)
        op = op_tok.value

        val_tok = self.peek()
        if val_tok.kind == TOKEN_NUMBER:
            val = self.consume(TOKEN_NUMBER).value
        elif val_tok.kind == TOKEN_STRING:
            val = self.consume(TOKEN_STRING).value
        elif val_tok.kind == TOKEN_KEYWORD and val_tok.value == "NULL":
            self.consume(TOKEN_KEYWORD, "NULL")
            val = None
        elif val_tok.kind == TOKEN_KEYWORD and val_tok.value in ("TRUE", "FALSE"):
            tok = self.consume(TOKEN_KEYWORD)
            val = tok.value == "TRUE"
        elif val_tok.kind == TOKEN_IDENT:
            # Identifier as value literal (e.g. unquoted status string)
            val = self.consume(TOKEN_IDENT).value
        else:
            raise ParseError(f"Expected literal value in comparison at position {val_tok.pos}, got {val_tok.value!r}")

        return ComparisonNode(column, op, val)

    # --- INSERT, UPDATE, DELETE Parsing ---

    def parse_insert(self) -> InsertStatement:
        self.consume(TOKEN_KEYWORD, "INSERT")
        self.consume(TOKEN_KEYWORD, "INTO")
        table_tok = self.consume_ident_or_keyword()
        table = table_tok.value

        columns = None
        if self.try_consume(TOKEN_PUNCT, "("):
            columns = self.parse_ident_list()
            self.consume(TOKEN_PUNCT, ")")

        self.consume(TOKEN_KEYWORD, "VALUES")
        values = []
        while True:
            self.consume(TOKEN_PUNCT, "(")
            row_vals = []
            while True:
                val = self.parse_literal_value()
                row_vals.append(val)
                if not self.try_consume(TOKEN_PUNCT, ","):
                    break
            self.consume(TOKEN_PUNCT, ")")
            values.append(row_vals)
            if not self.try_consume(TOKEN_PUNCT, ","):
                break

        return InsertStatement(table, columns, values)

    def parse_literal_value(self) -> Any:
        val = self._parse_single_literal()
        while self.try_consume(TOKEN_OP, "+"):
            right = self._parse_single_literal()
            if isinstance(val, (int, float)) and isinstance(right, (int, float)):
                val = val + right
            else:
                val = str(val if val is not None else "") + str(right if right is not None else "")
        return val

    def _parse_single_literal(self) -> Any:
        # Check negative number
        negative = False
        if self.try_consume(TOKEN_OP, "-"):
            negative = True

        tok = self.peek()
        if tok.kind == TOKEN_NUMBER:
            num = self.consume(TOKEN_NUMBER).value
            return -num if negative else num
        if negative:
            raise ParseError(f"Unexpected '-' before token {tok.value!r} at position {tok.pos}")

        if tok.kind == TOKEN_STRING:
            return self.consume(TOKEN_STRING).value
        if tok.kind == TOKEN_KEYWORD and tok.value == "NULL":
            self.consume(TOKEN_KEYWORD, "NULL")
            return None
        if tok.kind == TOKEN_KEYWORD and tok.value in ("TRUE", "FALSE"):
            t = self.consume(TOKEN_KEYWORD)
            return t.value == "TRUE"
        if tok.kind in (TOKEN_IDENT, TOKEN_KEYWORD):
            return self.consume_ident_or_keyword().value

        raise ParseError(f"Expected literal value at position {tok.pos}, got {tok.value!r}")

    def parse_update(self) -> UpdateStatement:
        self.consume(TOKEN_KEYWORD, "UPDATE")
        table = self.consume_ident_or_keyword().value

        self.consume(TOKEN_KEYWORD, "SET")
        assignments: Dict[str, Any] = {}
        while True:
            col_tok = self.consume_ident_or_keyword()
            self.consume(TOKEN_OP, "=")
            val = self.parse_literal_value()
            assignments[col_tok.value.split(".")[-1]] = val
            if not self.try_consume(TOKEN_PUNCT, ","):
                break

        where_expr = None
        if self.try_consume(TOKEN_KEYWORD, "WHERE"):
            where_expr = self.parse_or_expr()

        return UpdateStatement(table, assignments, where_expr)

    def parse_delete(self) -> DeleteStatement:
        self.consume(TOKEN_KEYWORD, "DELETE")
        self.consume(TOKEN_KEYWORD, "FROM")
        table = self.consume_ident_or_keyword().value

        where_expr = None
        if self.try_consume(TOKEN_KEYWORD, "WHERE"):
            where_expr = self.parse_or_expr()

        return DeleteStatement(table, where_expr)

    # --- DDL: CREATE / DROP INDEX ---

    def parse_create(self) -> Any:
        self.consume(TOKEN_KEYWORD, "CREATE")
        if self.try_consume(TOKEN_KEYWORD, "INDEX"):
            idx_name = self.consume_ident_or_keyword().value
            self.consume(TOKEN_KEYWORD, "ON")
            table = self.consume_ident_or_keyword().value
            self.consume(TOKEN_PUNCT, "(")
            column = self.consume_ident_or_keyword().value
            self.consume(TOKEN_PUNCT, ")")
            idx_type = "HASH"
            if self.try_consume(TOKEN_KEYWORD, "USING"):
                tok = self.consume_ident_or_keyword()
                idx_type = tok.value.upper()
                if idx_type not in ("HASH", "BTREE"):
                    raise ParseError(f"Unsupported index type '{idx_type}'. Choose HASH or BTREE.")
            return CreateIndexStatement(idx_name, table, column, idx_type)

        raise ParseError("CREATE only supports INDEX.")

    def parse_drop(self) -> Any:
        self.consume(TOKEN_KEYWORD, "DROP")
        if self.try_consume(TOKEN_KEYWORD, "INDEX"):
            idx_name = self.consume_ident_or_keyword().value
            table = None
            if self.try_consume(TOKEN_KEYWORD, "ON"):
                table = self.consume_ident_or_keyword().value
            return DropIndexStatement(idx_name, table)

        raise ParseError("DROP only supports INDEX.")


# -----------------------------------------------------------------------------
# Public API Functions
# -----------------------------------------------------------------------------

def parse(sql_text: str) -> Any:
    """Parses a SQL statement and returns the corresponding AST object."""
    sql_text = (sql_text or "").strip()
    if not sql_text:
        raise ParseError("Query is empty.")
    tokens = tokenize(sql_text)
    parser = Parser(tokens, sql_text)
    return parser.parse_statement()


def parse_column(col_text: str) -> Dict[str, Any]:
    """Backward-compatibility helper for parsing a single column expression."""
    col_text = col_text.strip()
    m = re.match(r"^(COUNT|SUM|AVG|MIN|MAX)\(\s*(\*|[\w.]+)\s*\)$", col_text, re.IGNORECASE)
    if m:
        return {"agg": m.group(1).upper(), "col": m.group(2).split(".")[-1], "alias": None}
    return {"agg": None, "col": col_text.split(".")[-1], "alias": None}


def parse_where(where_text: str) -> List[Tuple[str, str, Any]]:
    """Backward-compatibility helper for parsing a WHERE clause text."""
    sql = f"SELECT * FROM dummy WHERE {where_text}"
    tokens = tokenize(sql)
    parser = Parser(tokens, sql)
    query = parser.parse_select()
    return query.where


def parse_literal(value_text: str) -> Any:
    """Backward-compatibility helper for converting string to int, float, or str."""
    value_text = str(value_text).strip().strip("'\"")
    try:
        if "." in value_text:
            return float(value_text)
        return int(value_text)
    except ValueError:
        return value_text
