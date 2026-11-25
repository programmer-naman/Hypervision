"""
web_app.py
----------
Flask web and REST API layer for HyperVision:
Integrated Mini DBMS, Host Monitoring, VM/VMI Controller,
Bottleneck Classifier, and Performance Experiment Engine.
"""

from __future__ import annotations
import csv
import io
import json
import os
import re
import sys
import threading
import time
from collections import deque
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, Response
import psutil

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
SAMPLE_DATA = ROOT / "sample_data"
LOG_DIR = ROOT / "logs"
LOG_PATH = LOG_DIR / "experiments.json"

sys.path.insert(0, str(BACKEND))

from mini_dbms.storage import Storage  # noqa: E402
from mini_dbms.parser import parse, ParseError, ParsedQuery  # noqa: E402
from mini_dbms.executor import execute_statement  # noqa: E402
from mini_dbms.settings import ExecutionSettings  # noqa: E402
from experiments.experiment_manager import ExperimentManager  # noqa: E402
from vm.controller import VMController, VMError  # noqa: E402

app = Flask(__name__, static_folder=None)
app.config["JSON_SORT_KEYS"] = False


def ensure_sample_data():
    SAMPLE_DATA.mkdir(parents=True, exist_ok=True)
    if not (SAMPLE_DATA / "sales.csv").exists() or not (SAMPLE_DATA / "orders.csv").exists():
        from data.sample_data_generator import generate_sales_csv, generate_orders_csv
        generate_sales_csv(str(SAMPLE_DATA / "sales.csv"), 100000)
        generate_orders_csv(str(SAMPLE_DATA / "orders.csv"), 10000, 100000)


ensure_sample_data()
storage = Storage(str(SAMPLE_DATA))
LOG_DIR.mkdir(parents=True, exist_ok=True)
manager = ExperimentManager(storage, str(LOG_PATH))
vm_controller = VMController(backend="dryrun")
active_settings = ExecutionSettings()
manager.vm_info_provider = vm_controller.snapshot
query_lock = threading.Lock()
metric_samples = deque(maxlen=60)
process = psutil.Process(os.getpid())


def current_metrics():
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(str(ROOT))
    try:
        proc_mem = process.memory_info().rss / (1024 * 1024)
        proc_cpu = process.cpu_percent(interval=0.05)
    except Exception:
        proc_mem, proc_cpu = 0, 0
    item = {
        "timestamp": time.time(),
        "cpu_percent": psutil.cpu_percent(interval=0.05),
        "memory_percent": vm.percent,
        "memory_used_mb": round(vm.used / (1024 * 1024), 2),
        "memory_total_mb": round(vm.total / (1024 * 1024), 2),
        "process_memory_mb": round(proc_mem, 2),
        "process_cpu_percent": round(proc_cpu, 2),
        "process_threads": process.num_threads(),
        "process_count": len(psutil.pids()),
        "disk_percent": disk.percent,
        "source": "real host metrics via psutil",
    }
    metric_samples.append(item)
    return item


def read_history():
    try:
        with open(LOG_PATH, encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


# -----------------------------------------------------------------------------
# Static and Health Routes
# -----------------------------------------------------------------------------

@app.errorhandler(404)
def _not_found(_exc):
    return jsonify({"ok": False, "error": "Not found"}), 404


@app.errorhandler(500)
def _server_error(exc):
    return jsonify({"ok": False, "error": f"Internal server error: {exc}"}), 500


@app.after_request
def _no_cache_static(resp):
    # Prevents the browser from running a stale app.js after the fix is applied.
    if resp.mimetype in ("application/javascript", "text/javascript", "text/css", "text/html"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


@app.get("/")
def index():
    return send_from_directory(str(FRONTEND), "index.html")


@app.get("/<path:asset_path>")
def frontend_assets(asset_path):
    candidate = (FRONTEND / asset_path).resolve()
    if FRONTEND.resolve() not in candidate.parents and candidate != FRONTEND.resolve():
        return jsonify(error="Not found"), 404
    if candidate.is_file():
        return send_from_directory(str(FRONTEND), asset_path)
    return jsonify(error="Not found"), 404


@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "service": "HyperVision Platform API",
        "mode": "connected",
        "sql_support": "Full Mini DBMS (SELECT, INSERT, UPDATE, DELETE, INDEX)",
        "tables": sorted(storage.tables.keys()),
    })


