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
      "setting" actions carry the exact settings change, so the web
      dashboard and terminal can apply them and rerun the experiment to compare.

Every "setting" action maps to a real engine optimization (see
mini_dbms/settings.py) and was checked to return identical query results.
"""

from __future__ import annotations
import re
from typing import Any, Dict, List, Optional, Tuple

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


def recommend(classification: str) -> str:
    return RECOMMENDATIONS.get(classification, DEFAULT_RECOMMENDATION)


# ---------------------------------------------------------------------------
# Layer 2: concrete actions
# ---------------------------------------------------------------------------

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


def build_context(query, storage, settings, operator_stats, latency_ms, vm=None, sql=None):
    """
    Collects the facts about one executed query that the recommender needs:
    the query's shape, the table sizes, and what each operator cost.
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
        "sql": sql,
        "vm": vm,                       # None, or what VMController.snapshot() returned
        "settings": settings,
        "latency_ms": latency_ms,
        "operator_stats": operator_stats or [],
        "has_where": bool(query.where),
        "has_join": bool(query.join),
        "has_group": bool(query.group_by),
        "has_aggregate": any(c.get("agg") for c in query.columns),
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


def _action(action_id, title, detail, reduces, changes=None, kind="setting", experimental=False, tag=None,
            observation="", reason="", proposed_sql=None, current_sql=None, safe=True, impact_estimation=None):
    reduces_list = list(reduces)
    impact_badges = [f"↓ {m}" for m in reduces_list]
    if kind == "setting":
        action_type = "apply"
        can_apply_all = not experimental and action_id != "try_threads"
    elif kind == "vm":
        action_type = "vm"
        can_apply_all = False
    else:
        action_type = "edit_sql" if proposed_sql else "manual"
        can_apply_all = False

    return {
        "tag": tag,
        "id": action_id,
        "kind": kind,
        "action_type": action_type,
        "can_apply_all": can_apply_all,
        "title": title,
        "detail": detail,
        "reduces": reduces_list,
        "impact_badges": impact_badges,
        "expected_impact": ", ".join(impact_badges) if impact_badges else (tag or "Optimized execution"),
        "changes": changes,
        "experimental": experimental,
        "observation": observation or "",
        "reason": reason or detail,
        "proposed_sql": proposed_sql,
        "current_sql": current_sql,
        "safe": safe,
        "impact_estimation": impact_estimation or ("Measured" if observation else "Estimated"),
    }


