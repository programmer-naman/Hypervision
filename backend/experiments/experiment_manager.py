"""
experiment_manager.py
----------------------
Ties everything together for one "experiment" (one query run):

  1. parse the SQL text
  2. build the query plan
  3. start resource monitoring and connect to the (simulated) VMI adapter
  4. execute the query using the mini DBMS (with the chosen ExecutionSettings)
  5. stop resource monitoring
  6. classify the bottleneck and generate concrete recommendations
  7. save everything into a JSON log file

New in this version: `compare_settings` applies a settings change and reruns
the same query side by side with the original settings (several interleaved
runs, medians compared) so you can see whether a recommendation really
reduced CPU time, memory and latency.

Every saved experiment gets a unique ID like EXP-1001, EXP-1002, ...
"""

import csv
import gc
import hashlib
import json
import os
import time
import tracemalloc
from datetime import datetime, timezone

from mini_dbms import parser as sql_parser
from mini_dbms.executor import execute
from mini_dbms.optimizer import build_plan
from mini_dbms.settings import ExecutionSettings
from monitor.resource_monitor import ResourceMonitor
from monitor.vmi_adapter import get_vmi_adapter
from analysis.correlator import correlate
from analysis.classifier import classify
from analysis.recommender import recommend, recommend_actions, build_context
from analysis import comparator

MAX_SAVED_EXPERIMENTS = 200   # keep the log file from growing forever
DEFAULT_COMPARE_RUNS = 3
SEQUENTIAL_MIN_RUNS = 5
SEQUENTIAL_NOISE_PCT = 15.0   # VM-resize comparisons cannot be interleaved, so they jitter more


def result_fingerprint(rows):
    """
    Order-independent fingerprint of a full result set, used to prove that
    two runs returned the same data (a JOIN may return rows in another order).
    """
    total = 0
    for row in rows:
        digest = hashlib.md5(repr(sorted(row.items())).encode("utf-8")).hexdigest()
        total = (total + int(digest[:12], 16)) % (1 << 61)
    return "%d:%x" % (len(rows), total)


