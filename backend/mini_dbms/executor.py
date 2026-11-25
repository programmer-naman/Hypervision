"""
executor.py
-----------
Executes parsed queries and DDL/DML statements step by step.
Supports:
  - SELECT: SCAN or INDEX_SCAN -> JOIN -> FILTER -> GROUP/AGGREGATE -> HAVING -> PROJECTION -> DISTINCT -> SORT -> LIMIT/OFFSET
  - INSERT: row insertion with column validation and index maintenance
  - UPDATE: row modification with index maintenance
  - DELETE: row removal with index maintenance
  - CREATE/DROP INDEX: index lifecycle management
"""

from __future__ import annotations
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from . import operators as ops
from .optimizer import split_where_conditions, find_applicable_index
from .parser import (
    parse, ParsedQuery, InsertStatement, UpdateStatement, DeleteStatement,
    CreateIndexStatement, DropIndexStatement,
)
from .settings import ExecutionSettings

PARALLEL_ROW_THRESHOLD = 20000


class ExecutionResult:
    def __init__(self):
        self.rows: List[Dict[str, Any]] = []
        self.columns: List[str] = []
        self.stats: Optional[ops.OperatorStats] = None
        self.latency_ms: float = 0.0
        self.row_count: int = 0
        self.rows_affected: int = 0
        self.statement_type: str = "SELECT"
        self.index_used: Optional[str] = None
        self.message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "statement_type": self.statement_type,
            "rows": self.rows,
            "columns": self.columns,
            "row_count": self.row_count,
            "rows_affected": self.rows_affected,
            "latency_ms": self.latency_ms,
            "index_used": self.index_used,
            "message": self.message,
            "operator_stats": self.stats.steps if self.stats else [],
        }


def execute(query: ParsedQuery, storage: Any, thread_count: int = 1, settings: Optional[ExecutionSettings] = None, use_index: bool = True) -> ExecutionResult:
    """
    Executes a SELECT query AST against in-memory storage.
    Preserves exact operator sequence and compatibility for tuning knobs.
    """
    if settings is None:
        settings = ExecutionSettings(thread_count=thread_count)
    thread_count = settings.thread_count
    compiled = settings.compiled_predicates

    stats = ops.OperatorStats()
    start_time = time.perf_counter()

    table = storage.get_table(query.table)
    where = query.where
    index_used_name = None

    # Check for index scan if single-table query with WHERE conditions
    if use_index and not query.join and where:
        idx_match = find_applicable_index(table.name, where, storage)
        if idx_match is not None:
            index_obj, op, val, remaining_where = idx_match
            rows = ops.index_scan(table, index_obj, op, val, stats)
            index_used_name = index_obj.name
            where = remaining_where
        else:
            rows = ops.scan(table, stats)
    else:
        rows = ops.scan(table, stats)

    if query.join:
        right_table = storage.get_table(query.join["table"])
        right_rows = ops.scan(right_table, stats)
        left_key = query.join["left"].split(".")[-1]
        right_key = query.join["right"].split(".")[-1]
        join_type = query.join.get("type", "INNER")

        if settings.pushdown_filter and where:
            left_c, right_c, where = split_where_conditions(
                where, table.columns, right_table.columns)
            if left_c:
                rows = filter_with_threads(rows, left_c, stats, thread_count,
                                           compiled=compiled, label="FILTER " + table.name)
            if right_c:
                right_rows = filter_with_threads(right_rows, right_c, stats, thread_count,
                                                 compiled=compiled, label="FILTER " + right_table.name)

        if settings.build_smaller_side:
            build_left = len(rows) < len(right_rows)
            join_label = "HASH_JOIN (build: %s)" % (table.name if build_left else right_table.name)
            rows = ops.hash_join(
                rows, right_rows, left_key, right_key, stats,
                build_left=build_left, label=join_label, join_type=join_type,
                right_columns=right_table.columns, left_columns=table.columns
            )
        else:
            rows = ops.hash_join(
                rows, right_rows, left_key, right_key, stats,
                join_type=join_type, right_columns=right_table.columns, left_columns=table.columns
            )

    # Apply remaining WHERE conditions
    if where:
        # If we didn't use an index or there are remaining conditions, filter rows
        rows = filter_with_threads(rows, where, stats, thread_count, compiled=compiled)
    elif getattr(query, "where_expr", None) is not None and not index_used_name:
        # If where_expr is complex (OR, NOT, parentheses)
        rows = ops.filter_rows(rows, None, stats, expr=query.where_expr)

    # Aggregation or Projection
    has_aggregate = any(c.get("agg") for c in query.columns)
    if query.group_by or has_aggregate:
        rows = ops.group_aggregate(rows, query.group_by, query.columns, stats,
                                   streaming=settings.streaming_aggregation)
    else:
        rows = ops.project(rows, query.columns, stats)

    # HAVING clause on aggregates
    if getattr(query, "having", None) is not None:
        rows = [r for r in rows if query.having.evaluate(r)]

    # DISTINCT
    if getattr(query, "distinct", False):
        rows = ops.distinct(rows, stats)

    # ORDER BY
    if getattr(query, "order_by_specs", None):
        rows = ops.order_by(rows, query.order_by_specs, stats)
    elif query.order_by:
        rows = ops.order_by(rows, query.order_by, stats)

    # LIMIT & OFFSET
    if getattr(query, "limit", None) is not None or getattr(query, "offset", None) is not None:
        rows = ops.limit_offset(rows, query.limit, query.offset, stats)

    result = ExecutionResult()
    result.statement_type = "SELECT"
    result.rows = rows
    result.columns = list(rows[0].keys()) if rows else [c.get("alias") or c.get("col") for c in query.columns]
    result.stats = stats
    result.latency_ms = round((time.perf_counter() - start_time) * 1000, 3)
    result.row_count = len(rows)
    result.index_used = index_used_name
    return result