@app.get("/api/overview")
def overview_stats():
    """Aggregated stats for the Overview Dashboard."""
    records = read_history()
    total_queries = len(records)
    avg_latency = 0.0
    slowest_queries = []

    if records:
        latencies = [float(r.get("latency_ms", 0)) for r in records]
        avg_latency = round(sum(latencies) / len(latencies), 2)
        sorted_by_lat = sorted(records, key=lambda r: float(r.get("latency_ms", 0)), reverse=True)
        slowest_queries = [
            {
                "id": r.get("experiment_id"),
                "sql": r.get("sql"),
                "latency_ms": r.get("latency_ms"),
                "classification": (r.get("classification") or {}).get("classification", "Unknown"),
            }
            for r in sorted_by_lat[:5]
        ]

    metrics = current_metrics()
    vm_snap = vm_controller.snapshot()

    latest_rec = records[-1] if records else {}
    detected_bottlenecks = []
    if latest_rec:
        cls_info = latest_rec.get("classification") or {}
        detected_bottlenecks.append({
            "source": "Rule-based Diagnostic",
            "category": cls_info.get("classification", "Unknown"),
            "confidence": cls_info.get("confidence", 0.5),
        })

    latest_class = "Optimal / Balanced"
    if latest_rec:
        cls_info = latest_rec.get("classification") or {}
        latest_class = cls_info.get("classification") or "Optimal / Balanced"

    return jsonify({
        "ok": True,
        "total_queries": total_queries,
        "successful_queries": len([r for r in records if not r.get("error")]),
        "failed_queries": len([r for r in records if r.get("error")]),
        "avg_latency_ms": avg_latency,
        "latest_latency_ms": round(float(latest_rec.get("latency_ms", 0)), 2) if latest_rec else 0.0,
        "latest_experiment_id": latest_rec.get("experiment_id"),
        "total_experiments": total_queries,
        "latest_bottleneck": latest_class,
        "latest_optimization_result": latest_rec.get("comparison"),
        "slowest_queries": slowest_queries,
        "metrics": metrics,
        "vm_status": vm_snap,
        "vm_configuration": {
            "vm_name": vm_snap.get("vm_name"),
            "vcpus": vm_snap.get("vcpus"),
            "memory_mb": vm_snap.get("memory_mb"),
            "status": vm_snap.get("status"),
            "backend": vm_snap.get("backend"),
            "mode": "Simulation (dry-run)" if vm_snap.get("dry_run") else f"Real {vm_snap.get('backend', '').upper()}",
            "dry_run": vm_snap.get("dry_run", True),
        },
        "detected_bottlenecks": detected_bottlenecks,
        "latest_recommendations": latest_rec.get("recommendations", []),
        "recent_history": list(reversed(records[-10:])),
    })


@app.get("/api/settings")
def settings_status():
    return jsonify({
        "ok": True,
        "settings": active_settings.to_dict(),
        "description": active_settings.describe(),
    })