class ExperimentManager:
    def __init__(self, storage, log_path):
        self.storage = storage
        self.log_path = log_path
        # optional function returning VM info (see vm/controller.py); set by the console
        self.vm_info_provider = None

        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        if not os.path.exists(log_path):
            with open(log_path, "w") as f:
                json.dump([], f)

        with open(log_path) as f:
            existing_experiments = json.load(f)
        # continue numbering after the highest ID already saved
        highest = 0
        for exp in existing_experiments:
            try:
                highest = max(highest, int(str(exp["experiment_id"]).split("-")[1]) - 1000)
            except (KeyError, IndexError, ValueError):
                pass
        self.next_number = highest + 1

    def make_experiment_id(self):
        experiment_id = "EXP-%d" % (1000 + self.next_number)
        self.next_number += 1
        return experiment_id

    # ------------------------------------------------------------------ running

    def _execute_once(self, sql_text, settings):
        """Runs the query once and returns a full record WITHOUT saving it or giving it an ID."""
        query = sql_parser.parse(sql_text)
        plan = build_plan(query, settings, self.storage)
        # Snapshot before execution so a later VM resize cannot rewrite the
        # resource configuration attributed to this query.
        vm_info = self.vm_info_provider() if self.vm_info_provider else None
        if vm_info is not None:
            vm_info = dict(vm_info)
            vm_info["captured_at"] = datetime.now(timezone.utc).isoformat()

        vmi = get_vmi_adapter()
        vmi.connect()

        gc.collect()          # start every run from a clean heap (less noisy numbers)
        gc.disable()          # ... and keep the collector from firing mid-measurement
        monitor = ResourceMonitor(interval=0.05)
        monitor.start()
        try:
            cpu_start = time.process_time()
            start_time = time.time()
            result = execute(query, self.storage, settings=settings)
            wall_time_ms = (time.time() - start_time) * 1000
            cpu_time_ms = (time.process_time() - cpu_start) * 1000
        finally:
            gc.enable()
            resource_summary = monitor.stop()
        vmi_event_count = vmi.read_event_count()
        vmi.disconnect()

        classification = classify(resource_summary, result.latency_ms, settings.thread_count)
        context = build_context(query, self.storage, settings, result.stats.steps,
                                result.latency_ms, vm=vm_info, sql=sql_text)
        timeline = correlate(None, result.stats, monitor.samples)

        # Keep the resource envelope that was active for THIS query. In dry-run
        # mode these values are explicitly simulated; with Xen/KVM they describe
        # the guest view reported by the running environment.
        try:
            heap_peak_mb = self.measure_heap_peak_mb(sql_text, settings)
        except Exception:
            heap_peak_mb = None
        bottleneck_details = self._bottleneck_details(resource_summary, result.latency_ms,
                                                       cpu_time_ms, heap_peak_mb, vm_info,
                                                       result.stats.steps, settings)

        return {
            "experiment_id": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sql": sql_text,
            "plan": plan,
            "thread_count": settings.thread_count,
            "settings": settings.to_dict(),
            "vm_config": vm_info,
            "resource_envelope": {
                "vcpus": (vm_info or {}).get("vcpus"),
                "memory_mb": (vm_info or {}).get("memory_mb"),
                "vm_name": (vm_info or {}).get("vm_name"),
                "backend": (vm_info or {}).get("backend"),
                "dry_run": (vm_info or {}).get("dry_run", True),
                "status": (vm_info or {}).get("status"),
                "status_error": (vm_info or {}).get("status_error"),
                "host": (vm_info or {}).get("host"),
                "config_path": (vm_info or {}).get("config_path"),
                "captured_at": (vm_info or {}).get("captured_at"),
            },
            "latency_ms": result.latency_ms,
            "wall_latency_ms": round(wall_time_ms, 3),
            "cpu_time_ms": round(cpu_time_ms, 3),
            "heap_peak_mb": heap_peak_mb,
            "row_count": result.row_count,
            "result_fingerprint": result_fingerprint(result.rows),
            "rows_preview": result.rows[:50],
            "operator_stats": result.stats.steps,
            "resource": resource_summary,
            "vmi_events": vmi_event_count,
            "classification": classification,
            "index_used": result.index_used,
            "bottleneck_analysis": bottleneck_details,
            "recommendation": recommend(classification["classification"]),
            "recommendations": recommend_actions(classification["classification"], context),
            "timeline": timeline["timeline"],
        }

    @staticmethod
    def _bottleneck_details(resource, latency_ms, cpu_time_ms, heap_peak_mb, vm_info, plan_steps, settings):
        """Evidence-first diagnostics; never label a bottleneck without showing why."""
        resource = resource or {}
        peak_cpu = float(resource.get("peak_cpu_percent") or 0)
        peak_rss = float(resource.get("peak_memory_mb") or 0)
        details = []
        if peak_cpu >= 70:
            details.append({"type": "CPU pressure", "severity": "high", "evidence": f"Peak sampled system CPU was {peak_cpu:.1f}%.", "action": "Inspect CPU-heavy operators and benchmark a supported predicate/aggregation optimization."})
        elif peak_cpu >= 40:
            details.append({"type": "Moderate CPU activity", "severity": "medium", "evidence": f"Peak sampled system CPU was {peak_cpu:.1f}%.", "action": "Compare repeated runs before increasing thread count."})
        if heap_peak_mb is not None and heap_peak_mb >= 500:
            details.append({"type": "Query heap pressure", "severity": "high", "evidence": f"Separate tracemalloc probe measured {heap_peak_mb:.1f} MB peak Python heap.", "action": "Review streaming aggregation and join build-side recommendations."})
        elif peak_rss >= 500:
            details.append({"type": "Process memory pressure", "severity": "medium", "evidence": f"Peak process RSS during sampling was {peak_rss:.1f} MB.", "action": "Review result size and memory-heavy operators; compare heap measurement as well."})
        if latency_ms >= 500 and peak_cpu < 30:
            details.append({"type": "High latency with low CPU", "severity": "medium", "evidence": f"Latency was {latency_ms:.1f} ms while peak sampled system CPU was {peak_cpu:.1f}%.", "action": "Check data scanning and external/system wait; the current monitor does not directly measure disk wait."})
        # Operator-level evidence is more useful than CPU sampling alone for very
        # short queries, where the 50 ms sampling interval may miss the peak.
        scans = [step for step in (plan_steps or []) if isinstance(step, dict) and str(step.get("op", "")).upper().startswith("SCAN")]
        large_scans = [step for step in scans if int(step.get("rows_in") or 0) >= 10000]
        for scan in large_scans:
            details.append({"type": "Large table scan", "severity": "medium",
                            "evidence": f"{scan.get('op')} read {int(scan.get('rows_in') or 0):,} rows and passed {int(scan.get('rows_out') or 0):,} rows onward.",
                            "action": "Check whether the WHERE filter can be pushed closer to the scan. This Mini DBMS has no real index implementation, so index recommendations are not applied as if they were supported."})
        timed_ops = [step for step in (plan_steps or []) if isinstance(step, dict) and step.get("op") and step.get("ms") is not None]
        if timed_ops:
            total_op_ms = sum(max(0.0, float(step.get("ms") or 0)) for step in timed_ops)
            dominant = max(timed_ops, key=lambda step: float(step.get("ms") or 0))
            dominant_ms = float(dominant.get("ms") or 0)
            if dominant_ms >= 1.0 and total_op_ms > 0 and dominant_ms / total_op_ms >= 0.35:
                details.append({"type": "Dominant query operator", "severity": "medium",
                                "evidence": f"{dominant.get('op')} took {dominant_ms:.3f} ms, about {dominant_ms / total_op_ms * 100:.1f}% of recorded operator time.",
                                "action": "Inspect this operator's input row count and compare a second run after changing one supported setting at a time."})
        if not details:
            details.append({"type": "No strong bottleneck signal", "severity": "info", "evidence": f"Latency {latency_ms:.2f} ms; CPU time {cpu_time_ms:.2f} ms; sampled peak CPU {peak_cpu:.1f}%; process RSS peak {peak_rss:.1f} MB.", "action": "Use the experiment comparison panel and compare the same SQL under different configurations."})
        plan_names = []
        for step in plan_steps or []:
            if isinstance(step, dict):
                name = step.get("op") or step.get("operator") or step.get("name") or step.get("type")
            elif isinstance(step, (list, tuple)) and step:
                name = step[0]
            else:
                name = str(step)
            if name:
                plan_names.append(str(name))
        envelope = vm_info or {}
        return {
            "primary_classification": "; ".join(item["type"] for item in details if item["severity"] != "info") or "No strong bottleneck signal",
            "findings": details,
            "metrics": {"latency_ms": latency_ms, "cpu_time_ms": cpu_time_ms, "heap_peak_mb": heap_peak_mb,
                        "peak_cpu_percent": peak_cpu, "peak_process_rss_mb": peak_rss},
            "query_settings": settings.to_dict(),
            "resource_envelope": {"vm_name": envelope.get("vm_name"), "backend": envelope.get("backend"),
                                  "vcpus": envelope.get("vcpus"), "memory_mb": envelope.get("memory_mb"),
                                  "dry_run": envelope.get("dry_run", True)},
            "plan_operators": plan_names,
            "limitations": ["CPU and RSS samples are host-process/system measurements from psutil.",
                            "Dry-run vCPU/RAM values are simulated and do not constrain the Python process.",
                            "Disk wait and guest-internal counters are not directly measured by the current adapter."],
        }

    def run_query(self, sql_text, thread_count=1, settings=None, save=True):
        if settings is None:
            settings = ExecutionSettings(thread_count=max(1, int(thread_count)))
        else:
            settings = settings.copy()
        record = self._execute_once(sql_text, settings)
        if save:
            self.save_experiment(record)
        return record

    def measure_heap_peak_mb(self, sql_text, settings):
        """
        Peak Python memory the query allocates (MB), measured with tracemalloc.
        Run as a separate, untimed probe because tracemalloc slows execution down.
        """
        query = sql_parser.parse(sql_text)
        gc.collect()
        tracemalloc.start()
        try:
            execute(query, self.storage, settings=settings)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        return round(peak / (1024.0 * 1024.0), 3)

    # ------------------------------------------------------------ compare / apply

    def compare_settings(self, baseline_id, new_settings, runs=DEFAULT_COMPARE_RUNS, applied=None,
                         environment_change=None, simulated_only=False):
        """
        Reruns baseline experiment's SQL with its own settings and with
        `new_settings`, `runs` times each (interleaved so machine noise hits
        both sides equally), plus one memory probe per side. The candidate run
        closest to the median latency is saved as a new experiment.

        `environment_change` (optional) is a function that changes the machine
        itself, e.g. resizes the VM, and returns a short text describing what it
        did. A VM cannot be resized between every pair of runs, so in that case
        the order is: one warm-up run -> all "before" runs -> environment_change()
        -> all "after" runs.

        `simulated_only=True` marks a comparison where nothing real changed (a VM
        dry run), so the report says any difference is measurement noise.

        Returns {"candidate": record, "comparison": comparator.compare(...)}.
        """
        baseline = self.get_experiment(baseline_id)
        if not baseline:
            raise ValueError("Experiment '%s' not found." % baseline_id)

        base_settings = ExecutionSettings.from_dict(
            baseline.get("settings") or {"thread_count": baseline.get("thread_count", 1)})
        new_settings = new_settings.copy()
        sql_text = baseline["sql"]
        runs = max(1, int(runs))
        if environment_change is not None:
            runs = max(runs, SEQUENTIAL_MIN_RUNS)     # sequential runs jitter more, so use more of them

        before_runs, after_runs = [], []
        environment_note = None
        if environment_change is None:
            for _ in range(runs):
                before_runs.append(self._execute_once(sql_text, base_settings))
                after_runs.append(self._execute_once(sql_text, new_settings))
            before_heap = self.measure_heap_peak_mb(sql_text, base_settings)
            after_heap = self.measure_heap_peak_mb(sql_text, new_settings)
        else:
            self._execute_once(sql_text, base_settings)      # warm-up, thrown away
            for _ in range(runs):
                before_runs.append(self._execute_once(sql_text, base_settings))
            before_heap = self.measure_heap_peak_mb(sql_text, base_settings)
            environment_note = environment_change()          # e.g. resize the VM
            for _ in range(runs):
                after_runs.append(self._execute_once(sql_text, new_settings))
            after_heap = self.measure_heap_peak_mb(sql_text, new_settings)

        # the run with the median latency becomes the saved candidate experiment
        ordered = sorted(after_runs, key=lambda r: r["latency_ms"])
        candidate = ordered[len(ordered) // 2]
        candidate["heap_peak_mb"] = after_heap
        candidate["compared_to"] = baseline_id
        candidate["applied_actions"] = list(applied or [])
        candidate["environment_change"] = environment_note

        before_metrics = comparator.summarize_runs(
            before_runs, before_heap, baseline_id, base_settings.to_dict(),
            vm_config=baseline.get("vm_config") or baseline.get("resource_envelope"))
        after_metrics = comparator.summarize_runs(
            after_runs, after_heap, None, new_settings.to_dict(),
            vm_config=candidate.get("vm_config") or candidate.get("resource_envelope"))
        before_metrics["sql"] = sql_text
        after_metrics["sql"] = sql_text
        after_metrics["applied_actions"] = list(applied or [])

        # a pure-timing comparison across repeats; saved record carries the comparison too
        # before/after runs that cannot be interleaved (VM resize) drift more, so use a wider 'same' band
        noise_pct = 5.0 if environment_change is None else SEQUENTIAL_NOISE_PCT
        comparison = comparator.compare(before_metrics, after_metrics, noise_pct=noise_pct)

        comparison["environment_note"] = environment_note
        comparison["simulated_only"] = bool(simulated_only)
        self.save_experiment(candidate)
        comparison["after_id"] = candidate["experiment_id"]
        candidate["comparison"] = comparison
        self._update_saved(candidate)
        return {"candidate": candidate, "comparison": comparison}

    def compare_modified_sql(self, baseline_id, modified_sql, applied=None, runs=DEFAULT_COMPARE_RUNS):
        """
        Runs the baseline query vs the user-modified SQL query with the same settings,
        measures performance before and after, checks result equivalence,
        and records the optimization experiment.
        """
        baseline = self.get_experiment(baseline_id)
        if not baseline:
            raise ValueError("Experiment '%s' not found." % baseline_id)

        base_settings = ExecutionSettings.from_dict(
            baseline.get("settings") or {"thread_count": baseline.get("thread_count", 1)})
        orig_sql = baseline["sql"]
        modified_sql = str(modified_sql).strip().rstrip(";").strip()
        runs = max(1, int(runs))

        before_runs, after_runs = [], []
        for _ in range(runs):
            before_runs.append(self._execute_once(orig_sql, base_settings))
            after_runs.append(self._execute_once(modified_sql, base_settings))

        before_heap = self.measure_heap_peak_mb(orig_sql, base_settings)
        after_heap = self.measure_heap_peak_mb(modified_sql, base_settings)

        ordered = sorted(after_runs, key=lambda r: r["latency_ms"])
        candidate = ordered[len(ordered) // 2]
        candidate["heap_peak_mb"] = after_heap
        candidate["compared_to"] = baseline_id
        candidate["original_sql"] = orig_sql
        candidate["applied_actions"] = list(applied or ["Edit SQL (Selective WHERE Filter)"])

        before_metrics = comparator.summarize_runs(
            before_runs, before_heap, baseline_id, base_settings.to_dict(),
            vm_config=baseline.get("vm_config") or baseline.get("resource_envelope"))
        after_metrics = comparator.summarize_runs(
            after_runs, after_heap, None, base_settings.to_dict(),
            vm_config=candidate.get("vm_config") or candidate.get("resource_envelope"))
        before_metrics["sql"] = orig_sql
        after_metrics["sql"] = modified_sql
        after_metrics["applied_actions"] = candidate["applied_actions"]

        comparison = comparator.compare(before_metrics, after_metrics, noise_pct=5.0)
        comparison["same_query"] = False
        comparison["applied_actions"] = candidate["applied_actions"]

        self.save_experiment(candidate)
        comparison["after_id"] = candidate["experiment_id"]
        candidate["comparison"] = comparison
        self._update_saved(candidate)
        return {"candidate": candidate, "comparison": comparison}

    def compare_saved(self, id_before, id_after):
        """Compares two already-saved experiments using the numbers stored in the log."""
        before, after = self.get_experiment(id_before), self.get_experiment(id_after)
        for exp_id, exp in ((id_before, before), (id_after, after)):
            if not exp:
                raise ValueError("Experiment '%s' not found." % exp_id)
        return comparator.compare(comparator.metrics_from_record(before),
                                  comparator.metrics_from_record(after))

    # ------------------------------------------------------------------ storage

    def save_experiment(self, record):
        if not record.get("experiment_id"):
            record["experiment_id"] = self.make_experiment_id()
        with open(self.log_path) as f:
            all_experiments = json.load(f)
        all_experiments.append(record)
        all_experiments = all_experiments[-MAX_SAVED_EXPERIMENTS:]
        with open(self.log_path, "w") as f:
            json.dump(all_experiments, f, indent=2, default=str)

    def _update_saved(self, record):
        """Overwrites the saved copy of `record` (matched by experiment_id)."""
        with open(self.log_path) as f:
            all_experiments = json.load(f)
        for i, exp in enumerate(all_experiments):
            if isinstance(exp, dict) and exp.get("experiment_id") == record["experiment_id"]:
                all_experiments[i] = record
        with open(self.log_path, "w") as f:
            json.dump(all_experiments, f, indent=2, default=str)

    def list_experiments(self):
        with open(self.log_path) as f:
            all_experiments = json.load(f)
        all_experiments.reverse()   # newest experiment first
        return all_experiments

    def get_experiment(self, experiment_id):
        for exp in self.list_experiments():
            if isinstance(exp, dict) and str(exp.get("experiment_id", "")).strip() == str(experiment_id).strip():
                return exp
        return None

    def rerun(self, experiment_id, thread_count, settings=None):
        """Reruns an experiment's SQL. Keeps its original settings, changing only the thread count."""
        original = self.get_experiment(experiment_id)
        if not original:
            raise ValueError("Experiment '%s' not found." % experiment_id)
        if settings is None:
            settings = ExecutionSettings.from_dict(
                original.get("settings") or {"thread_count": original.get("thread_count", 1)})
        settings = settings.with_changes({"thread_count": thread_count})
        return self.run_query(original["sql"], settings=settings)

    def run_workload(self, queries=None, iterations=1):
        """
        Runs a test workload of diverse queries and records measurements.
        """
        if not queries:
            queries = [
                "SELECT department, AVG(salary) FROM sales WHERE salary > 50000 GROUP BY department",
                "SELECT region, COUNT(*) FROM sales GROUP BY region",
                "SELECT department, SUM(salary) FROM sales WHERE region = 'North' GROUP BY department",
                "SELECT sales.department, orders.amount FROM sales JOIN orders ON sales.employee_id = orders.employee_id WHERE orders.amount > 4000",
                "SELECT * FROM sales WHERE salary > 80000",
            ]

        results = []
        total_time_ms = 0.0
        start_wall = time.time()
        for _ in range(max(1, int(iterations))):
            for sql in queries:
                try:
                    rec = self.run_query(sql, save=True)
                    results.append({
                        "sql": sql,
                        "experiment_id": rec.get("experiment_id"),
                        "latency_ms": rec.get("latency_ms", 0),
                        "row_count": rec.get("row_count", 0),
                        "classification": (rec.get("classification") or {}).get("classification", "Unknown"),
                    })
                    total_time_ms += rec.get("latency_ms", 0)
                except Exception as exc:
                    results.append({"sql": sql, "error": str(exc)})

        return {
            "queries_executed": len(results),
            "total_latency_ms": round(total_time_ms, 2),
            "wall_time_s": round(time.time() - start_wall, 2),
            "average_latency_ms": round(total_time_ms / len(results), 2) if results else 0,
            "results": results,
        }

    def compare_index(self, sql_text, table_name, column, index_type="HASH", runs=3):
        """
        Executes query without index vs with index, measures latency and verifies result equivalence.
        """
        existing_idx = None
        if hasattr(self.storage, "index_manager"):
            existing_idx = self.storage.index_manager.get_index_for_column(table_name, column)
            if existing_idx:
                self.storage.index_manager.drop_index(existing_idx.name, table_name)

        before_runs = []
        for _ in range(runs):
            before_runs.append(self._execute_once(sql_text, ExecutionSettings()))

        idx_name = f"idx_{table_name}_{column}_{int(time.time())}"
        table = self.storage.get_table(table_name)
        new_idx = self.storage.index_manager.create_index(idx_name, table_name, column, index_type, table.rows)

        after_runs = []
        for _ in range(runs):
            after_runs.append(self._execute_once(sql_text, ExecutionSettings()))

        before_med = sorted(before_runs, key=lambda r: r["latency_ms"])[len(before_runs) // 2]
        after_med = sorted(after_runs, key=lambda r: r["latency_ms"])[len(after_runs) // 2]

        results_match = (before_med.get("result_fingerprint") == after_med.get("result_fingerprint"))
        before_lat = before_med.get("latency_ms", 0.0)
        after_lat = after_med.get("latency_ms", 0.0)
        diff_pct = round(((after_lat - before_lat) / before_lat) * 100, 2) if before_lat > 0 else 0.0

        return {
            "sql": sql_text,
            "table_name": table_name,
            "column": column,
            "index_type": index_type,
            "index_name": idx_name,
            "results_match": results_match,
            "before_latency_ms": before_lat,
            "after_latency_ms": after_lat,
            "diff_pct": diff_pct,
            "speedup_factor": round(before_lat / after_lat, 2) if after_lat > 0 else 1.0,
            "index_used": after_med.get("index_used") == idx_name,
            "conclusion": f"Index scan improved execution by {-diff_pct:.1f}%" if diff_pct < 0 else "Index scan had neutral or overhead effect on small data."
        }

    def export_csv(self):
        """Returns CSV formatted string of saved experiment history."""
        records = self.list_experiments()
        fields = [
            "experiment_id", "timestamp", "sql", "latency_ms", "cpu_time_ms",
            "heap_peak_mb", "row_count", "classification",
            "thread_count", "pushdown_filter", "build_smaller_side",
            "streaming_aggregation", "compiled_predicates", "index_used"
        ]
        import io
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for r in records:
            settings = r.get("settings", {}) or {}
            cls = r.get("classification", {}) or {}
            writer.writerow({
                "experiment_id": r.get("experiment_id", ""),
                "timestamp": r.get("timestamp", ""),
                "sql": r.get("sql", "").replace("\n", " "),
                "latency_ms": r.get("latency_ms", 0),
                "cpu_time_ms": r.get("cpu_time_ms", 0),
                "heap_peak_mb": r.get("heap_peak_mb", ""),
                "row_count": r.get("row_count", 0),
                "classification": cls.get("classification", ""),
                "thread_count": settings.get("thread_count", 1),
                "pushdown_filter": settings.get("pushdown_filter", False),
                "build_smaller_side": settings.get("build_smaller_side", False),
                "streaming_aggregation": settings.get("streaming_aggregation", False),
                "compiled_predicates": settings.get("compiled_predicates", False),
                "index_used": r.get("index_used", ""),
            })
        return output.getvalue()
