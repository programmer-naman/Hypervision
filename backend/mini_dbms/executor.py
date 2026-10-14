"""
executor.py
-----------
Runs a parsed query step by step, using the operators from operators.py,
and hands back the final rows plus timing information for every step.

`thread_count` lets us simulate increasing the number of "workers"
available for the FILTER step on a large table -- a stand-in for
increasing the vCPUs given to a Xen guest VM. Rerunning the same query
with a higher thread_count and comparing CPU usage is how we demonstrate
the "change a resource, rerun, compare" loop from the project report,
without needing a real hypervisor.
"""

import time
import threading

from . import operators as ops
from .optimizer import split_where_conditions
from .settings import ExecutionSettings

# Only bother splitting work across threads if the table is reasonably
# big -- for small tables the extra thread-management overhead is not
# worth it and can even make things slower.
PARALLEL_ROW_THRESHOLD = 20000


class ExecutionResult:
    def __init__(self):
        self.rows = []
        self.stats = None
        self.latency_ms = 0
        self.row_count = 0


def execute(query, storage, thread_count=1, settings=None):
    """
    Runs `query`. `settings` (an ExecutionSettings) selects the tuning knobs;
    if it is omitted, the default engine is used with `thread_count` threads.
    """
    if settings is None:
        settings = ExecutionSettings(thread_count=thread_count)
    thread_count = settings.thread_count
    compiled = settings.compiled_predicates

    stats = ops.OperatorStats()
    start_time = time.perf_counter()

    table = storage.get_table(query.table)
    rows = ops.scan(table, stats)
    where = query.where

    if query.join:
        right_table = storage.get_table(query.join["table"])
        right_rows = ops.scan(right_table, stats)
        left_key = query.join["left"].split(".")[-1]
        right_key = query.join["right"].split(".")[-1]

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
            rows = ops.hash_join(rows, right_rows, left_key, right_key, stats,
                                 build_left=build_left, label=join_label)
        else:
            rows = ops.hash_join(rows, right_rows, left_key, right_key, stats)

    if where:
        rows = filter_with_threads(rows, where, stats, thread_count, compiled=compiled)

    has_aggregate = any(c["agg"] for c in query.columns)
    if query.group_by or has_aggregate:
        rows = ops.group_aggregate(rows, query.group_by, query.columns, stats,
                                   streaming=settings.streaming_aggregation)
    else:
        rows = ops.project(rows, query.columns, stats)

    if query.order_by:
        rows = ops.order_by(rows, query.order_by, stats)

    result = ExecutionResult()
    result.rows = rows
    result.stats = stats
    result.latency_ms = round((time.perf_counter() - start_time) * 1000, 3)
    result.row_count = len(rows)
    return result


def filter_with_threads(rows, conditions, stats, thread_count, compiled=False, label="FILTER"):
    """
    Splits `rows` into `thread_count` roughly-equal chunks and filters
    each chunk in its own Python thread.

    Note for the viva: Python's Global Interpreter Lock (GIL) means pure
    Python threads do not truly run at the same time on multiple CPU
    cores. This is fine for our purpose -- we are only *demonstrating*
    the idea of scaling up workers/vCPUs and observing its effect on
    measured CPU usage, not chasing a real speed-up.
    """
    if thread_count <= 1 or len(rows) < PARALLEL_ROW_THRESHOLD:
        return ops.filter_rows(rows, conditions, stats, label=label, compiled=compiled)

    start_time = time.perf_counter()

    chunk_size = max(1, len(rows) // thread_count)
    chunks = []
    for i in range(0, len(rows), chunk_size):
        chunks.append(rows[i:i + chunk_size])

    # each thread writes its result into results[i], no shared list appends
    results = [None] * len(chunks)
    scratch_stats = ops.OperatorStats()  # per-chunk timings are not shown individually

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
