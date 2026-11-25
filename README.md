
# HyperVision — Integrated Mini DBMS, Host Resource Monitoring, Bottleneck Analysis & VM Performance Optimization Platform

**HyperVision** is an integrated full-stack platform that connects in-memory database query execution with operating system and virtual machine resource monitoring, rule-based bottleneck classification, automated optimization recommendations, and before/after performance comparison benchmarking.

The platform is designed to run seamlessly on a standard workstation or laptop without requiring dedicated hypervisors or cloud infrastructure, providing both a modern **Flask Web Dashboard** and an interactive **Terminal Console**, backed by a 100% passing automated test suite (127 tests).

---

## 1. System Architecture

```
hypervision/
├── backend/
│   ├── mini_dbms/                    # SQL Database Engine
│   │   ├── parser.py                 # Tokenizer + recursive-descent SQL parser
│   │   ├── operators.py              # SCAN, INDEX_SCAN, FILTER, HASH_JOIN, AGGREGATE, SORT, DISTINCT, LIMIT
│   │   ├── indexes.py                # Hash & B-Tree indexes + IndexManager
│   │   ├── optimizer.py              # Predicate pushdown + index selection + query plans
│   │   ├── storage.py                # Schema tables and index maintenance
│   │   ├── settings.py               # Dynamic optimization knobs (pushdown, streaming agg, compiled filters, threads)
│   │   └── executor.py               # Unified statement dispatcher & chunked filter executor
│   ├── monitor/                      # Host & VM Observability
│   │   ├── resource_monitor.py       # Real psutil CPU/RAM/process sampler
│   │   └── vmi_adapter.py            # LibVMI introspection adapter (with honest simulation mode)
│   ├── vm/                           # Virtual Machine Controller
│   │   └── controller.py             # Xen (xl), KVM (virsh), safe dry-run manager, verification & config history
│   ├── analysis/                     # Bottleneck Classification & Recommendations
│   │   ├── classifier.py             # Rule-based diagnostic classifier
│   │   ├── recommender.py            # Actionable recommendations engine with expected impact badges
│   │   ├── correlator.py             # Event & resource timeline correlation
│   │   └── comparator.py             # Multi-run statistical comparator with before/after metric table & verdicts
│   ├── experiments/                  # Experiment Management
│   │   └── experiment_manager.py     # History, workloads, index benchmarks, result fingerprinting, modified SQL comparison
│   ├── cli/                          # Interactive Terminal Console & ANSI renderers
│   └── web_app.py                    # Flask Web Application & REST API
├── frontend/                         # Responsive Web Dashboard
│   ├── index.html                    # Single-page modern dashboard shell
│   ├── css/styles.css                # Dark/light theme styles, recommendation cards, comparison tables, modal styling
│   └── js/app.js                     # Real-time charts, forms, SQL review modal, API integrations
├── sample_data/                      # Bundled datasets (sales.csv, orders.csv)
├── logs/                             # Persisted experiment runs (experiments.json)
├── tests/                            # Comprehensive Automated Test Suite (127 tests)
│   ├── test_db.py                    # Mini DBMS core & operator tests
│   ├── test_indexing.py              # Hash and B-Tree index tests
│   ├── test_analysis.py              # Bottleneck classifier & comparator tests
│   ├── test_experiments.py           # Experiment manager & comparison tests
│   ├── test_vm.py                    # VM controller, verification & dry-run tests
│   ├── test_web_api.py               # Web REST API tests
│   └── test_full_workflow.py         # End-to-end user workflow integration tests
├── requirements.txt                  # Python dependencies
└── run.py                            # Application entry point (terminal or --web)
```

---

## 2. Quick Start & Setup

### Prerequisites
- Python 3.9+ (Python 3.10, 3.11, 3.12, 3.13, 3.14 tested and supported)
- Windows, macOS, or Linux

### Installation

1. Navigate to the project root:
   ```bash
   cd hypervision
   ```

2. (Optional) Create and activate a virtual environment:
   ```bash
   # Windows (PowerShell)
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1

   # Linux / macOS
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install all required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Launch the Flask Web Dashboard:
   ```bash
   python run.py --web
   ```
   Open **`http://127.0.0.1:5000`** in any web browser.

5. (Optional) Run the Interactive Terminal CLI:
   ```bash
   python run.py
   ```

---

## 3. Web Dashboard Sections & Features

