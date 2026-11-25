"""
optimizer.py
------------
Query optimizer for the HyperVision Mini DBMS.
Implements:
  1. Predicate pushdown: push single-table WHERE filters before HASH_JOIN.
  2. Index selection: select an index scan when a WHERE predicate matches an index.
  3. Query execution plan generation.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple


def split_where_conditions(conditions, left_columns, right_columns):
    """
    Splits WHERE conditions for a JOIN into
        (left-only conditions, right-only conditions, conditions that must stay after the join).
    """
    left_columns = set(c.split(".")[-1] for c in (left_columns or []))
    right_columns = set(c.split(".")[-1] for c in (right_columns or []))
    left_only, right_only, after_join = [], [], []

    for condition in conditions:
        column = condition[0].split(".")[-1]
        in_left = column in left_columns
        in_right = column in right_columns
        if in_left and not in_right:
            left_only.append(condition)
        elif in_right and not in_left:
            right_only.append(condition)
        else:
            after_join.append(condition)
    return left_only, right_only, after_join


def find_applicable_index(
    table_name: str,
    conditions: List[Tuple[str, str, Any]],
    storage: Any
) -> Optional[Tuple[Any, str, Any, List[Tuple[str, str, Any]]]]:
    """
    Checks if any condition in `conditions` can use an index on `table_name`.
    Returns (index, op, value, remaining_conditions) or None.
    """
    if not storage or not hasattr(storage, "index_manager"):
        return None

    table_indexes = storage.index_manager.get_table_indexes(table_name)
    if not table_indexes:
        return None

    # Check for equality condition first (=), then range conditions
    for target_op in ("=", ">", ">=", "<", "<="):
        for idx, (col_name, op, val) in enumerate(conditions):
            clean_col = col_name.split(".")[-1]
            if op == target_op:
                index = storage.index_manager.get_index_for_column(table_name, clean_col, op)
                if index is not None:
                    remaining = [c for i, c in enumerate(conditions) if i != idx]
                    return index, op, val, remaining

    return None


def build_plan(query, settings=None, storage=None, index_used=None) -> List[str]:
    """
    Ordered list of operator names for `query`.
    Matches the actual pipeline executed by executor.py.
    """
    if index_used:
        plan_steps = [f"INDEX_SCAN {query.table} ({index_used.name})"]
    else:
        plan_steps = ["SCAN " + query.table]

    if query.join:
        plan_steps.append("SCAN " + query.join["table"])

        pushdown = (
            settings is not None and settings.pushdown_filter
            and storage is not None and query.where
        )
        if pushdown:
            left_table = storage.get_table(query.table)
            right_table = storage.get_table(query.join["table"])
            left_c, right_c, after_c = split_where_conditions(
                query.where, left_table.columns, right_table.columns)
            if left_c:
                plan_steps.append("FILTER " + query.table)
            if right_c:
                plan_steps.append("FILTER " + query.join["table"])
            plan_steps.append("HASH_JOIN")
            if after_c:
                plan_steps.append("FILTER")
        else:
            plan_steps.append("HASH_JOIN")
            if query.where:
                plan_steps.append("FILTER")
    elif query.where and not index_used:
        plan_steps.append("FILTER")   # runs right after the scan
    elif query.where and index_used:
        # If index was used but there were additional conditions, FILTER still runs
        pass

    has_aggregate = any(c["agg"] for c in query.columns)
    if query.group_by:
        plan_steps.append("HASH_GROUP_BY")
    elif has_aggregate:
        plan_steps.append("AGGREGATE")
    else:
        plan_steps.append("PROJECTION")

    if getattr(query, "distinct", False):
        plan_steps.append("DISTINCT")

    if query.order_by:
        plan_steps.append("SORT")

    if getattr(query, "limit", None) is not None or getattr(query, "offset", None) is not None:
        plan_steps.append("LIMIT_OFFSET")

    return plan_steps
