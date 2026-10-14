"""
operators.py
------------
This file has the actual "execution operators" of our mini DBMS:
SCAN, FILTER, HASH_JOIN, PROJECTION, GROUP/AGGREGATE and SORT.

Every real database engine executes a query as a pipeline of operators
like these. Each function below takes rows in and returns rows out, and
also records how long it took and how many rows it processed/produced,
using the OperatorStats class. This is exactly what gets shown in the
dashboard as the "query plan trace".
"""

import time


class OperatorStats:
    """Keeps a list of {operator name, rows in, rows out, time taken} entries."""

    def __init__(self):
        self.steps = []

    def record(self, operator_name, rows_in, rows_out, elapsed_ms):
        self.steps.append({
            "op": operator_name,
            "rows_in": rows_in,
            "rows_out": rows_out,
            "ms": round(elapsed_ms, 3),
        })


def scan(table, stats):
    """SCAN: read every row of a table. The simplest operator."""
    start_time = time.perf_counter()
    rows = list(table.rows)
    elapsed_ms = (time.perf_counter() - start_time) * 1000
    stats.record("SCAN " + table.name, len(table.rows), len(rows), elapsed_ms)
    return rows


# Maps a WHERE comparison operator (as text) to an actual Python comparison.
COMPARISON_FUNCTIONS = {
    "=":  lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">":  lambda a, b: a is not None and a > b,
    "<":  lambda a, b: a is not None and a < b,
    ">=": lambda a, b: a is not None and a >= b,
    "<=": lambda a, b: a is not None and a <= b,
}


def _compiled_filter(rows, column, operator, value):
    """
    Same result as COMPARISON_FUNCTIONS, but the comparison is written
    directly inside the list comprehension, so Python does not have to make
    a function call for every single row. (Used when compiled_predicates is on.)
    """
    if operator == "=":
        return [row for row in rows if row.get(column) == value]
    if operator == "!=":
        return [row for row in rows if row.get(column) != value]
    if operator == ">":
        return [row for row in rows if (v := row.get(column)) is not None and v > value]
    if operator == "<":
        return [row for row in rows if (v := row.get(column)) is not None and v < value]
    if operator == ">=":
        return [row for row in rows if (v := row.get(column)) is not None and v >= value]
    if operator == "<=":
        return [row for row in rows if (v := row.get(column)) is not None and v <= value]
    raise KeyError(operator)


def filter_rows(rows, conditions, stats, label="FILTER", compiled=False):
    """FILTER: keep only the rows that satisfy every WHERE condition (AND logic)."""
    start_time = time.perf_counter()
    filtered = rows
    for column, operator, value in conditions:
        if compiled:
            filtered = _compiled_filter(filtered, column, operator, value)
        else:
            compare = COMPARISON_FUNCTIONS[operator]
            filtered = [row for row in filtered if compare(row.get(column), value)]
    elapsed_ms = (time.perf_counter() - start_time) * 1000
    stats.record(label, len(rows), len(filtered), elapsed_ms)
    return filtered


def hash_join(left_rows, right_rows, left_key, right_key, stats, build_left=False, label="HASH_JOIN"):
    """
    HASH_JOIN: combine two tables on a matching key.
    Step 1: build a hash table (a Python dict) from one input, keyed by its
            join column -- this is the "build" phase.
    Step 2: for every row of the other input, look up matching rows in that
            dict in O(1) time -- this is the "probe" phase.

    By default the right-hand rows are the build side. With build_left=True
    the left-hand rows are used instead (cheaper when the left input is the
    smaller one). Each joined row has the same contents either way (left
    columns win over right columns); only the row order can differ.
    """
    start_time = time.perf_counter()

    joined_rows = []
    if not build_left:
        hash_table = {}
        for row in right_rows:
            key = row.get(right_key)
            hash_table.setdefault(key, []).append(row)

        for left_row in left_rows:
            key = left_row.get(left_key)
            matches = hash_table.get(key, [])
            for right_row in matches:
                merged_row = dict(right_row)
                merged_row.update(left_row)
                joined_rows.append(merged_row)
    else:
        hash_table = {}
        for row in left_rows:
            key = row.get(left_key)
            hash_table.setdefault(key, []).append(row)

        for right_row in right_rows:
            key = right_row.get(right_key)
            matches = hash_table.get(key, [])
            for left_row in matches:
                merged_row = dict(right_row)
                merged_row.update(left_row)
                joined_rows.append(merged_row)

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    stats.record(label, len(left_rows) + len(right_rows), len(joined_rows), elapsed_ms)
    return joined_rows


def project(rows, columns, stats):
    """PROJECTION: keep only the requested columns (used when there is no aggregate)."""
    start_time = time.perf_counter()
    plain_columns = [c["col"] for c in columns if c["agg"] is None]

    if plain_columns and plain_columns != ["*"]:
        projected = []
        for row in rows:
            new_row = {}
            for col in plain_columns:
                new_row[col] = row.get(col)
            projected.append(new_row)
    else:
        projected = rows

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    stats.record("PROJECTION", len(rows), len(projected), elapsed_ms)
    return projected


