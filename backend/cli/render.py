"""
render.py
---------
Prints experiment results, recommendations and before/after comparisons.
"""

from mini_dbms.settings import ExecutionSettings
from cli.ui import (paint, bold, dim, red, green, yellow, cyan,
                    heading, print_table)

TAG_COLORS = {            # same five classes as the dashboard tags
    "CPU-bound": "31",
    "Memory pressure": "35",
    "I/O-bound": "34",
    "Parallel/resource saturation": "33",
    "Mixed": "36",
}


def settings_of(record):
    return ExecutionSettings.from_dict(
        record.get("settings") or {"thread_count": record.get("thread_count", 1)})


def render(data):
    """Prints one full experiment record."""
    resource = data["resource"]
    peak_cpu = resource.get("peak_cpu_percent", 0)
    peak_mem = resource.get("peak_memory_mb", 0)

    heading("%s" % data["experiment_id"])
    print("  Latency: %s   CPU time: %s   Peak CPU: %s   Peak RAM: %s" % (
        bold("%.1f ms" % data["latency_ms"]),
        bold("%.1f ms" % data["cpu_time_ms"]) if data.get("cpu_time_ms") is not None else bold("n/a"),
        bold("%s%%" % peak_cpu),
        bold("%s MB" % peak_mem)))
    print(dim("  Settings: " + settings_of(data).describe()))

    heading("Query Plan")
    for i, step in enumerate(data["plan"], 1):
        print("  %d. %s" % (i, step))
    print()
    print_table(
        ["Operator", "Rows in", "Rows out", "Time"],
        [(s["op"], s["rows_in"], s["rows_out"], "%s ms" % s["ms"])
         for s in data["operator_stats"]])

    heading("Bottleneck Analysis")
    cls = data["classification"]["classification"]
    print("  " + bold(paint("[ %s ]" % cls, TAG_COLORS.get(cls, "36"))))
    print("  Confidence: %.0f%%" % (data["classification"]["confidence"] * 100))
    print(dim("  Peak CPU %s%% · Peak RAM %s MB · Latency %.1f ms"
              % (peak_cpu, peak_mem, data["latency_ms"])))

    render_recommendations(data)

    heading("Event Timeline")
    for ev in data["timeline"]:
        if ev["type"] == "resource_event":
            detail = "%s resource samples captured" % ev["count"]
        else:
            detail = "%s → %s rows, %s ms" % (ev["rows_in"], ev["rows_out"], ev["ms"])
        print("  • %s  %s" % (bold(ev["label"]), dim(detail)))

    heading("Xen / VMI Status (simulated)")
    print("  Domain: local-sim-domu (simulated)")
    print("  VMI events observed: %s" % data["vmi_events"])
    print("  Resource samples: %s" % resource["sample_count"])
    print("  Simulated vCPU/threads: %s" % data["thread_count"])

    if data.get("comparison"):
        render_comparison(data["comparison"], data.get("applied_actions"))

    heading("Result Preview")
    render_result_table(data["rows_preview"])


def render_recommendations(data):
    """Prints the numbered, ranked recommendations of one experiment."""
    heading("Recommendations")
    print("  " + data["recommendation"])
    actions = data.get("recommendations") or []

    if not actions:
        print(dim("  No further automatic suggestions for this query and these settings."))
        print(dim("  Rerun with a different thread count:  rerun <threads>"))
        return

    print(dim("  Ranked for this bottleneck. ↓ = what the action should reduce."))
    print()
    for a in actions:
        tags = a.get("tag") or " ".join("↓" + m for m in a["reduces"])
        if a["kind"] == "vm":
            kind = paint("[apply · VM]", "35") + (yellow(" experiment") if a.get("experimental") else "")
        elif a["kind"] == "setting":
            kind = green("[apply]") if not a.get("experimental") else yellow("[apply · experiment]")
        else:
            kind = cyan("[edit SQL]")
        print("  %s %s  %s  %s" % (bold("[%d]" % a["rank"]), bold(a["title"]), dim(tags), kind))
        for line in _wrap(a["detail"], 74):
            print("      " + line)
    print()
    print(dim("  Apply and compare:  apply 1   |   apply 1 2   |   apply all"))
    print(dim("  [edit SQL] items are changes to your query: edit it, run it, then `compare EXP-a EXP-b`."))
    if any(a["kind"] == "vm" for a in actions):
        print(dim("  [apply · VM] items resize the virtual machine itself (`apply all` never does this; pick its number)."))


