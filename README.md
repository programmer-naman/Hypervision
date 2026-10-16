# 🖥️ HyperVision — Resource-Aware Database Experiment System

HyperVision is a Python-based database experiment system that runs SQL queries on its own mini database engine, measures CPU and memory usage, identifies performance bottlenecks, and recommends specific improvements. Users can apply recommendations, rerun the same query, and compare the results before and after optimization, with optional virtual machine resource control.

## 📌 About the Project

HyperVision aims to answer a simple question: *"My query is slow, so what should I change?"*

It combines a small in-memory database engine, resource monitoring, bottleneck classification, and an apply-and-compare workflow. Each query becomes an **experiment**. HyperVision records the query plan, execution time, CPU and RAM usage, and bottleneck classification. It then recommends actions to reduce CPU usage, memory consumption, and query latency.

## ✨ Key Features

- **Mini DBMS:** SQL parser, query optimizer, and executor supporting `SELECT`, `JOIN`, `WHERE`, `GROUP BY`, aggregates, and `ORDER BY`.
- **Resource Monitoring:** Measures CPU and memory usage while queries execute.
- **Bottleneck Detection:** Classifies query performance as CPU-bound, Memory Pressure, I/O-bound, Parallel/Resource Saturation, or Mixed.
- **Smart Recommendations:** Provides ranked, query-specific actions and identifies whether they target CPU, RAM, or latency.
- **Apply and Compare:** Applies recommendations, reruns queries, and compares performance using median-of-runs measurements.
- **Result Safety Check:** Verifies that query results remain identical after optimization.
- **VM Resource Control:** Supports changing the vCPUs and memory of a real Xen or KVM virtual machine, with a safe dry-run mode.
- **Experiment History:** Saves each experiment with a unique ID (`EXP-1001`, `EXP-1002`, etc.).
- **Concurrency Demo:** Includes a lock manager with deadlock detection.

## 🛠️ Tech Stack

- **Programming Language:** Python 3.8+
- **Database Engine:** Custom in-memory mini DBMS
- **Resource Monitoring:** psutil
- **VM Control (Optional):** Xen (`xl`), KVM/libvirt (`virsh`), SSH
- **Testing:** Python unittest (74 tests)
- **Core Concepts:** Query Optimization, Performance Analysis, Virtualization, Concurrency Control

## 🧠 Concepts and Algorithms Used

| Concept | Purpose |
|---|---|
| Hash Join | Combines two tables using a matching key |
| Hash Group-By / Streaming Aggregation | Groups rows and calculates COUNT, SUM, AVG, MIN, and MAX |
| Predicate Pushdown | Filters rows before joining to reduce unnecessary processing |
| Rule-Based Classification | Identifies bottlenecks using CPU, memory, and latency measurements |
| Median-of-Runs Comparison | Compares performance while reducing measurement noise |
| Wait-For Graph | Detects deadlocks between transactions |

## 🔍 Bottleneck Classes

| Class | Meaning |
|---|---|
| CPU-bound | CPU processing is the main limitation |
| Memory Pressure | Memory usage is high or becoming a limitation |
| I/O-bound | Query execution is slow despite relatively low CPU usage |
| Parallel/Resource Saturation | Additional threads or resources provide limited improvement |
| Mixed | Multiple resources contribute to the performance problem |

## 💡 Recommendations You Can Apply

| Recommendation | Reduces | What It Does |
|---|---|---|
| Filter Before the Join | CPU, RAM, latency | Applies WHERE conditions before joining tables |
| Build Join on the Smaller Table | RAM, latency | Reduces the size of the join's hash table |
| Streaming Aggregation | RAM, CPU, latency | Maintains running aggregate values instead of storing all rows for every group |
| Compiled Predicates | CPU, latency | Reduces function-call overhead during filtering |
| Fewer Threads | CPU, latency | Reduces unnecessary thread overhead |
| Resize the VM | Latency, memory constraints | Adjusts vCPUs or memory for a Xen/KVM virtual machine |
| Edit the SQL | CPU, RAM, latency | Suggests query changes, such as adding a more selective WHERE condition |

