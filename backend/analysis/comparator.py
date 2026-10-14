"""
comparator.py
-------------
Compares two experiment runs ("before" and "after" a change) on the three
things the recommendations try to reduce:

    CPU      -> cpu_time_ms   (CPU seconds the process burned during the query)
    RAM      -> heap_peak_mb  (peak Python memory used by the query itself)
    Latency  -> latency_ms    (how long the query took)

plus two informational numbers from the resource monitor (peak sampled CPU %
and peak process memory). Lower is better for every metric. A change smaller
than `noise_pct` is reported as "same", because single measurements jitter.
"""

import statistics

# key, label, unit, which summary bucket it feeds (None = informational only)
METRIC_TABLE = [
    ("latency_ms", "Latency", "ms", "Latency"),
    ("cpu_time_ms", "CPU time", "ms", "CPU"),
    ("heap_peak_mb", "Query memory (heap peak)", "MB", "RAM"),
    ("peak_cpu_percent", "Peak CPU (sampled, system)", "%", None),
    ("peak_memory_mb", "Process memory (RSS peak)", "MB", None),
]


def metrics_from_record(record):
    """Pulls the comparable numbers out of one saved experiment record."""
    resource = record.get("resource") or {}
    return {
        "experiment_id": record.get("experiment_id"),
        "settings": record.get("settings"),
        "latency_ms": record.get("latency_ms"),
        "cpu_time_ms": record.get("cpu_time_ms"),
        "heap_peak_mb": record.get("heap_peak_mb"),
        "peak_cpu_percent": resource.get("peak_cpu_percent"),
        "peak_memory_mb": resource.get("peak_memory_mb"),
        "row_count": record.get("row_count"),
        "result_fingerprint": record.get("result_fingerprint"),
    }


def _median(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 3) if values else None


def summarize_runs(records, heap_peak_mb=None, experiment_id=None, settings=None):
    """Median of several repeated runs of the same query + settings."""
    return {
        "experiment_id": experiment_id,
        "settings": settings,
        "latency_ms": _median(r.get("latency_ms") for r in records),
        "cpu_time_ms": _median(r.get("cpu_time_ms") for r in records),
        "heap_peak_mb": heap_peak_mb,
        "peak_cpu_percent": _median((r.get("resource") or {}).get("peak_cpu_percent") for r in records),
        "peak_memory_mb": _median((r.get("resource") or {}).get("peak_memory_mb") for r in records),
        "row_count": records[0].get("row_count"),
        "result_fingerprint": records[0].get("result_fingerprint"),
        "runs": len(records),
    }


def _verdict(before, after, noise_pct):
    if before is None or after is None:
        return None, "n/a"
    if before == 0:
        return (0.0, "same") if after == 0 else (None, "worse")
    change = (after - before) / float(before) * 100.0
    if change <= -noise_pct:
        return round(change, 1), "better"
    if change >= noise_pct:
        return round(change, 1), "worse"
    return round(change, 1), "same"


def compare(before, after, noise_pct=5.0):
    """
    `before` / `after` are metric dicts (metrics_from_record or summarize_runs).
    Returns the per-metric table, a CPU/RAM/Latency summary, and whether the
    two runs returned the same result.
    """
    rows = []
    summary = {}
    for key, label, unit, bucket in METRIC_TABLE:
        b, a = before.get(key), after.get(key)
        change, verdict = _verdict(b, a, noise_pct)
        rows.append({"key": key, "label": label, "unit": unit, "bucket": bucket,
                     "before": b, "after": a, "change_pct": change, "verdict": verdict})
        if bucket:
            summary[bucket] = verdict

    fp_b, fp_a = before.get("result_fingerprint"), after.get("result_fingerprint")
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

    return {
        "before_id": before.get("experiment_id"),
        "after_id": after.get("experiment_id"),
        "before_settings": before.get("settings"),
        "after_settings": after.get("settings"),
        "runs_before": before.get("runs", 1),
        "runs_after": after.get("runs", 1),
        "noise_pct": noise_pct,
        "metrics": rows,
        "summary": summary,
        "overall": overall,
        "results_match": results_match,
    }