def filter_with_threads(rows, conditions, stats, thread_count, compiled=False, label="FILTER"):
    """
    Splits rows into chunks and filters each in a separate thread.
    """
    if thread_count <= 1 or len(rows) < PARALLEL_ROW_THRESHOLD:
        return ops.filter_rows(rows, conditions, stats, label=label, compiled=compiled)

    start_time = time.perf_counter()
    chunk_size = max(1, len(rows) // thread_count)
    chunks = []
    for i in range(0, len(rows), chunk_size):
        chunks.append(rows[i:i + chunk_size])

    results = [None] * len(chunks)
    scratch_stats = ops.OperatorStats()

    def worker(chunk_index, chunk_rows):
        results[chunk_index] = ops.filter_rows(chunk_rows, conditions, scratch_stats, compiled=compiled)

    threads = []
    for i, chunk in enumerate(chunks):
        thread = threading.Thread(target=worker, args=(i, chunk))
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    filtered_rows = []
    for chunk_result in results:
        filtered_rows.extend(chunk_result)

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    stats.record("%s (%d threads)" % (label, thread_count), len(rows), len(filtered_rows), elapsed_ms)
    return filtered_rows


def execute_statement(
    statement_or_sql: Union[str, Any],
    storage: Any,
    thread_count: int = 1,
    settings: Optional[ExecutionSettings] = None,
) -> ExecutionResult:
    """
    Unified entry point for executing any SQL statement supported by the Mini DBMS:
    SELECT, INSERT, UPDATE, DELETE, CREATE/DROP INDEX.
    """
    if isinstance(statement_or_sql, str):
        stmt = parse(statement_or_sql)
    else:
        stmt = statement_or_sql

    start_time = time.perf_counter()
    res = ExecutionResult()
    res.statement_type = getattr(stmt, "statement_type", "UNKNOWN")

    # 1. SELECT
    if isinstance(stmt, ParsedQuery):
        return execute(stmt, storage, thread_count=thread_count, settings=settings)

    # 2. INSERT
    if isinstance(stmt, InsertStatement):
        table = storage.get_table(stmt.table)

        columns = stmt.columns or table.columns
        inserted_count = 0

        for row_vals in stmt.values:
            if len(row_vals) != len(columns):
                raise ValueError(
                    f"Column count mismatch: table has {len(columns)} columns, values has {len(row_vals)}."
                )
            row_dict = dict(zip(columns, row_vals))
            # Fill missing table columns with None
            for c in table.columns:
                if c not in row_dict:
                    row_dict[c] = None

            table.insert_row(row_dict, storage.index_manager)
            inserted_count += 1

        res.rows_affected = inserted_count
        res.message = f"Query OK, {inserted_count} row(s) inserted."

    # 3. UPDATE
    elif isinstance(stmt, UpdateStatement):
        table = storage.get_table(stmt.table)

        target_rows = list(table.rows)
        if stmt.where_expr is not None:
            target_rows = [r for r in target_rows if stmt.where_expr.evaluate(r)]
        elif stmt.where:
            stats = ops.OperatorStats()
            target_rows = ops.filter_rows(target_rows, stmt.where, stats)

        updated_count = 0
        for row in list(target_rows):
            table.update_row(row, stmt.assignments, storage.index_manager)
            updated_count += 1

        res.rows_affected = updated_count
        res.message = f"Query OK, {updated_count} row(s) updated."

    # 4. DELETE
    elif isinstance(stmt, DeleteStatement):
        table = storage.get_table(stmt.table)

        target_rows = list(table.rows)
        if stmt.where_expr is not None:
            target_rows = [r for r in target_rows if stmt.where_expr.evaluate(r)]
        elif stmt.where:
            stats = ops.OperatorStats()
            target_rows = ops.filter_rows(target_rows, stmt.where, stats)

        deleted_count = 0
        for row in list(target_rows):
            table.delete_row(row, storage.index_manager)
            deleted_count += 1

        res.rows_affected = deleted_count
        res.message = f"Query OK, {deleted_count} row(s) deleted."

    # 5. CREATE INDEX
    elif isinstance(stmt, CreateIndexStatement):
        table = storage.get_table(stmt.table)
        if stmt.column not in table.columns:
            raise ValueError(f"Column '{stmt.column}' not found in table '{stmt.table}'.")
        idx = storage.index_manager.create_index(
            stmt.index_name, stmt.table, stmt.column, stmt.index_type, table.rows
        )
        res.message = f"Index '{stmt.index_name}' ({stmt.index_type}) created successfully on {stmt.table}({stmt.column})."

    # 6. DROP INDEX
    elif isinstance(stmt, DropIndexStatement):
        storage.index_manager.drop_index(stmt.index_name, stmt.table)
        res.message = f"Index '{stmt.index_name}' dropped successfully."

    res.latency_ms = round((time.perf_counter() - start_time) * 1000, 3)
    return res
