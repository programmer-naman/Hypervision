"""
comparator.py
-------------
Compares two experiment runs ("before" and "after" a change) on the three
things the recommendations try to reduce:

    CPU      -> cpu_time_ms   (CPU seconds the process burned during the query)
    RAM      -> heap_peak_mb  (peak Python memory used by the query itself)
    Latency  -> latency_ms    (how long the query took)

plus informational system measurements (peak sampled CPU %, peak process memory,
rows processed, worker threads, and VM vCPU/RAM envelope). Lower is better for
resource/latency metrics. A change smaller than `noise_pct` is reported as
"same", because single measurements jitter.
"""

from __future__ import annotations
import statistics
from typing import Any, Dict, List, Optional, Tuple, Union

# key, label, unit, which summary bucket it feeds (None = informational only)
METRIC_TABLE = [
    ("latency_ms", "Latency", "ms", "Latency"),
    ("cpu_time_ms", "CPU time", "ms", "CPU"),
    ("heap_peak_mb", "Query memory (heap peak)", "MB", "RAM"),
    ("peak_cpu_percent", "Peak CPU (sampled, system)", "%", None),
    ("peak_memory_mb", "Process memory (RSS peak)", "MB", None),
    ("rows_processed", "Rows processed", "rows", None),
    ("worker_threads", "Worker threads", "threads", None),
    ("vm_vcpus", "VM vCPU", "vCPUs", None),
    ("vm_memory_mb", "VM RAM", "MB", None),
]

DIFF_METRICS = {"rows_processed", "worker_threads", "vm_vcpus", "vm_memory_mb"}


def metrics_from_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Pulls the comparable numbers out of one saved experiment record."""
    resource = record.get("resource") or {}
    envelope = record.get("resource_envelope") or record.get("vm_config") or {}
    settings = record.get("settings") or {}
    thread_count = settings.get("thread_count") or record.get("thread_count") or 1
    return {
        "experiment_id": record.get("experiment_id"),
        "settings": settings,
        "latency_ms": record.get("latency_ms"),
        "cpu_time_ms": record.get("cpu_time_ms"),
        "heap_peak_mb": record.get("heap_peak_mb"),
        "peak_cpu_percent": resource.get("peak_cpu_percent"),
        "peak_memory_mb": resource.get("peak_memory_mb"),
        "rows_processed": record.get("row_count"),
        "row_count": record.get("row_count"),
        "worker_threads": thread_count,
        "vm_vcpus": envelope.get("vcpus"),
        "vm_memory_mb": envelope.get("memory_mb"),
        "result_fingerprint": record.get("result_fingerprint"),
        "sql": record.get("sql"),
        "plan": record.get("plan"),
        "applied_actions": record.get("applied_actions"),
    }


def _num(value):
    """Coerces a stored metric to a finite number (or None) so comparisons never crash."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return int(number) if isinstance(value, int) else number


def _median(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 3) if values else None


def summarize_runs(records: List[Dict[str, Any]], heap_peak_mb=None, experiment_id=None, settings=None, vm_config=None):
    """Median of several repeated runs of the same query + settings."""
    first = records[0] if records else {}
    env = vm_config or first.get("resource_envelope") or first.get("vm_config") or {}
    st = settings or first.get("settings") or {}
    thread_count = st.get("thread_count") if isinstance(st, dict) else getattr(st, "thread_count", 1)
    if thread_count is None:
        thread_count = first.get("thread_count", 1)

    return {
        "experiment_id": experiment_id or first.get("experiment_id"),
        "settings": st,
        "latency_ms": _median(r.get("latency_ms") for r in records),
        "cpu_time_ms": _median(r.get("cpu_time_ms") for r in records),
        "heap_peak_mb": heap_peak_mb,
        "peak_cpu_percent": _median((r.get("resource") or {}).get("peak_cpu_percent") for r in records),
        "peak_memory_mb": _median((r.get("resource") or {}).get("peak_memory_mb") for r in records),
        "rows_processed": first.get("row_count"),
        "row_count": first.get("row_count"),
        "worker_threads": thread_count,
        "vm_vcpus": env.get("vcpus"),
        "vm_memory_mb": env.get("memory_mb"),
        "result_fingerprint": first.get("result_fingerprint"),
        "sql": first.get("sql"),
        "plan": first.get("plan"),
        "applied_actions": first.get("applied_actions"),
        "runs": len(records),
    }