### A. Overview Dashboard
- **KPI Summary Cards**: Total queries executed, successful and failed queries, average latency, latest query execution time, system CPU %, process RSS memory, active VM configuration, and total experiments recorded.
- **Query Performance Latency Chart**: Interactive chart tracking latency trends over time.
- **Resource Allocation Donut**: CPU, RAM, and available host headroom.
- **Latest Bottleneck & Optimization Results**: Displays the most recent bottleneck classification verdict and before/after improvement summary.

### B. Mini DBMS & SQL Query Lab
- **Interactive SQL Editor**: Write, format, run, and clear custom SQL queries directly in the browser.
- **One-Click SQL Presets**: Instant sample queries covering Aggregations, Hash Joins, Multi-Condition Filters, DML (INSERT, UPDATE, DELETE), and Indexes.
- **Query Execution Plan**: Flowchart representation showing operator pipeline stages (`SCAN`, `INDEX_SCAN`, `FILTER`, `HASH_JOIN`, `HASH_GROUP_BY`, `SORT`, `LIMIT`).
- **Live Results Table**: Dynamic paginated table showing returned rows, rows processed, and execution latency.
- **Execution Details**: Process CPU time, peak query heap allocation (tracemalloc), and index utilization.

### C. Recommendations & Optimization Engine
- **Actionable Advice Cards**: Each detected bottleneck automatically yields concrete optimization recommendations.
- **Impact Badges**: Clear visual tags highlighting expected resource improvements (`↓ RAM`, `↓ CPU`, `↓ Latency`).
- **Operator Observations & Explanations**: Details measured execution facts (e.g. *"HASH_GROUP_BY took 81% of query time"*) and plain-English engineering reasons.
- **"Apply One" Button**: Benchmarks and applies an individual recommendation (e.g., streaming aggregation, compiled predicates, hash index, thread tuning).
- **"Apply All Compatible" Button**: Combines all non-conflicting engine recommendations into a single pass and benchmarks the compound speedup.
- **"Review & Edit SQL" Workflow**: If a recommendation suggests changing the SQL query (such as narrowing a WHERE clause or adding a LIMIT):
  - Opens the **SQL Review & Confirmation Modal**.
  - Shows side-by-side diff between Current and Proposed SQL.
  - Allows the user to freely edit the SQL text before confirmation.
  - Benchmarks the revised query against the baseline and shows result set differences.

### D. Before and After Performance Comparison
Whenever recommendations are applied or modified SQL is benchmarked, HyperVision renders a detailed Before/After comparison table matching the rigorous performance specification:

| Metric | Before | After | Change | Result |
| :--- | :--- | :--- | :--- | :--- |
| **Latency** | 215.40 ms | 48.20 ms | `-77.6%` | <span style="color:#10b981">Better</span> |
| **CPU time** | 198.10 ms | 42.00 ms | `-78.8%` | <span style="color:#10b981">Better</span> |
| **Query memory (heap peak)** | 14.20 MB | 3.10 MB | `-78.2%` | <span style="color:#10b981">Better</span> |
| **Peak CPU (sampled, system)** | 48.0% | 22.0% | `-54.2%` | <span style="color:#10b981">Better</span> |
| **Process memory (RSS peak)** | 62.40 MB | 58.10 MB | `-6.9%` | <span style="color:#10b981">Better</span> |
| **Rows processed** | 25,000 | 500 | `-24,500` | <span style="color:#64748b">Informational</span> |
| **Worker threads** | 1 | 1 | `+0` | <span style="color:#64748b">Informational</span> |
| **VM vCPU** | 2 | 4 | `+2` | <span style="color:#64748b">Informational</span> |
| **VM RAM** | 1024 MB | 2048 MB | `+1024` | <span style="color:#64748b">Informational</span> |

- **Verdicts**: `Better` (green), `Worse` (red), `Same` (amber), `Informational` (slate), or `N/A`.
- **Result Set Equivalence Check**: Flags whether the optimized query produced identical rows or changed the result set intentionally (with clear explanation).
- **Summary Narrative**: Auto-generated text summary highlighting net improvement or regression.

### E. System & VM Resource Configuration Management
- **OS Observability**: System CPU %, RAM %, process RSS, and active thread counts via `psutil`.
- **VM Configuration Controls**:
  - View allocated vCPUs and RAM.
  - Select new vCPU count and RAM allocation.
  - **Apply Configuration Button**: Reconfigures resources with confirmation guardrails.
  - **Verify Configuration Button**: Queries hypervisor or verification engine to confirm actual allocation.
  - **Configuration History Log**: Complete audit table of all past reallocations with timestamps, old/new vCPUs, old/new RAM, backend mode, and verification status.
  - **Simulation & Real Modes**: Safe dry-run simulation mode by default for standard laptops; real Xen (`xl`) and KVM (`virsh`) integration available when supported hypervisors exist. Distinguishes vCPU allocations from Python worker threads.