def recommend_actions(classification: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Returns a ranked list of action dicts for this bottleneck and query.
    Actions that are already switched on, or that do not fit the query, are left out.
    """
    settings = context["settings"]
    rows = context["table_rows"]
    candidates = []

    # --- engine settings (apply automatically) ------------------------------

    if context["has_join"] and context["pushable_conditions"] and not settings.pushdown_filter:
        obs = _share_text(context, ["HASH_JOIN"])
        reason = (
            f"{context['pushable_conditions']} of {context['where_count']} WHERE condition(s) only touch one table, "
            "so they can run BEFORE the join. The join then builds and probes fewer rows and creates fewer merged rows."
        )
        candidates.append(_action(
            "pushdown_filter", "Filter each table before the join",
            obs + reason,
            ("CPU", "RAM", "Latency"), {"pushdown_filter": True},
            observation=obs.strip(), reason=reason
        ))

    if context["has_join"] and not settings.build_smaller_side:
        left_n, right_n = rows[context["left_table"]], rows[context["right_table"]]
        if right_n > left_n:
            reason = (
                f"The hash table is currently built on {context['right_table']} ({right_n} rows) although "
                f"{context['left_table']} has only {left_n}. Building on the smaller side shrinks the hash table and speeds up the join."
            )
            candidates.append(_action(
                "build_smaller_side", "Build the join hash table on the smaller table",
                reason,
                ("RAM", "Latency", "CPU"), {"build_smaller_side": True},
                reason=reason
            ))

    if (context["has_group"] or context["has_aggregate"]) and not settings.streaming_aggregation:
        obs = _share_text(context, ["HASH_GROUP_BY", "AGGREGATE"])
        reason = (
            "Streaming keeps a few running totals per group instead of holding every row of "
            "every group in memory, so it needs less RAM and one pass instead of two."
        )
        candidates.append(_action(
            "streaming_aggregation", "Use streaming aggregation",
            obs + reason,
            ("RAM", "CPU", "Latency"), {"streaming_aggregation": True},
            observation=obs.strip(), reason=reason
        ))

    if context["has_where"] and not settings.compiled_predicates:
        obs = _share_text(context, ["FILTER"])
        reason = (
            "Compiled predicates inline the comparison directly inside the filter loop instead of making a "
            "Python function call for every row, which cuts CPU time and latency."
        )
        candidates.append(_action(
            "compiled_predicates", "Use compiled predicates in filters",
            obs + reason,
            ("CPU", "Latency"), {"compiled_predicates": True},
            observation=obs.strip(), reason=reason
        ))

    if settings.thread_count > 1:
        reason = (
            "Python threads share one interpreter lock (the GIL), so extra threads add "
            "scheduling and copying overhead without running the filter in parallel."
        )
        candidates.append(_action(
            "reduce_threads", "Reduce threads to 1",
            reason,
            ("CPU", "Latency"), {"thread_count": 1},
            reason=reason
        ))
    elif (classification == "CPU-bound" and context["has_where"]
            and max(rows.values()) >= _LARGE_TABLE_ROWS):
        reason = (
            "The original resource experiment. Because of the GIL the gain is usually small or "
            "negative, so treat this as a measurement, not a fix - `apply` will show the result."
        )
        candidates.append(_action(
            "try_threads", "Experiment: try 2 threads for the filter",
            reason,
            ("Latency",), {"thread_count": 2}, experimental=True,
            reason=reason
        ))

    # --- virtual machine resources (only when a VM controller is set up) -----------

    vm = context.get("vm")
    if vm and vm.get("enabled") and isinstance(vm.get("vcpus"), (int, float)) and isinstance(vm.get("memory_mb"), (int, float)):
        cpus, memory = int(vm["vcpus"]), int(vm["memory_mb"])
        where = "VM '%s' (%d vCPUs, %d MB)" % (vm.get("vm_name", "vm"), cpus, memory)
        if classification == "CPU-bound" and cpus < MAX_SUGGESTED_VCPUS:
            reason = (
                f"The run is CPU-bound on {where}. More vCPUs only help if the work can spread across "
                "cores; this engine runs mostly on one core, so the gain may be small - "
                "apply it to measure, then restore if it did not pay off."
            )
            candidates.append(_action(
                "vm_more_vcpus", "Give the VM more vCPUs: %d -> %d" % (cpus, cpus * 2),
                reason,
                ("Latency",), {"vm_vcpus": cpus * 2}, kind="vm", experimental=True,
                tag="adds CPU capacity", reason=reason
            ))
        if classification == "Memory pressure":
            target = int(round(memory * 1.5 / 256.0)) * 256
            reason = (
                f"Memory is the limit on {where}. More guest memory stops the guest from running out "
                "or swapping; it does not make the query itself use less."
            )
            candidates.append(_action(
                "vm_more_memory", "Give the VM more memory: %d MB -> %d MB" % (memory, target),
                reason,
                ("RAM",), {"vm_memory_mb": target}, kind="vm", tag="more memory headroom",
                reason=reason
            ))
        if classification == "Parallel/resource saturation" and cpus > 1:
            reason = (
                f"Extra parallelism is not paying off on {where}. Giving the vCPUs back saves resources "
                "for other VMs; compare to confirm latency does not get worse."
            )
            candidates.append(_action(
                "vm_fewer_vcpus", "Take vCPUs away from the VM: %d -> %d" % (cpus, max(1, cpus // 2)),
                reason,
                ("CPU",), {"vm_vcpus": max(1, cpus // 2)}, kind="vm", tag="frees vCPUs",
                reason=reason
            ))

    # --- manual changes (edit the SQL or the data) -----------------------------

    filters = _find_steps(context, "FILTER")
    filter_in = sum(s["rows_in"] for s in filters)
    filter_out = sum(s["rows_out"] for s in filters)
    weak_filter = bool(filters) and filter_in > 0 and filter_out / float(filter_in) > 0.5
    big_scan = max(rows.values()) >= _LARGE_TABLE_ROWS

    if big_scan and (not context["has_where"] or weak_filter):
        pct_kept = (100.0 * filter_out / filter_in) if filter_in > 0 else 100
        why = ("The query has no WHERE condition" if not context["has_where"]
               else "The WHERE condition keeps %.0f%% of the rows" % pct_kept)
        obs = f"Filter kept {filter_out:,} of {filter_in:,} rows ({pct_kept:.0f}%)." if filter_in > 0 else "Full table scan across all rows."
        reason = (
            why + ", so every later step handles most of the table. Filtering on a column that "
            "removes more rows (a specific region, a higher salary threshold, a date range) "
            "reduces scan, CPU and memory together."
        )

        # Propose improved SQL if original SQL is available
        orig_sql = (context.get("sql") or "").strip().rstrip(";").strip()
        proposed_sql = None
        if orig_sql:
            if re.search(r"\bsalary\s*>\s*\d+", orig_sql, re.IGNORECASE):
                proposed_sql = re.sub(r"\bsalary\s*>\s*\d+", "salary > 85000", orig_sql, flags=re.IGNORECASE)
            elif re.search(r"\bamount\s*>\s*\d+", orig_sql, re.IGNORECASE):
                proposed_sql = re.sub(r"\bamount\s*>\s*\d+", "amount > 4000", orig_sql, flags=re.IGNORECASE)
            elif "WHERE" not in orig_sql.upper():
                match = re.search(r"(FROM\s+[A-Za-z0-9_]+(?:\s+JOIN\s+[A-Za-z0-9_]+\s+ON\s+[^;]+)?)(\s+GROUP\s+BY|\s+ORDER\s+BY|\s+LIMIT|$)", orig_sql, re.IGNORECASE | re.DOTALL)
                if match:
                    proposed_sql = orig_sql[:match.end(1)] + " WHERE salary > 75000" + orig_sql[match.end(1):]
            if not proposed_sql and orig_sql:
                proposed_sql = orig_sql + " WHERE salary > 85000"

        candidates.append(_action(
            "narrow_where", "Edit the SQL: add a more selective WHERE condition",
            reason,
            ("CPU", "RAM", "Latency"), kind="manual",
            observation=obs, reason=reason, proposed_sql=proposed_sql, current_sql=orig_sql
        ))

    sorts = _find_steps(context, "SORT")
    if sorts and sorts[0]["rows_in"] >= _BIG_SORT_ROWS:
        reason = (
            "ORDER BY sorts %d rows. Add a WHERE condition or an aggregate (GROUP BY) so the "
            "sort only sees the rows you need." % sorts[0]["rows_in"]
        )
        candidates.append(_action(
            "shrink_sort", "Edit the SQL: sort fewer rows",
            reason,
            ("CPU", "Latency"), kind="manual",
            observation=f"SORT processed {sorts[0]['rows_in']:,} rows.", reason=reason
        ))

    if classification == "Memory pressure":
        reason = (
            "Memory use grows with the number of rows the query keeps. Add a WHERE condition or "
            "aggregate the data so the result set is smaller; also close other programs "
            "or reduce the VM's concurrent workload."
        )
        candidates.append(_action(
            "reduce_result_size", "Edit the SQL: return fewer rows",
            reason,
            ("RAM",), kind="manual",
            reason=reason
        ))

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
