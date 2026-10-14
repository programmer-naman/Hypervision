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
        vm_info = self.vm_info_provider() if self.vm_info_provider else None
        context = build_context(query, self.storage, settings, result.stats.steps,
                                result.latency_ms, vm=vm_info)
        timeline = correlate(None, result.stats, monitor.samples)

        return {
            "experiment_id": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sql": sql_text,
            "plan": plan,
            "thread_count": settings.thread_count,
            "settings": settings.to_dict(),
            "latency_ms": result.latency_ms,
            "wall_latency_ms": round(wall_time_ms, 3),
            "cpu_time_ms": round(cpu_time_ms, 3),
            "heap_peak_mb": None,
            "row_count": result.row_count,
            "result_fingerprint": result_fingerprint(result.rows),
            "rows_preview": result.rows[:50],
            "operator_stats": result.stats.steps,
            "resource": resource_summary,
            "vmi_events": vmi_event_count,
            "classification": classification,
            "recommendation": recommend(classification["classification"]),
            "recommendations": recommend_actions(classification["classification"], context),
            "timeline": timeline["timeline"],
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
            before_runs, before_heap, baseline_id, base_settings.to_dict())
        after_metrics = comparator.summarize_runs(
            after_runs, after_heap, None, new_settings.to_dict())
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
            if exp["experiment_id"] == record["experiment_id"]:
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
            if exp["experiment_id"] == experiment_id:
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