def _wrap(text, width):
    words, lines, line = text.split(), [], ""
    for w in words:
        if line and len(line) + 1 + len(w) > width:
            lines.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        lines.append(line)
    return lines


def _fmt(value, unit):
    if value is None:
        return "n/a"
    return ("%.1f %s" % (value, unit)).strip() if unit != "%" else "%.1f%%" % value


def render_comparison(comp, applied=None):
    """Prints the before/after table produced by analysis.comparator.compare."""
    heading("Comparison: %s → %s" % (comp.get("before_id") or "before", comp.get("after_id") or "after"))

    before = ExecutionSettings.from_dict(comp.get("before_settings") or {})
    after = ExecutionSettings.from_dict(comp.get("after_settings") or {})
    changed = before.diff(after)
    if applied:
        print("  Applied: " + ", ".join(applied))
    if comp.get("environment_note"):
        print("  VM change: " + comp["environment_note"])
    if changed:
        print(dim("  Settings changed: " + ", ".join(
            "%s %s → %s" % (k, _setting_text(a), _setting_text(b)) for k, (a, b) in changed.items())))
    print(dim("  Median of %s run(s) per side, changes under %.0f%% count as 'same'."
              % (comp.get("runs_after", 1), comp.get("noise_pct", 5))))
    print()

    rows = []
    for m in comp["metrics"]:
        if m["change_pct"] is None:
            change = "n/a"
        else:
            change = "%+.1f%%" % m["change_pct"]
        mark = {"better": green("better ✔"), "worse": red("worse ✘"),
                "same": dim("same ≈"), "n/a": dim("n/a")}[m["verdict"]]
        label = m["label"] + ("" if m["bucket"] else " *")
        rows.append((label, _fmt(m["before"], m["unit"]), _fmt(m["after"], m["unit"]), change, mark))
    print_table(["Metric", "Before", "After", "Change", "Result"], rows)
    print(dim("  * informational (sampled from the whole machine; noisy)"))

    print()
    parts = []
    for bucket in ("CPU", "RAM", "Latency"):
        v = comp["summary"].get(bucket, "n/a")
        word = {"better": green("reduced"), "worse": red("increased"),
                "same": dim("unchanged"), "n/a": dim("n/a")}[v]
        parts.append("%s %s" % (bold(bucket), word))
    print("  " + "   ".join(parts))

    if comp.get("results_match") is True:
        print("  " + green("Query results are identical before and after."))
    elif comp.get("results_match") is False:
        print("  " + red("WARNING: the two runs returned different results!"))

    verdict = comp["overall"]
    if comp.get("simulated_only"):
        print("  " + yellow("Dry run: nothing real was changed, so any difference above is only "
                            "measurement noise. Use a real backend (xen / kvm) to measure an effect."))
    elif verdict == "better":
        print("  " + green("Verdict: the change helped."))
    elif verdict == "worse":
        print("  " + red("Verdict: the change made things worse — type `undo` to revert it."))
    elif verdict == "mixed":
        print("  " + yellow("Verdict: mixed result (some metrics better, some worse) — keep or `undo` as you prefer."))
    else:
        print("  " + dim("Verdict: no measurable difference for this query."))


def _setting_text(value):
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


def render_result_table(rows):
    if not rows:
        print(dim("  Query returned no rows."))
        return
    cols = list(rows[0].keys())
    print_table(cols, [[("" if r.get(c) is None else r.get(c)) for c in cols] for r in rows])
    print(dim("  Showing up to 50 rows."))
