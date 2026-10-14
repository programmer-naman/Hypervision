"""
correlator.py
-------------
Combines two kinds of events that happened during the same experiment:

  1. "db_event"       -- one row per operator step from the query
                          (SCAN, FILTER, HASH_JOIN, ...), with timing.
  2. "resource_event"  -- a summary of how many resource samples the
                          ResourceMonitor collected while the query ran.

The result is a simple ordered "timeline" that the dashboard displays,
which is exactly what the report calls "correlating DB events with
resource observations using timestamps and experiment IDs".
"""


def correlate(experiment_id, db_stats, resource_samples):
    timeline = []

    for step in db_stats.steps:
        timeline.append({
            "type": "db_event",
            "label": step["op"],
            "rows_in": step["rows_in"],
            "rows_out": step["rows_out"],
            "ms": step["ms"],
        })

    if resource_samples:
        timeline.append({
            "type": "resource_event",
            "label": "RESOURCE_SAMPLING",
            "count": len(resource_samples),
        })

    return {"experiment_id": experiment_id, "timeline": timeline}
