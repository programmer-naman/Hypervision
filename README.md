
# 🚀 HyperVision
### Intelligent DBMS Performance Monitoring, Bottleneck Analysis & VM Resource Optimization Platform

HyperVision is an integrated database performance analysis and optimization platform that combines a Mini Database Management System (Mini DBMS), Operating System resource monitoring, bottleneck detection, virtual machine resource management, and performance experimentation.

The main objective of HyperVision is to analyze database query execution, identify performance bottlenecks, recommend suitable optimizations, and compare execution performance before and after applying those optimizations.

It provides an interactive Web Dashboard and a Terminal-based interface for executing SQL queries, monitoring system resources, managing experiments, and analyzing performance improvements.

---

## 📌 Key Objectives

- Execute SQL queries using a custom Mini DBMS.
- Monitor CPU, RAM, process memory, and thread usage.
- Identify CPU-bound, memory-bound, and I/O-related performance bottlenecks.
- Generate optimization recommendations based on query execution behavior.
- Experiment with different database execution settings.
- Compare query performance before and after optimization.
- Monitor and configure virtual machine resources (vCPU and RAM).
- Maintain experiment history for performance analysis.

---

## ✨ Key Features

### 1. Interactive SQL Query Engine
- Custom SQL parser and query execution engine.
- Supports SELECT, INSERT, UPDATE, and DELETE.
- Supports filtering, sorting, grouping, aggregation, and joins.
- Supports Hash and B-Tree indexing.
- Displays query execution plans and results.
- Tracks query execution latency and operator statistics.

### 2. OS Resource Monitoring
- Real-time CPU utilization monitoring.
- RAM and memory usage monitoring.
- Process-level CPU and memory monitoring.
- Active thread and process information.
- Resource utilization tracking during query execution.

### 3. Bottleneck Analysis
HyperVision analyzes query execution and resource utilization to identify possible performance bottlenecks.

Major bottleneck categories include:
- CPU-bound
- Memory-bound
- I/O-bound
- Parallelism and resource saturation
- Optimal / Balanced

The classification engine uses resource measurements, query latency, and execution statistics to generate diagnostic results.

### 4. Optimization Recommendation Engine
The system generates recommendations based on detected bottlenecks.

Available optimization techniques include:

- Predicate Pushdown
- Compiled Predicates
- Streaming Aggregation
- Building Hash Tables on the Smaller Join Side
- Thread Count Tuning
- Index-based Query Optimization
- SQL Query Optimization

Recommendations include explanations of why an optimization may improve performance and which resources it is expected to affect.

### 5. Performance Experiment Manager
The Experiment Manager manages database performance experiments.

It performs the following operations:

1. Executes the SQL query.
2. Generates the query execution plan.
3. Monitors CPU and memory utilization.
4. Collects query execution statistics.
5. Identifies performance bottlenecks.
6. Generates optimization recommendations.
7. Saves experiment results in JSON format.

Each experiment is assigned a unique ID for tracking and comparison.

### 6. Before & After Performance Comparison
HyperVision compares query execution under different configurations.

The comparison includes:

| Metric | Description |
|---|---|
| Query Latency | Total query execution time |
| CPU Time | CPU time consumed during execution |
| Memory Usage | Peak query memory allocation |
| CPU Utilization | System CPU usage |
| Process Memory | Process RSS memory |
| Rows Processed | Number of rows processed |
| Worker Threads | Threads used during execution |
| VM vCPU | Allocated virtual CPUs |
| VM RAM | Allocated virtual memory |

The system also checks result-set equivalence to help verify that performance optimization has not unintentionally changed query results.

### 7. Virtual Machine Resource Management
HyperVision provides VM resource configuration and monitoring.

Supported backends:

- Dry-run (Simulation Mode)
- Xen Hypervisor
- KVM / Libvirt

Features:
- Monitor allocated vCPUs.
- Monitor allocated RAM.
- Change VM CPU and memory allocation.
- Verify VM resource configuration.
- Maintain VM configuration history.

**Note:** Dry-run mode is enabled by default. It simulates VM configuration changes without modifying an actual virtual machine. Real Xen and KVM operations require a supported hypervisor environment and appropriate permissions.

---

## 🏗️ System Architecture

```text
                    HyperVision
                         |
             +-----------+-----------+
             |                       |
        Web Dashboard           Terminal CLI
             |                       |
             +-----------+-----------+
                         |
                    Flask API
                         |
          +--------------+--------------+
          |              |              |
       Mini DBMS     Experiment      VM Controller
          |           Manager            |
          |              |               |
       SQL Engine    Resource         Xen / KVM /
       Optimizer     Monitoring       Dry-run
       Indexing          |
          |          Bottleneck
          |           Analysis
          |              |
          +------- Performance
                    Comparison
                         |
                  Experiment History
```

---

## 📂 Project Structure

