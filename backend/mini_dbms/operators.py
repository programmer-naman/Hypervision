"""
operators.py
------------
Execution operators of the HyperVision Mini DBMS:
SCAN, INDEX_SCAN, FILTER, HASH_JOIN (INNER/LEFT/RIGHT), PROJECTION,
GROUP/AGGREGATE (standard and streaming), SORT, DISTINCT, and LIMIT/OFFSET.

Every operator takes rows in and returns rows out, while recording elapsed
time and row counts through the OperatorStats tracker.
"""

from __future__ import annotations
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


class OperatorStats:
    """Keeps a list of {operator name, rows in, rows out, time taken} entries."""

    def __init__(self):
        self.steps = []

    def record(self, operator_name: str, rows_in: int, rows_out: int, elapsed_ms: float):
        self.steps.append({
            "op": operator_name,
            "rows_in": rows_in,
            "rows_out": rows_out,
            "ms": round(elapsed_ms, 3),
        })


def scan(table: Any, stats: OperatorStats) -> List[Dict[str, Any]]:
    """SCAN: read every row of a table."""
    start_time = time.perf_counter()
    rows = list(table.rows)
    elapsed_ms = (time.perf_counter() - start_time) * 1000
    stats.record("SCAN " + table.name, len(table.rows), len(rows), elapsed_ms)
    return rows


