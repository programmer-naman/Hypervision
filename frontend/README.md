# HyperVision Web Platform & REST API

The HyperVision platform integrates a Python Mini DBMS, operating-system monitoring, virtual-machine management, rule-based bottleneck classification, and performance experiment benchmarking into a modern web dashboard.

---

## 1. Quick Start Guide (Windows / Linux / macOS)

1. Open PowerShell or Terminal in the project root:
   ```powershell
   cd hypervision
   ```

2. (Recommended) Create and activate a Python virtual environment:
   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   # Linux/macOS: source .venv/bin/activate
   ```

3. Install all required dependencies:
   ```powershell
   pip install -r requirements.txt
   ```

4. Start the Flask Web Dashboard:
   ```powershell
   python backend/web_app.py
   ```

5. Open your web browser at:
   **`http://127.0.0.1:5000`**

---

## 2. Platform Architecture & Features

### A. Overview Dashboard
- Total queries executed & average execution latency
- Live CPU, RAM, and active OS process telemetry
- Slowest queries list with microsecond precision
- Rule-based bottleneck diagnostic signals
- Recommended database & system optimizations with instant "Apply & Benchmark"

### B. SQL Query Lab
- **SELECT**: Column projections, aliases (`AS`), aggregates (`COUNT`, `SUM`, `AVG`, `MIN`, `MAX`), `DISTINCT`, multi-table `JOIN` (INNER, LEFT, RIGHT), rich boolean `WHERE` with `AND`, `OR`, `NOT`, and parentheses, `GROUP BY`, `HAVING`, `ORDER BY` (ASC/DESC), and `LIMIT` / `OFFSET`.
- **INSERT**: `INSERT INTO table (columns) VALUES (...)` with schema validation and automatic index maintenance.
- **UPDATE**: `UPDATE table SET col = val WHERE ...` with index synchronization.
- **DELETE**: `DELETE FROM table WHERE ...` with index cleanup.
- **INDEXES**: `CREATE INDEX idx ON table (col) [USING HASH|BTREE]`, `DROP INDEX idx ON table`.
- Execution plan inspection with individual operator row counts and timings.

### C. Database Explorer
- In-memory tables loaded from CSV files (including `sales` and `orders`).
- Column types, row counts, and sample record inspection.
- **Index Manager**: Inspect active Hash/B-Tree indexes, create new indexes via GUI form, or drop existing indexes.

### D. System & VM Monitoring
- Real-time CPU, RAM, disk, active threads, and process table loaded via `psutil`.
- VM Controller: Safe dry-run simulation mode by default (no Xen installation needed on developer laptops). Real Xen (`xl`) and KVM (`virsh`) management available with explicit confirmation.
- Clear transparency labels for real host metrics vs simulated VM telemetry.

### E. Bottleneck Classification
- Rule-based classifier using measured CPU, memory, and latency data.
- Classifies queries into 5 distinct categories:
  1. `CPU-bound`
  2. `Memory pressure`
  3. `I/O-bound` (Slow-query / full-table scan bottleneck)
  4. `Parallel/resource saturation`
  5. `Optimal / Balanced`
- Displays predicted probabilities across all 5 classes and highlights top contributing features.
- Model evaluation metrics (Accuracy, 5-Fold Cross Validation, Macro F1-score) and actionable bottleneck findings.

### F. Benchmarking & Experiments
- Automated test workload generator.
- Index A/B benchmark tool (compares query execution with vs without index and verifies hash fingerprint equivalence).
- Side-by-side comparison of any two saved experiments.
- CSV export for query history and performance measurements (`/api/history/export`).

---

## 3. Complete REST API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Backend connection health and loaded tables |
| `GET` | `/api/overview` | Aggregated dashboard statistics and slowest queries |
| `POST` | `/api/query` | Execute SQL statement (SELECT, INSERT, UPDATE, DELETE, INDEX) |
| `GET` | `/api/tables` | List loaded tables, row counts, column types, and schema |
| `GET` | `/api/tables/<name>` | Retrieve sample rows and column definitions for a table |
| `GET` | `/api/indexes` | List all active Hash and B-Tree indexes |
| `POST` | `/api/indexes` | Create an index on a table column |
| `POST` | `/api/indexes/drop` | Drop an index |
| `GET` | `/api/metrics` | Real-time host CPU, RAM, threads, and disk metrics via psutil |
| `GET` | `/api/processes` | Active operating system processes list |
| `GET` | `/api/vm` | Hypervisor / VM status snapshot |
| `POST` | `/api/vm/configure` | Configure VM backend (dryrun, xen, kvm) |
| `POST` | `/api/vm/action` | Perform lifecycle or resource resize actions on VM |
| `GET` | `/api/analysis` | Latest query diagnosis (rule-based bottleneck analysis) |
| `POST` | `/api/recommendations/apply` | Apply an optimization setting and run A/B benchmark |
| `GET` | `/api/history` | Historical query runs and execution records |
| `GET` | `/api/history/export` | Export query history and metrics as CSV |
| `POST` | `/api/experiments/compare` | Compare two saved experiments |
| `POST` | `/api/experiments/compare_index` | Benchmark query execution with vs without index |
| `POST` | `/api/experiments/workload` | Run automated test workload |