```text
HyperVision/
│
├── backend/
│   ├── mini_dbms/
│   │   ├── parser.py
│   │   ├── executor.py
│   │   ├── operators.py
│   │   ├── optimizer.py
│   │   ├── indexes.py
│   │   ├── storage.py
│   │   └── settings.py
│   │
│   ├── monitor/
│   │   ├── resource_monitor.py
│   │   └── vmi_adapter.py
│   │
│   ├── vm/
│   │   └── controller.py
│   │
│   ├── experiments/
│   │   └── experiment_manager.py
│   │
│   ├── analysis/
│   │   ├── classifier.py
│   │   ├── recommender.py
│   │   ├── correlator.py
│   │   └── comparator.py
│   │
│   ├── cli/
│   ├── data/
│   └── web_app.py
│
├── frontend/
│   ├── index.html
│   ├── css/
│   │   └── styles.css
│   └── js/
│       ├── app.js
│       └── data.js
│
├── sample_data/
│   ├── sales.csv
│   └── orders.csv
│
├── logs/
│   └── experiments.json
│
├── tests/
│
├── requirements.txt
├── TEAM_SPLIT.md
├── run.py
└── README.md
```

---

## 🛠️ Technologies Used

| Technology | Purpose |
|---|---|
| Python | Backend development and DBMS implementation |
| Flask | Web application and REST API |
| HTML5 | Dashboard structure |
| CSS3 | Dashboard styling |
| JavaScript | Frontend interactions and API integration |
| psutil | OS resource monitoring |
| CSV | Sample database storage |
| JSON | Experiment history and configuration logs |
| Git & GitHub | Version control and collaboration |

---

## 🖥️ Operating System Concepts Used

HyperVision demonstrates several important Operating System concepts.

| OS Concept | Application in HyperVision |
|---|---|
| Process Management | Monitoring running processes and resource consumption |
| Threading | Parallel execution of large filtering workloads |
| CPU Scheduling | Analyzing CPU utilization and execution behavior |
| Memory Management | Monitoring RAM and query memory allocation |
| Virtualization | Managing VM resources through Xen and KVM |
| Resource Allocation | Adjusting vCPU and RAM configurations |
| Performance Monitoring | Collecting CPU, memory, and execution metrics |

---

## 🗄️ DBMS Concepts Used

| DBMS Concept | Application |
|---|---|
| SQL Parsing | Parsing SQL statements into executable query structures |
| Query Processing | Executing SQL using a custom execution engine |
| Query Optimization | Improving query execution using optimization techniques |
| Indexing | Hash and B-Tree indexes |
| Hash Join | Performing table joins |
| Aggregation | COUNT, SUM, AVG and GROUP BY operations |
| Filtering | Applying WHERE conditions |
| Sorting | ORDER BY operations |
| DML | INSERT, UPDATE and DELETE |
| Query Execution Plan | Representing the sequence of execution operators |

---

## ⚙️ Installation & Setup

### Prerequisites

- Python 3.9 or above
- pip package manager
- Git (optional)

### Step 1: Clone the Repository

```bash
git clone https://github.com/USERNAME/REPOSITORY-NAME.git
```

### Step 2: Navigate to Project Directory

```bash
cd HyperVision
```

### Step 3: Create Virtual Environment

```bash
python -m venv .venv
```

Activate the environment:

**Windows:**
```bash
.venv\Scripts\activate
```

**Linux / macOS:**
```bash
source .venv/bin/activate
```

### Step 4: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 5: Run Web Dashboard

```bash
python run.py --web
```

Open your browser and visit:

http://127.0.0.1:5000

### Step 6: Run Terminal Version (Optional)

```bash
python run.py
```

---

## 🧪 Running Tests

To execute the automated test suite:

```bash
python -m unittest discover -s tests
```

---

## 📊 Demonstration Workflow

The following workflow demonstrates the main functionality of HyperVision:

1. Launch the HyperVision Web Dashboard.
2. Open the SQL Query Lab.
3. Execute a sample SQL query.
4. Observe the query execution plan and execution time.
5. Check CPU and memory utilization.
6. Open the Bottleneck Analysis section.
7. Review the detected bottleneck and optimization recommendations.
8. Apply a recommended optimization.
9. Execute the same query again under the updated configuration.
10. Analyze the Before & After Performance Comparison.
11. Explore VM resource configuration and experiment history.

---

## 🎯 Expected Outcome

HyperVision aims to provide a practical environment for understanding the relationship between database query execution and operating system resource utilization.

It helps demonstrate how query optimization, resource monitoring, parallel execution, memory management, and virtual machine resource allocation can influence database performance.

The platform also provides measurable performance comparisons that help users understand the impact of different optimization techniques.

---

## 🔮 Future Scope

- Advanced query optimization techniques.
- Improved resource prediction and analysis.
- Integration with additional hypervisors.
- More advanced parallel query execution.
- Extended database workload benchmarking.
- Enhanced performance visualization.

---

## 👨‍💻 Project Information

**Project Name:** HyperVision

**Project Type:** Academic / Software Engineering Project

**Domain:** Database Management Systems, Operating Systems, Performance Optimization and Virtualization.

**Interface:** Web Dashboard + Terminal CLI

**Backend:** Python and Flask

---

## 📜 License

This project is developed for academic and educational purposes.