def group_aggregate_streaming(rows, group_columns, agg_columns, stats):
    """
    Streaming version of GROUP/AGGREGATE (used when streaming_aggregation is on).

    Instead of first collecting every row of every group in memory and then
    computing the aggregates, it keeps only a few running numbers per group
    (row count, value count, running total, min, max) and updates them as
    each row goes by. Same output as group_aggregate(), far less memory.
    """
    start_time = time.perf_counter()

    specs = [c for c in agg_columns if c["agg"] is not None]
    # (position in the state list, aggregate name, column) for each aggregate
    plan = [(i, spec["agg"], spec["col"]) for i, spec in enumerate(specs)]

    groups = {}   # key -> one state per aggregate: [rows, values, total, min, max]
    # building the key is the hottest line of the loop, so pick the cheapest form once
    single_column = group_columns[0] if len(group_columns) == 1 else None
    for row in rows:
        if single_column is not None:
            key = (row.get(single_column),)
        elif group_columns:
            key = tuple([row.get(col) for col in group_columns])
        else:
            key = ("__all_rows__",)
        states = groups.get(key)
        if states is None:
            states = groups[key] = [[0, 0, 0, None, None] for _ in plan]

        for i, agg_name, agg_col in plan:
            state = states[i]
            state[0] += 1
            if agg_col == "*":
                continue
            value = row.get(agg_col)
            if value is None:
                continue
            state[1] += 1
            if agg_name == "SUM" or agg_name == "AVG":
                state[2] += value
            elif agg_name == "MIN":
                if state[3] is None or value < state[3]:
                    state[3] = value
            elif agg_name == "MAX":
                if state[4] is None or value > state[4]:
                    state[4] = value

    output_rows = []
    for key, states in groups.items():
        result_row = {}
        for i, col in enumerate(group_columns):
            result_row[col] = key[i]

        for i, agg_name, agg_col in plan:
            n_rows, n_values, total, lowest, highest = states[i]
            label = "%s(%s)" % (agg_name, agg_col)
            if agg_name == "COUNT":
                result_row[label] = n_rows if agg_col == "*" else n_values
            elif agg_name == "SUM":
                result_row[label] = round(total, 4) if n_values else 0
            elif agg_name == "AVG":
                result_row[label] = round(total / n_values, 4) if n_values else None
            elif agg_name == "MIN":
                result_row[label] = lowest if n_values else None
            elif agg_name == "MAX":
                result_row[label] = highest if n_values else None

        output_rows.append(result_row)

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    operator_name = "HASH_GROUP_BY" if group_columns else "AGGREGATE"
    stats.record(operator_name + " (streaming)", len(rows), len(output_rows), elapsed_ms)
    return output_rows


def group_aggregate(rows, group_columns, agg_columns, stats, streaming=False):
    """
    GROUP/AGGREGATE: group rows by the GROUP BY columns, then compute
    COUNT/SUM/AVG/MIN/MAX for each requested aggregate column.
    """
    if streaming:
        return group_aggregate_streaming(rows, group_columns, agg_columns, stats)

    start_time = time.perf_counter()

    groups = {}
    for row in rows:
        if group_columns:
            key = tuple(row.get(col) for col in group_columns)
        else:
            key = ("__all_rows__",)   # one big group when there is no GROUP BY
        groups.setdefault(key, []).append(row)

    aggregate_specs = [c for c in agg_columns if c["agg"] is not None]

    output_rows = []
    for key, group_rows in groups.items():
        result_row = {}

        for i, col in enumerate(group_columns):
            result_row[col] = key[i]

        for spec in aggregate_specs:
            agg_name = spec["agg"]
            agg_col = spec["col"]
            label = "%s(%s)" % (agg_name, agg_col)

            if agg_col == "*":
                values = None
            else:
                values = [r.get(agg_col) for r in group_rows if r.get(agg_col) is not None]

            if agg_name == "COUNT":
                result_row[label] = len(group_rows) if agg_col == "*" else len(values)
            elif agg_name == "SUM":
                result_row[label] = round(sum(values), 4) if values else 0
            elif agg_name == "AVG":
                result_row[label] = round(sum(values) / len(values), 4) if values else None
            elif agg_name == "MIN":
                result_row[label] = min(values) if values else None
            elif agg_name == "MAX":
                result_row[label] = max(values) if values else None

        output_rows.append(result_row)

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    operator_name = "HASH_GROUP_BY" if group_columns else "AGGREGATE"
    stats.record(operator_name, len(rows), len(output_rows), elapsed_ms)
    return output_rows


def order_by(rows, order_expression, stats):
    """SORT: order rows by one column, ascending by default, DESC if given."""
    start_time = time.perf_counter()

    order_expression = order_expression.strip()
    descending = False
    if order_expression.upper().endswith(" DESC"):
        descending = True
        order_expression = order_expression[:-5].strip()
    elif order_expression.upper().endswith(" ASC"):
        order_expression = order_expression[:-4].strip()

    column = order_expression
    sorted_rows = sorted(
        rows,
        key=lambda row: (row.get(column) is None, row.get(column)),
        reverse=descending,
    )

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    stats.record("SORT", len(rows), len(sorted_rows), elapsed_ms)
    return sorted_rows
