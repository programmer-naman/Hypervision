"""
classifier.py
-------------
A rule-based bottleneck classifier -- basically a set of if/elif checks
on the resource numbers collected for one experiment. This matches the
five classes described in the project report:

  CPU-bound, Memory pressure, I/O-bound, Parallel/resource saturation, Mixed

The threshold numbers below (70%, 500 MB, etc.) are simple constants
tuned for a laptop-scale demo. Feel free to adjust them if your machine
behaves differently -- that is exactly why they are defined as constants
at the top of the file instead of being buried in the logic.
"""

CPU_BOUND_CPU_THRESHOLD = 70          # percent
MEMORY_PRESSURE_MB_THRESHOLD = 500    # megabytes
IO_BOUND_LATENCY_MS_THRESHOLD = 500   # milliseconds
IO_BOUND_CPU_CEILING = 30             # percent
SATURATION_CPU_CEILING = 40           # percent


def classify(resource_summary, latency_ms, thread_count):
    peak_cpu = resource_summary.get("peak_cpu_percent", 0)
    peak_memory = resource_summary.get("peak_memory_mb", 0)

    if peak_cpu >= CPU_BOUND_CPU_THRESHOLD and peak_memory < MEMORY_PRESSURE_MB_THRESHOLD:
        classification = "CPU-bound"
        confidence = min(0.99, 0.5 + peak_cpu / 200)

    elif peak_memory >= MEMORY_PRESSURE_MB_THRESHOLD:
        classification = "Memory pressure"
        confidence = min(0.95, 0.4 + peak_memory / 2000)

    elif peak_cpu < IO_BOUND_CPU_CEILING and latency_ms > IO_BOUND_LATENCY_MS_THRESHOLD:
        classification = "I/O-bound"
        confidence = 0.6

    elif thread_count > 1 and peak_cpu < SATURATION_CPU_CEILING:
        classification = "Parallel/resource saturation"
        confidence = 0.55

    else:
        classification = "Mixed"
        confidence = 0.5

    return {"classification": classification, "confidence": round(confidence, 2)}