@app.post("/api/settings/update")
def settings_update():
    body = request.get_json(silent=True) or {}
    global active_settings
    try:
        active_settings = active_settings.with_changes(body)
        return jsonify({
            "ok": True,
            "message": "Optimization settings updated.",
            "settings": active_settings.to_dict(),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/metrics")
def metrics():
    latest = current_metrics()
    return jsonify({
        "ok": True,
        "metrics": latest,
        "history": list(metric_samples),
        "source": "real host metrics via psutil",
    })


@app.get("/api/processes")
def processes():
    rows = []
    for proc in psutil.process_iter(attrs=["pid", "name", "cpu_percent", "memory_info"]):
        try:
            info = proc.info
            memory = info.get("memory_info")
            rows.append({
                "pid": info.get("pid"),
                "name": info.get("name") or "unknown",
                "cpu_percent": round(float(info.get("cpu_percent") or 0), 2),
                "memory_mb": round(memory.rss / (1024 * 1024), 2) if memory else 0,
                "source": "real host process metrics via psutil",
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    rows.sort(key=lambda row: (row["cpu_percent"], row["memory_mb"]), reverse=True)
    return jsonify({"ok": True, "processes": rows[:20], "source": "real host process metrics via psutil"})


# -----------------------------------------------------------------------------
# SQL Query Execution
# -----------------------------------------------------------------------------

@app.post("/api/query")
def run_query():
    body = request.get_json(silent=True) or {}
    original_sql = str(body.get("sql", "")).strip().rstrip(";").strip()

    if not original_sql:
        return jsonify({"ok": False, "error": "Enter a SQL query first."}), 400

    try:
        stmt = parse(original_sql)
        with query_lock:
            settings = active_settings.copy()
            if "thread_count" in body:
                thread_count = max(1, min(16, int(body.get("thread_count", settings.thread_count))))
                settings = settings.with_changes({"thread_count": thread_count})

            # Check statement type
            if isinstance(stmt, ParsedQuery):
                # SELECT statement: runs through full experiment pipeline
                record = manager.run_query(original_sql, settings=settings)
                rows = record.get("rows_preview") or []
                columns = list(rows[0].keys()) if rows else [c.get("alias") or c.get("col") for c in stmt.columns]
                output_rows = [[row.get(col) for col in columns] for row in rows]

                limit_arg = request.args.get("limit")
                if limit_arg:
                    try:
                        lim = int(limit_arg)
                        output_rows = output_rows[:lim]
                    except ValueError:
                        pass

                return jsonify({
                    "ok": True,
                    "statement_type": "SELECT",
                    "sql": original_sql,
                    "columns": columns,
                    "rows": output_rows,
                    "row_count": record.get("row_count", len(output_rows)),
                    "displayed_rows": len(output_rows),
                    "latency_ms": record.get("latency_ms", 0),
                    "plan": record.get("plan", []),
                    "operator_stats": record.get("operator_stats", []),
                    "resource": record.get("resource", {}),
                    "classification": record.get("classification", {}),
                    "index_used": record.get("index_used"),
                    "recommendation": record.get("recommendation", ""),
                    "recommendations": record.get("recommendations", []),
                    "experiment_id": record.get("experiment_id"),
                    "settings": record.get("settings", {}),
                    "resource_envelope": record.get("resource_envelope", {}),
                    "cpu_time_ms": record.get("cpu_time_ms"),
                    "heap_peak_mb": record.get("heap_peak_mb"),
                    "bottleneck_analysis": record.get("bottleneck_analysis", {}),
                    "source": "actual Mini DBMS execution; host resource measurements via psutil",
                })
            else:
                # DML or DDL statement
                res = execute_statement(stmt, storage, settings=settings)
                return jsonify({
                    "ok": True,
                    "statement_type": res.statement_type,
                    "sql": original_sql,
                    "message": res.message,
                    "rows_affected": res.rows_affected,
                    "latency_ms": res.latency_ms,
                    "columns": res.columns,
                    "rows": res.rows,
                    "row_count": res.row_count,
                    "source": "Mini DBMS executor",
                })

    except ParseError as exc:
        return jsonify({"ok": False, "error": f"SQL Syntax Error: {exc}"}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


# -----------------------------------------------------------------------------
# Database Explorer Routes: Tables, Schema, Indexes
# -----------------------------------------------------------------------------

@app.get("/api/tables")
def tables():
    result = []
    for name, table in sorted(storage.tables.items()):
        idxs = storage.index_manager.get_table_indexes(name)
        result.append({
            "name": name,
            "columns": table.columns,
            "column_types": table.get_column_types(),
            "row_count": len(table.rows),
            "sample_rows": table.rows[:10],
            "indexes": [idx.to_dict() for idx in idxs.values()],
        })
    return jsonify({
        "ok": True,
        "tables": result,
        "total_tables": len(result),
        "source": "in-memory tables loaded by Mini DBMS",
    })


@app.get("/api/tables/<table_name>")
def table_detail(table_name):
    try:
        table = storage.get_table(table_name)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404

    try:
        limit = max(1, min(500, int(request.args.get("limit", "50"))))
    except ValueError:
        limit = 50

    idxs = storage.index_manager.get_table_indexes(table_name)

    return jsonify({
        "ok": True,
        "name": table.name,
        "columns": table.columns,
        "column_types": table.get_column_types(),
        "row_count": len(table.rows),
        "rows": table.rows[:limit],
        "indexes": [idx.to_dict() for idx in idxs.values()],
        "source": "project table data",
    })


@app.get("/api/indexes")
def list_indexes():
    all_idxs = storage.index_manager.list_all_indexes()
    return jsonify({"ok": True, "indexes": all_idxs, "count": len(all_idxs)})


@app.post("/api/indexes")
def create_index():
    body = request.get_json(silent=True) or {}
    try:
        table_name = str(body.get("table", "")).strip()
        column = str(body.get("column", "")).strip()
        index_type = str(body.get("index_type", "HASH")).strip().upper()
        index_name = str(body.get("name", "")).strip() or f"idx_{table_name}_{column}"

        if not table_name or not column:
            raise ValueError("Table and column are required.")

        table = storage.get_table(table_name)
        if column not in table.columns:
            raise ValueError(f"Column '{column}' not found in table '{table_name}'.")

        idx = storage.index_manager.create_index(index_name, table_name, column, index_type, table.rows)
        return jsonify({
            "ok": True,
            "message": f"Index '{index_name}' ({index_type}) created on {table_name}({column}).",
            "index": idx.to_dict(),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/indexes/drop")
def drop_index():
    body = request.get_json(silent=True) or {}
    try:
        index_name = str(body.get("name", "")).strip()
        table_name = body.get("table")
        if not index_name:
            raise ValueError("Index name is required.")
        storage.index_manager.drop_index(index_name, table_name)
        return jsonify({"ok": True, "message": f"Index '{index_name}' dropped successfully."})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


# -----------------------------------------------------------------------------
# Bottleneck Analysis
# -----------------------------------------------------------------------------

@app.get("/api/analysis")
def analysis():
    records = read_history()
    if not records:
        return jsonify({
            "ok": True,
            "findings": [],
            "message": "Run a supported SQL query first to generate performance analysis.",
            "source": "no experiment data yet"
        })

    latest = records[-1]
    cls = latest.get("classification") or {}

    findings = [{
        "id": latest.get("experiment_id", "LATEST"),
        "priority": "medium" if cls.get("classification") not in ("Optimal / Balanced", "Mixed") else "info",
        "title": "Rule-based Diagnostic: " + str(cls.get("classification", "Unknown")),
        "description": latest.get("recommendation", "Review query plan and measurements."),
        "evidence": "Measured latency: %.3f ms; rows returned: %s" % (float(latest.get("latency_ms", 0)), latest.get("row_count", 0)),
        "recommendation": latest.get("recommendation", "Review the execution plan and rerun a baseline."),
        "actions": latest.get("recommendations", []),
        "bottleneck_analysis": latest.get("bottleneck_analysis", {}),
        "resource": latest.get("resource", {}),
        "settings": latest.get("settings", {}),
    }]

    return jsonify({
        "ok": True,
        "findings": findings,
        "classification": cls,
        "experiment_id": latest.get("experiment_id"),
        "recommendations": latest.get("recommendations", []),
        "bottleneck_analysis": latest.get("bottleneck_analysis", {}),
        "source": "Rule-based bottleneck diagnostics",
    })


# -----------------------------------------------------------------------------
# Benchmarking & Performance Experiments
# -----------------------------------------------------------------------------

@app.post("/api/experiments/workload")
def run_workload():
    body = request.get_json(silent=True) or {}
    queries = body.get("queries")
    iterations = int(body.get("iterations", 1))
    try:
        with query_lock:
            result = manager.run_workload(queries=queries, iterations=iterations)
        return jsonify({"ok": True, "workload": result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/experiments/compare_index")
def compare_index_endpoint():
    body = request.get_json(silent=True) or {}
    sql = str(body.get("sql", "")).strip()
    table = str(body.get("table", "")).strip()
    column = str(body.get("column", "")).strip()
    index_type = str(body.get("index_type", "HASH")).strip().upper()

    if not sql or not table or not column:
        return jsonify({"ok": False, "error": "sql, table, and column are required."}), 400

    try:
        with query_lock:
            result = manager.compare_index(sql, table, column, index_type=index_type, runs=3)
        return jsonify({"ok": True, "comparison": result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/recommendations/apply")
def apply_recommendation():
    body = request.get_json(silent=True) or {}
    experiment_id = str(body.get("experiment_id", "")).strip()
    action_id = str(body.get("action_id", "")).strip()
    action_ids = body.get("action_ids") or []
    apply_all = bool(body.get("apply_all") or action_id.lower() == "all")

    try:
        if not experiment_id:
            raise ValueError("Select a recommendation from a saved experiment first.")
        baseline = manager.get_experiment(experiment_id)
        if not baseline:
            raise ValueError("Experiment not found. Run a query and refresh analysis.")

        recs = baseline.get("recommendations") or []
        base_settings = ExecutionSettings.from_dict(
            baseline.get("settings") or {"thread_count": baseline.get("thread_count", 1)})
        global active_settings

        # Case 1: Apply All or Multiple compatible recommendations
        if apply_all or (action_ids and len(action_ids) > 1):
            target_ids = set(action_ids) if action_ids else {r["id"] for r in recs if r.get("kind") == "setting"}
            compatible_actions = []
            skipped_actions = []
            has_thread_action = False

            for r in recs:
                rid = r.get("id")
                if rid in target_ids:
                    if r.get("kind") == "setting":
                        if rid in ("reduce_threads", "try_threads"):
                            if has_thread_action:
                                skipped_actions.append({"id": rid, "title": r.get("title"), "reason": "Thread configuration conflict; skipped."})
                                continue
                            has_thread_action = True
                        compatible_actions.append(r)
                    else:
                        skipped_actions.append({"id": rid, "title": r.get("title"), "reason": "Manual SQL edits and VM adjustments require separate explicit confirmation."})

            if not compatible_actions:
                raise ValueError("No automated engine recommendations available to apply.")

            combined_changes = {}
            for act in compatible_actions:
                combined_changes.update(act.get("changes") or {})

            applied_ids = [act["id"] for act in compatible_actions]
            applied_titles = [act["title"] for act in compatible_actions]
            new_settings = base_settings.with_changes(combined_changes)

            with query_lock:
                result = manager.compare_settings(experiment_id, new_settings, runs=3, applied=applied_ids)
                active_settings = active_settings.with_changes(combined_changes)

            return jsonify({
                "ok": True,
                "message": f"Applied {len(applied_ids)} compatible optimization(s): {', '.join(applied_titles)}",
                "applied_actions": applied_titles,
                "applied_ids": applied_ids,
                "skipped_actions": skipped_actions,
                "result": result,
                "comparison": result.get("comparison"),
                "active_settings": active_settings.to_dict(),
            })

        # Case 2: Apply single recommendation
        target_action_id = action_ids[0] if action_ids else action_id
        if not target_action_id:
            raise ValueError("Select a recommendation to apply.")

        action = next((item for item in recs if item.get("id") == target_action_id), None)
        if not action:
            # Fallback if an engine setting or action ID was directly requested
            action = {"id": target_action_id, "kind": "setting", "changes": {target_action_id: True}}

        kind = action.get("kind", "setting")
        if kind == "manual":
            proposed = action.get("proposed_sql") or baseline.get("sql")
            if body.get("confirmed") or body.get("force_apply"):
                with query_lock:
                    result = manager.compare_modified_sql(experiment_id, proposed, applied=[target_action_id], runs=3)
                return jsonify({
                    "ok": True,
                    "message": f"Applied SQL optimization '{action.get('title', target_action_id)}'.",
                    "applied_actions": [action.get("title", target_action_id)],
                    "result": result,
                    "comparison": result.get("comparison"),
                })
            return jsonify({
                "ok": False,
                "action_type": "edit_sql",
                "proposed_sql": proposed,
                "error": "This recommendation modifies the SQL query. Please review and confirm the edited SQL in the SQL review editor before executing.",
            }), 400

        changes = action.get("changes") or {}

        if kind == "setting":
            new_settings = base_settings.with_changes(changes)
            with query_lock:
                result = manager.compare_settings(experiment_id, new_settings, runs=3, applied=[target_action_id])
                active_settings = active_settings.with_changes(changes)
            return jsonify({
                "ok": True,
                "message": f"Optimization '{action.get('title', target_action_id)}' applied and benchmarked.",
                "applied_actions": [action.get("title", target_action_id)],
                "result": result,
                "comparison": result.get("comparison"),
                "active_settings": active_settings.to_dict(),
            })

        if kind == "vm":
            target_vcpus = changes.get("vm_vcpus")
            target_memory = changes.get("vm_memory_mb")
            if vm_controller.dry_run:
                change_fn = (lambda: vm_controller.set_vcpus(int(target_vcpus))) if target_vcpus else (lambda: vm_controller.set_memory_mb(int(target_memory)))
                with query_lock:
                    result = manager.compare_settings(experiment_id, base_settings, runs=3, applied=[target_action_id],
                                                      environment_change=change_fn, simulated_only=True)
                return jsonify({
                    "ok": True,
                    "message": f"Simulated VM recommendation: {action.get('title', target_action_id)}",
                    "applied_actions": [action.get("title", target_action_id)],
                    "result": result,
                    "comparison": result.get("comparison"),
                })

            if not body.get("confirmed"):
                raise ValueError("Confirm before applying a real hypervisor change.")
            change_fn = (lambda: vm_controller.set_vcpus(int(target_vcpus))) if target_vcpus else (lambda: vm_controller.set_memory_mb(int(target_memory)))
            with query_lock:
                result = manager.compare_settings(experiment_id, base_settings, runs=5, applied=[target_action_id],
                                                  environment_change=change_fn, simulated_only=False)
            return jsonify({
                "ok": True,
                "message": f"VM recommendation applied: {action.get('title', target_action_id)}",
                "applied_actions": [action.get("title", target_action_id)],
                "result": result,
                "comparison": result.get("comparison"),
            })

        raise ValueError("Unknown recommendation type.")
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/recommendations/apply_sql")
def apply_sql_recommendation():
    body = request.get_json(silent=True) or {}
    experiment_id = str(body.get("experiment_id") or body.get("baseline_id") or "").strip()
    modified_sql = str(body.get("modified_sql", "")).strip().rstrip(";").strip()
    action_id = str(body.get("action_id", "narrow_where")).strip()

    try:
        if not experiment_id or not modified_sql:
            raise ValueError("Baseline experiment ID and modified SQL query are required.")

        parse(modified_sql)

        with query_lock:
            result = manager.compare_modified_sql(experiment_id, modified_sql, applied=[action_id], runs=3)

        return jsonify({
            "ok": True,
            "message": "User-reviewed SQL optimization executed and benchmarked against baseline.",
            "result": result,
            "comparison": result.get("comparison"),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/history")
def history():
    records = read_history()
    items = []
    for rec in reversed(records[-100:]):
        items.append({
            "id": rec.get("experiment_id", "—"),
            "sql": rec.get("sql", ""),
            "latency_ms": rec.get("latency_ms", 0),
            "row_count": rec.get("row_count", 0),
            "timestamp": rec.get("timestamp", ""),
            "classification": (rec.get("classification") or {}).get("classification", "Unknown"),
            "index_used": rec.get("index_used"),
            "recommendation": rec.get("recommendation", ""),
            "plan": rec.get("plan", []),
            "settings": rec.get("settings", {}),
            "resource": rec.get("resource", {}),
            "cpu_time_ms": rec.get("cpu_time_ms"),
            "heap_peak_mb": rec.get("heap_peak_mb"),
            "result_fingerprint": rec.get("result_fingerprint"),
        })
    return jsonify({"ok": True, "history": items, "total": len(records), "source": "persistent experiments.json"})


@app.get("/api/history/export")
def export_history_csv():
    csv_text = manager.export_csv()
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=hypervision_experiments.csv"}
    )


@app.get("/api/experiments/<experiment_id>")
def experiment_detail(experiment_id):
    record = manager.get_experiment(experiment_id)
    if not record:
        return jsonify({"ok": False, "error": "Experiment not found."}), 404
    return jsonify({"ok": True, "experiment": record})


def _scrub(value):
    """Recursively replaces NaN/Infinity with None so responses are valid JSON."""
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value



@app.post("/api/experiments/compare")
def compare_experiments():
    body = request.get_json(silent=True) or {}
    before_id = str(body.get("before_id", "")).strip()
    after_id = str(body.get("after_id", "")).strip()
    try:
        if not before_id or not after_id:
            raise ValueError("Choose two saved experiments to compare.")
        before = manager.get_experiment(before_id)
        after = manager.get_experiment(after_id)
        if not before or not after:
            raise ValueError("One or both experiment IDs were not found in saved history.")
        comparison = manager.compare_saved(before_id, after_id)
        norm = lambda q: re.sub(r"\s+", " ", str(q or "").strip().rstrip(";")).lower()
        comparison["same_query"] = norm(before.get("sql")) == norm(after.get("sql"))
        # json round-trip with default=str guarantees the response is serialisable
        # and replaces NaN/Infinity (invalid JSON that breaks the browser's parser).
        payload = {"ok": True, "comparison": comparison, "before": before, "after": after}
        safe = _scrub(json.loads(json.dumps(payload, default=str)))
        return jsonify(safe)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        app.logger.exception("Experiment comparison failed")
        return jsonify({"ok": False, "error": f"Comparison failed: {exc}"}), 500


# -----------------------------------------------------------------------------
# VM / Hypervisor Control Routes
# -----------------------------------------------------------------------------

@app.get("/api/vm")
def vm_status():
    vm = vm_controller.snapshot()
    source = "simulated VM state (dry-run)" if vm_controller.dry_run else f"{vm_controller.backend.upper()} hypervisor"
    return jsonify({"ok": True, "vm": vm, "source": source, "available_backends": ["dryrun", "xen", "kvm"]})


@app.post("/api/vm/configure")
def vm_configure():
    body = request.get_json(silent=True) or {}
    try:
        backend = str(body.get("backend", "dryrun")).lower()
        vm_name = str(body.get("vm_name", "demo-vm")).strip() or "demo-vm"
        host = str(body.get("host", "")).strip() or None
        config_path = str(body.get("config_path", "")).strip() or None
        if backend != "dryrun" and not body.get("confirm_real_backend"):
            raise ValueError("Confirm that you intend to connect to a real hypervisor.")
        vm_controller.configure(backend=backend, vm_name=vm_name, host=host, vm_config_path=config_path)
        return jsonify({
            "ok": True,
            "message": "VM controller configured.",
            "vm": vm_controller.snapshot(),
            "warning": "DRY-RUN mode active (safe simulation)." if vm_controller.dry_run else "Real hypervisor commands enabled.",
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/vm/action")
def vm_action():
    body = request.get_json(silent=True) or {}
    action = str(body.get("action", "")).strip().lower()
    try:
        if not vm_controller.dry_run and not body.get("confirmed"):
            raise ValueError("Explicit confirmation is required before changing a real VM.")
        if action == "start":
            message = vm_controller.start()
        elif action == "shutdown":
            message = vm_controller.shutdown()
        elif action == "force_stop":
            if not body.get("force_confirmed"):
                raise ValueError("Force stop may lose guest work. Confirm separately.")
            message = vm_controller.force_stop()
        elif action == "set_vcpus":
            count = int(body.get("vcpus", 0))
            message = vm_controller.set_vcpus(count)
        elif action == "set_memory":
            memory = int(body.get("memory_mb", 0))
            message = vm_controller.set_memory_mb(memory)
        elif action == "restore":
            message = vm_controller.restore()
        elif action == "status":
            message = "VM state: " + vm_controller.status()
        else:
            raise ValueError("Unsupported VM action.")
        return jsonify({
            "ok": True,
            "message": message,
            "vm": vm_controller.snapshot(),
            "history": vm_controller.history[-20:],
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/vm/verify")
def vm_verify():
    """Verifies that the VM resource state matches hypervisor or simulation records."""
    verification = vm_controller.verify_configuration()
    return jsonify({
        "ok": True,
        "verified": verification.get("verified", True),
        "verification": verification,
        "verified_configuration": verification,
        "current_configuration": verification,
        "vm": vm_controller.snapshot(),
    })


@app.get("/api/vm/history")
def vm_history():
    """Returns the history of all VM resource reconfigurations."""
    return jsonify({
        "ok": True,
        "history": list(vm_controller.config_history),
        "total": len(vm_controller.config_history),
        "source": "VM configuration log",
    })


@app.post("/api/vm/apply_resources")
def vm_apply_resources():
    """Changes VM vCPUs and/or RAM, verifies the state, and records history."""
    body = request.get_json(silent=True) or {}
    try:
        vcpus = body.get("vcpus")
        memory_mb = body.get("memory_mb")
        if not vm_controller.dry_run and not body.get("confirmed"):
            raise ValueError("Confirmation required before changing a real hypervisor VM.")

        res = vm_controller.apply_resources(vcpus=vcpus, memory_mb=memory_mb)
        return jsonify({
            "ok": True,
            "message": res["message"],
            "verification": res["verification"],
            "current_configuration": res["verification"],
            "verified_configuration": res["verification"],
            "vm": vm_controller.snapshot(),
            "history": res["history"],
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.errorhandler(404)
def not_found(_error):
    return jsonify(error="Not found"), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    print(f"HyperVision Web App: http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
