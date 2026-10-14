"""
recommender.py
--------------
Turns a detected bottleneck into SPECIFIC actions that reduce CPU usage,
RAM consumption and query latency.

Two layers:

  recommend(classification)
      The original one-line summary for a bottleneck class (kept so old
      experiment records and callers keep working).

  recommend_actions(classification, context)
      A ranked list of concrete actions. Each action says
        - what to change and why (using this query's own numbers),
        - which of CPU / RAM / Latency it should reduce,
        - whether it can be applied automatically ("setting") or needs
          you to edit the SQL or data ("manual").
      "setting" actions carry the exact settings change, so the terminal
      command `apply N` can apply them and rerun the experiment to compare.

Every "setting" action maps to a real engine optimisation (see
mini_dbms/settings.py) and was checked to return identical query results.
"""

from mini_dbms.optimizer import split_where_conditions
from mini_dbms.settings import ExecutionSettings

METRICS = ("CPU", "RAM", "Latency")

# ---------------------------------------------------------------------------
# Layer 1: one-line summary per bottleneck class (unchanged)
# ---------------------------------------------------------------------------

RECOMMENDATIONS = {
    "CPU-bound": "CPU is the limit: cut the work each query does first (actions below), then measure any vCPU/thread change.",
    "Memory pressure": "Memory is the limit: shrink what the query keeps in memory (actions below) before adding RAM or removing concurrency.",
    "I/O-bound": "Scanning is the limit: read and keep fewer rows (actions below) before adding more CPU.",
    "Parallel/resource saturation": "Extra threads are not paying off: reduce them and cut per-row work (actions below).",
    "Mixed": "No single resource dominates: change one thing at a time (actions below) and compare.",
}

DEFAULT_RECOMMENDATION = RECOMMENDATIONS["Mixed"]


def recommend(classification):
    return RECOMMENDATIONS.get(classification, DEFAULT_RECOMMENDATION)


# ---------------------------------------------------------------------------
# Layer 2: concrete actions
# ---------------------------------------------------------------------------

# Which metrics matter most for each bottleneck (most important first).
# This decides the order of the suggestions.
BOTTLENECK_FOCUS = {
    "CPU-bound": ("CPU", "Latency", "RAM"),
    "Memory pressure": ("RAM", "Latency", "CPU"),
    "I/O-bound": ("Latency", "CPU", "RAM"),
    "Parallel/resource saturation": ("CPU", "Latency", "RAM"),
    "Mixed": ("Latency", "CPU", "RAM"),
}

_FOCUS_WEIGHTS = (30, 20, 10)

_LARGE_TABLE_ROWS = 20000     # same size the engine uses before it starts threads
_BIG_SORT_ROWS = 10000
MAX_SUGGESTED_VCPUS = 16


def build_context(query, storage, settings, operator_stats, latency_ms, vm=None):
    """
    Collects the facts about one executed query that the recommender needs:
    the query's shape, the table sizes and what each operator cost.
    """
    table_rows = {}
    pushable = 0
    left = storage.get_table(query.table)
    table_rows[left.name] = len(left.rows)
    right = None
    if query.join:
        right = storage.get_table(query.join["table"])
        table_rows[right.name] = len(right.rows)
        if query.where:
            left_c, right_c, _ = split_where_conditions(query.where, left.columns, right.columns)
            pushable = len(left_c) + len(right_c)

    return {
        "vm": vm,                       # None, or what VMController.snapshot() returned
        "settings": settings,
        "latency_ms": latency_ms,
        "operator_stats": operator_stats or [],
        "has_where": bool(query.where),
        "has_join": bool(query.join),
        "has_group": bool(query.group_by),
        "has_aggregate": any(c["agg"] for c in query.columns),
        "has_order": bool(query.order_by),
        "table_rows": table_rows,
        "left_table": left.name,
        "right_table": right.name if right else None,
        "pushable_conditions": pushable,
        "where_count": len(query.where or []),
    }


def _find_steps(context, prefix):
    return [s for s in context["operator_stats"] if s["op"].startswith(prefix)]