### F. Database Explorer & Indexes
- **Table Browser**: Inspect schema, columns, row counts, and live data in in-memory tables (`sales`, `orders`).
- **Index Management**: Create and drop **Hash Indexes** ($O(1)$ equality lookups) and **B-Tree Indexes** (range comparisons).

### G. Performance Experiments & History
- **Automated Workload Generator**: Run multi-iteration query workloads to measure sustained throughput.
- **Index A/B Benchmark**: Execute queries with and without an index to measure exact speedup factor.
- **History Viewer & CSV Export**: Inspect up to 100 past experiment records and export complete telemetry as a `.csv` file.

---

## 4. Supported SQL Syntax

### SELECT Queries
```sql
-- Aggregation and grouping
SELECT department, AVG(salary) AS avg_sal, COUNT(*) AS cnt
FROM sales
WHERE salary > 50000
GROUP BY department
HAVING avg_sal > 60000
ORDER BY avg_sal DESC
LIMIT 10 OFFSET 0;

-- Hash Joins (INNER, LEFT, RIGHT)
SELECT sales.department, orders.amount, orders.order_id
FROM sales
JOIN orders ON sales.employee_id = orders.employee_id
WHERE orders.amount > 2000;

-- Complex boolean filters with parentheses
SELECT employee_id, department, salary
FROM sales
WHERE (region = 'North' OR region = 'South') AND salary >= 80000;
```

### Data Modification (DML)
```sql
-- Insert
INSERT INTO sales (employee_id, department, region, salary)
VALUES (99991, 'Engineering', 'West', 95000), (99992, 'Finance', 'East', 88000);

-- Update
UPDATE sales
SET salary = 110000
WHERE employee_id = 99991;

-- Delete
DELETE FROM sales
WHERE employee_id = 99991;
```

### Indexes (DDL)
```sql
-- B-Tree Index (Equality and Range queries: >, >=, <, <=, =)
CREATE INDEX idx_sales_salary ON sales (salary) USING BTREE;

-- Hash Index (O(1) Equality lookups: =)
CREATE INDEX idx_sales_dept ON sales (department) USING HASH;

-- Drop Index
DROP INDEX idx_sales_salary ON sales;
```

---

## 6. Running Automated Tests

HyperVision includes **127 automated unit and integration tests** covering all subsystems.

To run the entire test suite:
```bash
python -m unittest discover -s tests
```

To run individual test modules:
```bash
python -m unittest discover -s tests -p "test_db.py"
python -m unittest discover -s tests -p "test_indexing.py"
python -m unittest discover -s tests -p "test_analysis.py"
python -m unittest discover -s tests -p "test_experiments.py"
python -m unittest discover -s tests -p "test_vm.py"
python -m unittest discover -s tests -p "test_web_api.py"
python -m unittest discover -s tests -p "test_full_workflow.py"
```

---

## 7. College Project Demonstration Workflow

For an interactive college presentation or viva demonstration:

1. **Start the Web Dashboard**:
   Run `python run.py --web` and open `http://127.0.0.1:5000`.
2. **Execute an Unoptimized Query**:
   In SQL Query Lab, select the preset *"Aggregations on sales"* or run:
   ```sql
   SELECT department, AVG(salary) FROM sales WHERE salary > 50000 GROUP BY department
   ```
3. **Review Bottleneck Classification**:
   Observe the detected bottleneck (e.g., *CPU-bound / Scan-intensive*) and the confidence shown by the rule-based classifier.
4. **Inspect Recommendations Panel**:
   Notice the badges (`↓ RAM`, `↓ Latency`, `↓ CPU`), the measured observation, and the engineering rationale.
5. **Apply Recommendations**:
   - Click **"Apply All Compatible"** or click **"Apply"** on a specific recommendation.
   - For SQL recommendations, click **"Review & Edit SQL"** to inspect and modify the proposed query before confirming.
6. **Examine Before/After Comparison Table**:
   Point out the structured comparison table showing Before, After, Change %, and Result badges across all 9 metrics.
7. **Demonstrate VM Configuration Management**:
   Go to the VM Monitoring tab, change vCPU to 4 and RAM to 2048 MB, click **"Apply Configuration"**, verify the allocation via **"Verify Allocation"**, and inspect the **Configuration History Log**.
