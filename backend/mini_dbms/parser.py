"""
parser.py
---------
A small SQL parser. It only understands one kind of statement:

    SELECT col1, col2, AGG(col3)
    FROM table_name
    [JOIN other_table ON table.key = other_table.key]
    [WHERE condition AND condition ...]
    [GROUP BY col1, col2]
    [ORDER BY col [ASC|DESC]]

We use Python's `re` (regular expressions) module to break the query
text into pieces. This is the "tokenizer + parser" stage that every real
database also has -- ours is just much simpler.
"""

import re


class ParseError(Exception):
    """Raised when the given SQL text does not match what we support."""
    pass


class ParsedQuery:
    """Holds the pieces of a query after parsing, ready to be executed."""

    def __init__(self):
        self.columns = []     # list of {"agg": "SUM"/None, "col": "salary"}
        self.table = None
        self.join = None      # {"table":..., "left":..., "right":...} or None
        self.where = []       # list of (column, operator, value) tuples, ANDed together
        self.group_by = []
        self.order_by = None


# The big regular expression below matches the overall shape of the query.
# (?P<name> ... ) creates a "named group" so we can pull out each part by name
# after matching, instead of counting parentheses by position.
QUERY_PATTERN = re.compile(
    r"SELECT\s+(?P<cols>.+?)\s+FROM\s+(?P<table>\w+)"
    r"(?:\s+JOIN\s+(?P<jtable>\w+)\s+ON\s+(?P<jleft>[\w.]+)\s*=\s*(?P<jright>[\w.]+))?"
    r"(?:\s+WHERE\s+(?P<where>.+?))?"
    r"(?:\s+GROUP\s+BY\s+(?P<groupby>.+?))?"
    r"(?:\s+ORDER\s+BY\s+(?P<orderby>.+?))?"
    r"\s*$",
    re.IGNORECASE | re.DOTALL,
)

# Matches things like AVG(salary), COUNT(*), SUM(amount)
AGGREGATE_PATTERN = re.compile(r"^(COUNT|SUM|AVG|MIN|MAX)\(\s*(\*|[\w.]+)\s*\)$", re.IGNORECASE)

# Matches one WHERE condition, e.g. "salary > 50000"
CONDITION_PATTERN = re.compile(r"(.+?)\s*(>=|<=|!=|=|>|<)\s*(.+)")


def parse(sql_text):
    sql_text = sql_text.strip().rstrip(";")
    if sql_text == "":
        raise ParseError("Query is empty.")

    match = QUERY_PATTERN.match(sql_text)
    if not match:
        raise ParseError(
            "Could not understand this query. Supported form:\n"
            "SELECT ... FROM table [JOIN ... ON ...] [WHERE ...] [GROUP BY ...] [ORDER BY ...]"
        )

    query = ParsedQuery()
    query.table = match.group("table")

    column_texts = match.group("cols").split(",")
    for col_text in column_texts:
        query.columns.append(parse_column(col_text.strip()))

    if match.group("jtable"):
        query.join = {
            "table": match.group("jtable"),
            "left": match.group("jleft"),
            "right": match.group("jright"),
        }

    if match.group("where"):
        query.where = parse_where(match.group("where"))

    if match.group("groupby"):
        query.group_by = [c.strip() for c in match.group("groupby").split(",")]

    if match.group("orderby"):
        query.order_by = match.group("orderby").strip()

    return query


def parse_column(col_text):
    agg_match = AGGREGATE_PATTERN.match(col_text)
    if agg_match:
        return {"agg": agg_match.group(1).upper(), "col": agg_match.group(2)}
    # strip a "table." prefix if present, e.g. "sales.department" -> "department"
    plain_name = col_text.split(".")[-1]
    return {"agg": None, "col": plain_name}


def parse_where(where_text):
    conditions = []
    # split the WHERE text on "AND" (case-insensitive) to support multiple conditions
    parts = re.split(r"\s+AND\s+", where_text, flags=re.IGNORECASE)
    for part in parts:
        m = CONDITION_PATTERN.match(part.strip())
        if not m:
            raise ParseError("Could not understand WHERE condition: %s" % part)
        column = m.group(1).strip().split(".")[-1]
        operator = m.group(2)
        value_text = m.group(3).strip().strip("'\"")
        conditions.append((column, operator, parse_literal(value_text)))
    return conditions


def parse_literal(value_text):
    try:
        if "." in value_text:
            return float(value_text)
        return int(value_text)
    except ValueError:
        return value_text