def _share_text(context, prefixes):
    """'HASH_GROUP_BY took 81% of the query time (165.2 ms of 204.0 ms)' or ''."""
    total = sum(s["ms"] for s in context["operator_stats"]) or 0
    if not total:
        return ""
    best = None
    for prefix in prefixes:
        for step in _find_steps(context, prefix):
            if best is None or step["ms"] > best["ms"]:
                best = step
    if best is None:
        return ""
    return "%s took %.0f%% of the operator time (%.1f ms of %.1f ms). " % (
        best["op"], 100.0 * best["ms"] / total, best["ms"], total)


def _action(action_id, title, detail, reduces, changes=None, kind="setting", experimental=False, tag=None):
    return {
        "tag": tag,                   # optional text shown instead of the arrows (used by VM actions)
        "id": action_id,
        "kind": kind,                 # "setting" = can be applied with `apply`; "manual" = edit SQL/data
        "title": title,
        "detail": detail,
        "reduces": list(reduces),
        "changes": changes,           # settings change for "setting" actions, else None
        "experimental": experimental,
    }


def recommend_actions(classification, context):
    """
    Returns a ranked list of action dicts for this bottleneck and query.
    Actions that are already switched on, or that do not fit the query, are left out.
    """
    settings = context["settings"]
    rows = context["table_rows"]
    candidates = []

    # --- engine settings (apply automatically) ------------------------------

    if context["has_join"] and context["pushable_conditions"] and not settings.pushdown_filter:
        candidates.append(_action(
            "pushdown_filter", "Filter each table before the join",
            _share_text(context, ["HASH_JOIN"]) +
            "%d of %d WHERE condition(s) only touch one table, so they can run BEFORE the join. "
            "The join then builds and probes fewer rows and creates fewer merged rows."
            % (context["pushable_conditions"], context["where_count"]),
            ("CPU", "RAM", "Latency"), {"pushdown_filter": True}))

    if context["has_join"] and not settings.build_smaller_side:
        left_n, right_n = rows[context["left_table"]], rows[context["right_table"]]
        if right_n > left_n:
            candidates.append(_action(
                "build_smaller_side", "Build the join hash table on the smaller table",
                "The hash table is currently built on %s (%d rows) although %s has only %d. "
                "Building on the smaller side shrinks the hash table and speeds up the join."
                % (context["right_table"], right_n, context["left_table"], left_n),
                ("RAM", "Latency", "CPU"), {"build_smaller_side": True}))

    if (context["has_group"] or context["has_aggregate"]) and not settings.streaming_aggregation:
        candidates.append(_action(
            "streaming_aggregation", "Use streaming aggregation",
            _share_text(context, ["HASH_GROUP_BY", "AGGREGATE"]) +
            "Streaming keeps a few running totals per group instead of holding every row of "
            "every group in memory, so it needs less RAM and one pass instead of two.",
            ("RAM", "CPU", "Latency"), {"streaming_aggregation": True}))

    if context["has_where"] and not settings.compiled_predicates:
        candidates.append(_action(
            "compiled_predicates", "Use compiled predicates in filters",
            _share_text(context, ["FILTER"]) +
            "Compiled predicates inline the comparison in the filter loop instead of making a "
            "Python function call for every row, which cuts CPU time and latency.",
            ("CPU", "Latency"), {"compiled_predicates": True}))

    if settings.thread_count > 1:
        candidates.append(_action(
            "reduce_threads", "Reduce threads to 1",
            "Python threads share one interpreter lock (the GIL), so extra threads add "
            "scheduling and copying overhead without running the filter in parallel.",
            ("CPU", "Latency"), {"thread_count": 1}))
    elif (classification == "CPU-bound" and context["has_where"]
            and max(rows.values()) >= _LARGE_TABLE_ROWS):
        candidates.append(_action(
            "try_threads", "Experiment: try 2 threads for the filter",
            "The original resource experiment. Because of the GIL the gain is usually small or "
            "negative, so treat this as a measurement, not a fix - `apply` will show the result.",
            ("Latency",), {"thread_count": 2}, experimental=True))

    # --- virtual machine resources (only when a VM controller is set up) -----------

    vm = context.get("vm")
    if vm and vm.get("enabled"):
        cpus, memory = vm["vcpus"], vm["memory_mb"]
        where = "VM '%s' (%d vCPUs, %d MB)" % (vm.get("vm_name", "vm"), cpus, memory)
        if classification == "CPU-bound" and cpus < MAX_SUGGESTED_VCPUS:
            candidates.append(_action(
                "vm_more_vcpus", "Give the VM more vCPUs: %d -> %d" % (cpus, cpus * 2),
                "The run is CPU-bound on %s. More vCPUs only help if the work can spread across "
                "cores; this engine runs mostly on one core, so the gain may be small - "
                "apply it to measure, then `vm restore` if it did not pay off." % where,
                ("Latency",), {"vm_vcpus": cpus * 2}, kind="vm", experimental=True,
                tag="adds CPU capacity"))
        if classification == "Memory pressure":
            target = int(round(memory * 1.5 / 256.0)) * 256
            candidates.append(_action(
                "vm_more_memory", "Give the VM more memory: %d MB -> %d MB" % (memory, target),
                "Memory is the limit on %s. More guest memory stops the guest from running out "
                "or swapping; it does not make the query itself use less." % where,
                ("RAM",), {"vm_memory_mb": target}, kind="vm", tag="more memory headroom"))
        if classification == "Parallel/resource saturation" and cpus > 1:
            candidates.append(_action(
                "vm_fewer_vcpus", "Take vCPUs away from the VM: %d -> %d" % (cpus, max(1, cpus // 2)),
                "Extra parallelism is not paying off on %s. Giving the vCPUs back saves resources "
                "for other VMs; compare to confirm latency does not get worse." % where,
                ("CPU",), {"vm_vcpus": max(1, cpus // 2)}, kind="vm", tag="frees vCPUs"))

    # --- manual changes (edit the SQL or the data) -----------------------------

    filters = _find_steps(context, "FILTER")
    filter_in = sum(s["rows_in"] for s in filters)
    filter_out = sum(s["rows_out"] for s in filters)
    weak_filter = bool(filters) and filter_in > 0 and filter_out / float(filter_in) > 0.5
    big_scan = max(rows.values()) >= _LARGE_TABLE_ROWS

    if big_scan and (not context["has_where"] or weak_filter):
        why = ("The query has no WHERE condition" if not context["has_where"]
               else "The WHERE condition keeps %.0f%% of the rows" % (100.0 * filter_out / filter_in))
        candidates.append(_action(
            "narrow_where", "Edit the SQL: add a more selective WHERE condition",
            why + ", so every later step handles most of the table. Filtering on a column that "
            "removes more rows (a specific region, a higher salary threshold, a date range) "
            "reduces scan, CPU and memory together.",
            ("CPU", "RAM", "Latency"), kind="manual"))

    sorts = _find_steps(context, "SORT")
    if sorts and sorts[0]["rows_in"] >= _BIG_SORT_ROWS:
        candidates.append(_action(
            "shrink_sort", "Edit the SQL: sort fewer rows",
            "ORDER BY sorts %d rows. Add a WHERE condition or an aggregate (GROUP BY) so the "
            "sort only sees the rows you need." % sorts[0]["rows_in"],
            ("CPU", "Latency"), kind="manual"))

    if classification == "Memory pressure":
        candidates.append(_action(
            "reduce_result_size", "Edit the SQL: return fewer rows",
            "Memory use grows with the number of rows the query keeps. Add a WHERE condition or "
            "aggregate the data so the result set is smaller; also close other programs "
            "or reduce the VM's concurrent workload.",
            ("RAM",), kind="manual"))

    # --- rank by what matters for this bottleneck ----------------------------------
    focus = BOTTLENECK_FOCUS.get(classification, BOTTLENECK_FOCUS["Mixed"])

    def score(action):
        total = sum(w for metric, w in zip(focus, _FOCUS_WEIGHTS) if metric in action["reduces"])
        if classification == "Parallel/resource saturation" and action["id"] == "reduce_threads":
            total += 100
        if action["kind"] == "vm" and not action["experimental"]:
            total += 60     # the VM fix for memory pressure / saturation is the main answer
        if action["experimental"]:
            total -= 50
        if action["kind"] == "manual":
            total -= 5      # prefer the one-command fixes, but still list manual advice
        return total

    candidates.sort(key=score, reverse=True)
    for rank, action in enumerate(candidates, 1):
        action["rank"] = rank
    return candidates