def index_scan(
    table: Any,
    index: Any,
    op: str,
    value: Any,
    stats: OperatorStats,
    label: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    INDEX_SCAN: lookup rows using a hash or B-Tree index.
    Avoids scanning the entire table for matching rows.
    """
    start_time = time.perf_counter()
    if op == "=":
        matching = index.lookup_equal(value)
    else:
        matching = index.lookup_range(op, value)

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    op_label = label or f"INDEX_SCAN {table.name} ({index.name} ON {index.column})"
    stats.record(op_label, len(table.rows), len(matching), elapsed_ms)
    return matching


# Maps a WHERE comparison operator (as text) to an actual Python comparison.
COMPARISON_FUNCTIONS = {
    "=":  lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">":  lambda a, b: a is not None and a > b,
    "<":  lambda a, b: a is not None and a < b,
    ">=": lambda a, b: a is not None and a >= b,
    "<=": lambda a, b: a is not None and a <= b,
}


def _compiled_filter(rows: List[Dict[str, Any]], column: str, operator: str, value: Any) -> List[Dict[str, Any]]:
    """
    Inlines each comparison to avoid Python function-call overhead on each row.
    """
    col = column.split(".")[-1]
    if operator == "=":
        return [row for row in rows if row.get(col, row.get(column)) == value]
    if operator == "!=":
        return [row for row in rows if row.get(col, row.get(column)) != value]
    if operator == ">":
        return [row for row in rows if (v := row.get(col, row.get(column))) is not None and v > value]
    if operator == "<":
        return [row for row in rows if (v := row.get(col, row.get(column))) is not None and v < value]
    if operator == ">=":
        return [row for row in rows if (v := row.get(col, row.get(column))) is not None and v >= value]
    if operator == "<=":
        return [row for row in rows if (v := row.get(col, row.get(column))) is not None and v <= value]
    raise KeyError(operator)


def filter_rows(
    rows: List[Dict[str, Any]],
    conditions: Any,
    stats: OperatorStats,
    label: str = "FILTER",
    compiled: bool = False,
    expr: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """
    FILTER: keep only rows matching WHERE criteria.
    Supports either an AST ExprNode (for rich boolean logic AND/OR/NOT/parentheses)
    or a list of (column, operator, value) tuples.
    """
    start_time = time.perf_counter()
    if expr is not None and not (isinstance(conditions, list) and not expr):
        filtered = [row for row in rows if expr.evaluate(row)]
    else:
        filtered = rows
        for cond in (conditions or []):
            column, operator, value = cond[0], cond[1], cond[2]
            if compiled:
                filtered = _compiled_filter(filtered, column, operator, value)
            else:
                compare = COMPARISON_FUNCTIONS[operator]
                col = column.split(".")[-1]
                filtered = [row for row in filtered if compare(row.get(col, row.get(column)), value)]

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    stats.record(label, len(rows), len(filtered), elapsed_ms)
    return filtered


def hash_join(
    left_rows: List[Dict[str, Any]],
    right_rows: List[Dict[str, Any]],
    left_key: str,
    right_key: str,
    stats: OperatorStats,
    build_left: bool = False,
    label: str = "HASH_JOIN",
    join_type: str = "INNER",
    right_columns: Optional[List[str]] = None,
    left_columns: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    HASH_JOIN: combines rows on matching keys.
    Supports INNER JOIN, LEFT OUTER JOIN, and RIGHT OUTER JOIN.
    """
    start_time = time.perf_counter()
    l_key = left_key.split(".")[-1]
    r_key = right_key.split(".")[-1]

    joined_rows = []
    join_type_up = join_type.upper()

    if not build_left:
        # Build right hash table
        hash_table: Dict[Any, List[Dict[str, Any]]] = {}
        for row in right_rows:
            key = row.get(r_key, row.get(right_key))
            hash_table.setdefault(key, []).append(row)

        matched_right_ids = set()

        for left_row in left_rows:
            key = left_row.get(l_key, left_row.get(left_key))
            matches = hash_table.get(key, [])
            if matches:
                for right_row in matches:
                    matched_right_ids.add(id(right_row))
                    merged = dict(right_row)
                    merged.update(left_row)
                    joined_rows.append(merged)
            elif join_type_up == "LEFT":
                # Unmatched left row with nulls for right columns
                merged = dict(left_row)
                if right_columns:
                    for c in right_columns:
                        if c not in merged:
                            merged[c] = None
                joined_rows.append(merged)

        if join_type_up == "RIGHT":
            # Add unmatched right rows
            for right_row in right_rows:
                if id(right_row) not in matched_right_ids:
                    merged = dict(right_row)
                    if left_columns:
                        for c in left_columns:
                            if c not in merged:
                                merged[c] = None
                    joined_rows.append(merged)

    else:
        # Build left hash table
        hash_table = {}
        for row in left_rows:
            key = row.get(l_key, row.get(left_key))
            hash_table.setdefault(key, []).append(row)

        matched_left_ids = set()

        for right_row in right_rows:
            key = right_row.get(r_key, right_row.get(right_key))
            matches = hash_table.get(key, [])
            if matches:
                for left_row in matches:
                    matched_left_ids.add(id(left_row))
                    merged = dict(right_row)
                    merged.update(left_row)
                    joined_rows.append(merged)
            elif join_type_up == "RIGHT":
                merged = dict(right_row)
                if left_columns:
                    for c in left_columns:
                        if c not in merged:
                            merged[c] = None
                joined_rows.append(merged)

        if join_type_up == "LEFT":
            for left_row in left_rows:
                if id(left_row) not in matched_left_ids:
                    merged = dict(left_row)
                    if right_columns:
                        for c in right_columns:
                            if c not in merged:
                                merged[c] = None
                    joined_rows.append(merged)

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    stats.record(label, len(left_rows) + len(right_rows), len(joined_rows), elapsed_ms)
    return joined_rows


def project(rows: List[Dict[str, Any]], columns: List[Dict[str, Any]], stats: OperatorStats) -> List[Dict[str, Any]]:
    """PROJECTION: keep only requested columns and apply aliases."""
    start_time = time.perf_counter()
    plain_specs = [c for c in columns if c.get("agg") is None]

    if plain_specs and not (len(plain_specs) == 1 and plain_specs[0].get("col") == "*"):
        projected = []
        for row in rows:
            new_row = {}
            for spec in plain_specs:
                col = spec.get("col", "").split(".")[-1]
                val = row.get(col)
                if val is None and spec.get("col") in row:
                    val = row[spec["col"]]
                out_name = spec.get("alias") or col
                new_row[out_name] = val
            projected.append(new_row)
    else:
        projected = rows

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    stats.record("PROJECTION", len(rows), len(projected), elapsed_ms)
    return projected


def group_aggregate_streaming(
    rows: List[Dict[str, Any]],
    group_columns: List[str],
    agg_columns: List[Dict[str, Any]],
    stats: OperatorStats
) -> List[Dict[str, Any]]:
    """Streaming GROUP BY version for lower memory footprint."""
    start_time = time.perf_counter()

    specs = [c for c in agg_columns if c.get("agg") is not None]
    plan = [(i, spec["agg"], spec["col"].split(".")[-1], spec.get("alias")) for i, spec in enumerate(specs)]

    groups = {}
    clean_group_cols = [c.split(".")[-1] for c in group_columns]
    single_column = clean_group_cols[0] if len(clean_group_cols) == 1 else None

    for row in rows:
        if single_column is not None:
            key = (row.get(single_column, row.get(group_columns[0])),)
        elif clean_group_cols:
            key = tuple(row.get(c, row.get(orig)) for c, orig in zip(clean_group_cols, group_columns))
        else:
            key = ("__all_rows__",)

        states = groups.get(key)
        if states is None:
            states = groups[key] = [[0, 0, 0, None, None] for _ in plan]

        for i, agg_name, agg_col, _ in plan:
            state = states[i]
            state[0] += 1
            if agg_col == "*":
                continue
            value = row.get(agg_col)
            if value is None:
                continue
            state[1] += 1
            if agg_name in ("SUM", "AVG"):
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
        for i, col in enumerate(clean_group_cols):
            result_row[col] = key[i]

        for i, agg_name, agg_col, alias in plan:
            n_rows, n_values, total, lowest, highest = states[i]
            label = alias or ("%s(%s)" % (agg_name, agg_col))
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


def group_aggregate(
    rows: List[Dict[str, Any]],
    group_columns: List[str],
    agg_columns: List[Dict[str, Any]],
    stats: OperatorStats,
    streaming: bool = False
) -> List[Dict[str, Any]]:
    """Standard GROUP BY / AGGREGATE."""
    if streaming:
        return group_aggregate_streaming(rows, group_columns, agg_columns, stats)

    start_time = time.perf_counter()
    clean_group_cols = [c.split(".")[-1] for c in group_columns]

    groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    for row in rows:
        if clean_group_cols:
            key = tuple(row.get(c, row.get(orig)) for c, orig in zip(clean_group_cols, group_columns))
        else:
            key = ("__all_rows__",)
        groups.setdefault(key, []).append(row)

    aggregate_specs = [c for c in agg_columns if c.get("agg") is not None]

    output_rows = []
    for key, group_rows in groups.items():
        result_row = {}

        for i, col in enumerate(clean_group_cols):
            result_row[col] = key[i]

        for spec in aggregate_specs:
            agg_name = spec["agg"]
            agg_col = spec["col"].split(".")[-1]
            label = spec.get("alias") or ("%s(%s)" % (agg_name, agg_col))

            if agg_col == "*":
                values = None
            else:
                values = [r.get(agg_col, r.get(spec["col"])) for r in group_rows if r.get(agg_col, r.get(spec["col"])) is not None]

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


def order_by(
    rows: List[Dict[str, Any]],
    order_expression: Union[str, List[Tuple[str, str]]],
    stats: OperatorStats
) -> List[Dict[str, Any]]:
    """SORT: order rows by columns, ascending or descending."""
    start_time = time.perf_counter()

    if isinstance(order_expression, list):
        # List of (column, direction)
        sorted_rows = list(rows)
        # Apply in reverse order for stable sorting
        for col, direction in reversed(order_expression):
            clean_col = col.split(".")[-1]
            descending = (direction.upper() == "DESC")
            sorted_rows.sort(
                key=lambda r: (r.get(clean_col) is None, r.get(clean_col)),
                reverse=descending
            )
    else:
        order_expression = order_expression.strip()
        descending = False
        if order_expression.upper().endswith(" DESC"):
            descending = True
            order_expression = order_expression[:-5].strip()
        elif order_expression.upper().endswith(" ASC"):
            order_expression = order_expression[:-4].strip()

        column = order_expression.split(".")[-1]
        sorted_rows = sorted(
            rows,
            key=lambda row: (row.get(column) is None, row.get(column)),
            reverse=descending,
        )

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    stats.record("SORT", len(rows), len(sorted_rows), elapsed_ms)
    return sorted_rows


def distinct(rows: List[Dict[str, Any]], stats: OperatorStats) -> List[Dict[str, Any]]:
    """DISTINCT: remove duplicate result rows."""
    start_time = time.perf_counter()
    seen = set()
    unique_rows = []
    for r in rows:
        key = tuple(sorted((k, v) for k, v in r.items()))
        if key not in seen:
            seen.add(key)
            unique_rows.append(r)
    elapsed_ms = (time.perf_counter() - start_time) * 1000
    stats.record("DISTINCT", len(rows), len(unique_rows), elapsed_ms)
    return unique_rows


def limit_offset(
    rows: List[Dict[str, Any]],
    limit: Optional[int],
    offset: Optional[int],
    stats: OperatorStats
) -> List[Dict[str, Any]]:
    """LIMIT and OFFSET operator."""
    start_time = time.perf_counter()
    start_idx = offset or 0
    end_idx = (start_idx + limit) if limit is not None else None
    result = rows[start_idx:end_idx]
    elapsed_ms = (time.perf_counter() - start_time) * 1000
    stats.record("LIMIT_OFFSET", len(rows), len(result), elapsed_ms)
    return result