On the sample data, applying recommendations reduced latency and CPU time by approximately 36–60% and query memory by 15–83%, while preserving identical results in the tested experiments.

*Note: Actual improvements depend on the query, dataset, system resources, and applied optimization.*

## ⚙️ Installation and Setup

### Prerequisites

- Python 3.8 or newer
- Git installed on your system

### 1. Clone the Repository

Replace `YOUR_USERNAME` with your GitHub username.

```bash
git clone https://github.com/YOUR_USERNAME/HyperVision.git
cd HyperVision
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Run the Application

```bash
python run.py
```

On the first run, HyperVision offers to generate sample data files such as `sales.csv` and `orders.csv`. Press Enter to accept if prompted.

## 🕹️ How to Use

Example commands:

```text
hypervision> examples
hypervision> example 1
hypervision> recs
hypervision> apply 1 2
hypervision> history
hypervision> compare EXP-1001 EXP-1002
hypervision> help
```

| Command | What It Does |
|---|---|
| `<SQL>` | Runs a query as a new experiment |
| `examples` / `example N` | Lists or runs example queries |
| `recs` | Displays ranked recommendations |
| `apply N` / `apply all` | Applies recommendations, reruns the query, and compares performance |
| `undo` / `reset` | Restores previous settings or defaults |
| `settings` / `set NAME VALUE` | Displays or changes tuning settings |
| `compare ID1 ID2` | Compares two saved experiments |
| `history` / `view ID` | Displays experiment history or details |
| `vm setup` / `vm status` | Sets up or checks virtual machine control |
| `vm vcpus N` | Changes the configured VM's virtual CPU count |
| `vm memory MB` | Changes the configured VM's memory allocation |
| `vm restore` | Restores the previous VM configuration |
| `txn` | Runs the deadlock detection demonstration |

You can also run a query directly:

```bash
python run.py -q "SELECT region, COUNT(*) FROM sales GROUP BY region" --apply all
```

## 🖧 Virtual Machine Control

HyperVision supports optional virtual machine resource management through Xen or KVM/libvirt. When configured for remote control, it sends resource adjustment commands to the host over SSH.

Example using Xen:

```bash
python run.py --vm-backend xen --vm-name guest1 --vm-host root@192.168.1.10
```

Use the dry-run backend to inspect the commands without changing actual VM resources.

```bash
python run.py --vm-backend dryrun
```

**Important:** Real VM control requires a compatible virtualization host, appropriate permissions, and a correctly configured guest VM. A Windows laptop alone does not provide the Xen host environment required for direct Xen resource control.

For more details, see [`docs/GUIDE.md`](docs/GUIDE.md).

## 🗂️ Project Structure

```text
HyperVision/
├── backend/
│   ├── mini_dbms/      # Parser, optimizer, operators, executor, settings, lock manager
│   ├── data/           # Sample data generator
│   ├── monitor/        # Resource monitor and simulated VMI adapter
│   ├── analysis/       # Classifier, recommender, comparator, correlator
│   ├── vm/             # VM controller for Xen, KVM, and dry-run
│   ├── experiments/    # Experiment manager
│   └── cli/             # Command interface
├── tests/               # Unit tests
├── scripts/             # Git workflow helper
├── run.py               # Application entry point
├── TEAM_SPLIT.md        # Project contribution reference
└── requirements.txt
```

## ✅ Running the Tests

Run the following command to execute the unit tests:

```bash
python -m unittest discover -s tests
```

## 👥 Team

**Team Name:** The Hypervisors

**Team Members:**
- Mahesh Singh
- Naman Garg
- Karishma Rastogi

## 🎯 Project Objective

The objective of HyperVision is to identify and improve slow database queries by combining a custom database engine, resource measurement, bottleneck detection, and an apply-and-compare workflow.

## 🚀 Future Scope

- Integration with a real LibVMI layer instead of the simulated adapter
- Truly parallel query execution across multiple cores
- Extended SQL support, including `LIMIT`, indexes, and subqueries
- Improved bottleneck classification using machine learning
- Support for additional query optimization techniques and virtualization environments

---

**HyperVision — Measure It, Fix It, Prove It.**