def _verdict(before, after, noise_pct):
    if before is None or after is None:
        return None, "N/A", "n/a"
    if before == 0:
        return ((0.0, "0.0%", "same") if after == 0 else (None, "N/A", "worse"))
    change = (after - before) / float(before) * 100.0
    change_disp = f"{change:+.1f}%"
    if change <= -noise_pct:
        return round(change, 1), change_disp, "better"
    if change >= noise_pct:
        return round(change, 1), change_disp, "worse"
    return round(change, 1), change_disp, "same"


def compare(before: Dict[str, Any], after: Dict[str, Any], noise_pct: float = 5.0) -> Dict[str, Any]:
    """
    `before` / `after` are metric dicts (metrics_from_record or summarize_runs).
    Returns the per-metric table, a CPU/RAM/Latency summary, and whether the
    two runs returned equivalent results.
    """
    rows = []
    summary = {}

    for entry in METRIC_TABLE:
        key, label, unit, bucket = entry[0], entry[1], entry[2], entry[3]
        b, a = _num(before.get(key)), _num(after.get(key))

        if key in DIFF_METRICS:
            if b is None and a is None:
                change_val = None
                change_disp = "N/A"
                verdict = "n/a"
                res_label = "N/A"
            else:
                b_val = b if b is not None else 0
                a_val = a if a is not None else 0
                diff = a_val - b_val
                change_val = diff
                change_disp = f"{int(diff):+d}" if float(diff).is_integer() else f"{diff:+.1f}"
                verdict = "informational"
                res_label = "Informational"
            rows.append({
                "key": key,
                "metric": key,
                "label": label,
                "unit": unit,
                "bucket": bucket,
                "before": b,
                "after": a,
                "change": change_disp,
                "change_pct": change_val,
                "change_display": change_disp,
                "verdict": verdict,
                "result": res_label,
            })
        else:
            change_pct, change_disp, verdict = _verdict(b, a, noise_pct)
            res_label = "Better" if verdict == "better" else ("Worse" if verdict == "worse" else ("Same" if verdict == "same" else "N/A"))
            rows.append({
                "key": key,
                "metric": key,
                "label": label,
                "unit": unit,
                "bucket": bucket,
                "before": b,
                "after": a,
                "change": change_disp,
                "change_pct": change_pct,
                "change_display": change_disp,
                "verdict": verdict,
                "result": res_label,
            })
            if bucket:
                summary[bucket] = verdict

    fp_b = before.get("result_fingerprint")
    fp_a = after.get("result_fingerprint")
    results_match = None if (fp_b is None or fp_a is None) else (fp_b == fp_a)

    verdicts = [v for v in summary.values() if v != "n/a"]
    if "worse" in verdicts and "better" in verdicts:
        overall = "mixed"
    elif "worse" in verdicts:
        overall = "worse"
    elif "better" in verdicts:
        overall = "better"
    else:
        overall = "same"

    # Improvement summary text
    improvements = []
    lat_row = next((r for r in rows if r["key"] == "latency_ms"), None)
    if lat_row and lat_row["change_pct"] is not None:
        c = lat_row["change_pct"]
        if c < -1:
            improvements.append(f"Latency improved by {-c:.1f}%")
        elif c > 1:
            improvements.append(f"Latency increased by {c:.1f}%")
        else:
            improvements.append("Latency remained steady")

    cpu_row = next((r for r in rows if r["key"] == "cpu_time_ms"), None)
    if cpu_row and cpu_row["change_pct"] is not None:
        c = cpu_row["change_pct"]
        if c < -1:
            improvements.append(f"CPU time reduced by {-c:.1f}%")

    mem_row = next((r for r in rows if r["key"] == "heap_peak_mb"), None)
    if mem_row and mem_row["change_pct"] is not None:
        c = mem_row["change_pct"]
        if c < -1:
            improvements.append(f"Query memory reduced by {-c:.1f}%")

    improvement_summary = "; ".join(improvements) if improvements else "Comparison completed."

    return {
        "before_id": before.get("experiment_id"),
        "after_id": after.get("experiment_id"),
        "before_sql": before.get("sql"),
        "after_sql": after.get("sql"),
        "before_plan": before.get("plan"),
        "after_plan": after.get("plan"),
        "before_settings": before.get("settings"),
        "after_settings": after.get("settings"),
        "applied_actions": after.get("applied_actions") or [],
        "runs_before": before.get("runs", 1),
        "runs_after": after.get("runs", 1),
        "noise_pct": noise_pct,
        "metrics": rows,
        "summary": summary,
        "overall": overall,
        "results_match": results_match,
        "improvement_summary": improvement_summary,
    }
