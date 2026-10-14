<<<<<<< HEAD
# HyperVision — Mini DBMS + Resource-Aware Experiment Engine (Terminal Edition)

A 5th-semester project implementing the DBMS + resource monitoring +
correlation + dashboard pieces described in the HyperVision report. It
runs entirely on a normal laptop — **no Xen hypervisor or LibVMI
installation is required** to try it out.

---

## 1. Project structure

```
hypervision/
├── backend/
│   ├── mini_dbms/                # Person 1
│   │   ├── storage.py            # loads CSV files into in-memory tables
│   │   ├── parser.py             # turns SQL text into a ParsedQuery object
│   │   ├── settings.py           # ExecutionSettings: the tuning knobs recommendations change
│   │   ├── optimizer.py          # query plan + predicate pushdown
│   │   ├── operators.py          # SCAN, FILTER, HASH_JOIN, PROJECTION, GROUP/AGGREGATE, SORT
│   │   ├── executor.py           # runs the plan; threads + optimisation switches
│   │   └── transactions.py       # lock manager + deadlock detection demo
│   ├── data/
│   │   └── sample_data_generator.py   # Person 1
│   ├── vm/                       # Person 2
│   │   └── controller.py         # resizes a real VM: Xen (xl), KVM (virsh), or dry-run
│   ├── monitor/                  # Person 2
│   │   ├── resource_monitor.py   # samples CPU/RAM while a query runs
│   │   └── vmi_adapter.py        # simulated stand-in for a real LibVMI layer
│   ├── analysis/                 # Person 2
│   │   ├── correlator.py         # lines up DB events with resource samples
│   │   ├── classifier.py         # rule-based bottleneck classification
│   │   ├── recommender.py        # bottleneck -> specific CPU / RAM / latency actions
│   │   └── comparator.py         # before/after comparison of two runs
│   ├── experiments/              # Person 3
│   │   └── experiment_manager.py # runs an experiment, A/B-compares settings, saves to JSON
│   └── cli/                      # Person 3 (terminal console, replaces the web dashboard)
│       ├── __init__.py           # command-line arguments + entry point
│       ├── console.py            # interactive commands (apply, compare, undo, ...)
│       ├── render.py             # prints experiments, recommendations, comparisons
│       └── ui.py                 # colours + text tables
├── tests/                        # one test file per area (python -m unittest discover -s tests)
├── scripts/commit_part.sh        # helper for the 3-person GitHub workflow
├── sample_data/                  # sales.csv / orders.csv generated here
├── logs/experiments.json         # saved experiment history
├── run.py                        # start here
├── TEAM_SPLIT.md                 # who owns what + exact git steps
└── requirements.txt
```

---

## 2. How to run it

```bash
# 1. (Recommended) create a virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 2. Install the only dependency (psutil)
pip install -r requirements.txt

# 3. Start the terminal console
python run.py
```

On first start, if no tables exist, it asks whether to generate the sample
data (`sales.csv`, `orders.csv`). You can also run
`python backend/data/sample_data_generator.py` yourself, or type `generate`
inside the console.

### Terminal commands

| Command | What it does (dashboard equivalent) |
|---|---|
| `<SQL>` | Run a query as a new experiment (Run Query button) |
| `examples` / `example N` | List / run the example queries |
| `recs` | Show the ranked recommendations of the latest experiment again |
| `apply N [N ...]` / `apply all` | **Apply recommendation(s), rerun, and compare before/after** |
| `undo` / `reset` | Restore the previous settings / restore the defaults |
| `settings` | Show the current tuning settings |
| `set NAME VALUE` | Change a setting by hand, e.g. `set pushdown_filter on` |
| `threads N` | Shortcut for `set thread_count N` |
| `vm setup BACKEND [NAME] [HOST]` | Connect VM control: `dryrun`, `xen` or `kvm` |
| `vm status` / `vm vcpus N` / `vm memory MB` / `vm restore` | Show / resize / restore the virtual machine |
| `compare ID1 ID2` | Compare two saved experiments |
| `runs N` | Runs per side used by `apply` (default 3) |
| `rerun [ID] [N]` | Rerun the last (or given) experiment with N threads (Rerun button) |
| `txn` | Concurrency / deadlock demo |
| `history` | Experiment history, newest 25 |
| `view ID` | Show a saved experiment, e.g. `view EXP-1001` |
| `tables` | Loaded tables, columns and row counts |
| `generate` | (Re)generate the sample CSV data |
| `help`, `quit` | Help / exit |

End a line with `\` to continue the SQL on the next line.

One-shot mode (no interactive prompt):

```bash
python run.py -q "SELECT region, COUNT(*) FROM sales GROUP BY region" -t 4
python run.py -q "SELECT region, COUNT(*) FROM sales GROUP BY region" --apply all
python run.py --txn-demo
python run.py --history
```

## 3. Recommendations: what they say and how to make the changes

After every query the console prints a ranked list of **specific actions**.
Each one says what to change, why (using that query's own numbers, e.g.
"HASH_GROUP_BY took 87% of the operator time"), and which of **CPU, RAM and
latency** it should reduce (`↓CPU ↓RAM ↓Latency`). The order depends on the
detected bottleneck: a CPU-bound run puts CPU savers first, a memory-pressure
run puts RAM savers first, and so on.

There are two kinds of recommendation:

| Tag | What it is | How you make the change |
|---|---|---|
| `[apply]` | A change to an engine setting | Type `apply N` (or `apply all`). The tool applies it, reruns the same query, and prints a before/after comparison. |
| `[edit SQL]` | A change only you can make (e.g. "add a more selective WHERE") | Edit your query and run it, then compare the two experiments: `compare EXP-1001 EXP-1002`. |
| `[apply · experiment]` | A "try it and measure" suggestion (e.g. 2 threads) | Same as `apply`, but expect little or no gain: Python threads share the GIL. |

| Setting (recommendation) | Reduces | What it does |
|---|---|---|
| `pushdown_filter` — filter each table before the join | CPU, RAM, latency | WHERE conditions that touch one table run before the join, so it handles fewer rows |
| `build_smaller_side` — hash the smaller table | RAM, latency, CPU | Builds the join hash table on the smaller input |
| `streaming_aggregation` — streaming GROUP BY | RAM, CPU, latency | Keeps running totals per group instead of every row of every group |
| `compiled_predicates` — compiled filters | CPU, latency | Inlines each comparison instead of calling a function per row |
| `thread_count` — fewer threads | CPU, latency | Removes GIL scheduling overhead |

Every setting is **off by default** and none of them changes a query's result
(the only visible difference is that a JOIN may list its rows in another
order when there is no ORDER BY). `apply` checks this and prints
*"Query results are identical before and after"*.

### The apply-and-compare loop

```
hypervision> example 1            # run a query
hypervision> apply 1 2            # apply recommendations 1 and 2, rerun, compare
hypervision> undo                 # don't like the result? go back
hypervision> set streaming_aggregation on    # or change a setting by hand
```

`apply` runs the original settings and the new settings alternately
(3 runs each by default, `runs N` to change), compares the **median** of
each metric, and reports a CPU / RAM / latency verdict. Changes smaller than
5% are shown as "same" because single measurements jitter.

| Metric | What is measured |
|---|---|
| Latency | how long the query took |
| CPU time | CPU seconds the process used during the query (`time.process_time`) |
| Query memory (heap peak) | peak Python memory allocated by the query (`tracemalloc`, in a separate untimed probe) |
| Peak CPU / process memory * | sampled by the resource monitor; informational and noisy |

Typical results on the sample data (your numbers will differ):

| Query | Applied | Latency | CPU time | Query memory |
|---|---|---|---|---|
| Avg salary by dept | streaming aggregation + compiled predicates | −54% | −54% | −15% |
| Count by region | streaming aggregation | −60% | −56% | −55% |
| Join demo | pushdown + compiled predicates | −56% | −55% | −66% |
| Join, big table on the right | pushdown + smaller build side + compiled | −36% | −36% | −83% |

## 4. Changing the real virtual machine (vCPUs and memory)

The engine settings above change *software*. The `vm` commands change the
*virtual machine itself*: how many vCPUs and how much memory it has. Then you
rerun the query and compare, which is the "change a resource, rerun, compare"
loop from the HyperVision report.

### Where each part runs

```
   ┌──────────────── host (Xen dom0 / KVM server) ───────────────┐
   │  xl vcpu-set / xl mem-set     (or virsh setvcpus / setmem)  │
   │            ▲                                                │
   │            │ ssh  (key login, no password)                  │
   │   ┌────────┴────────── guest VM ───────────────┐            │
   │   │  python run.py   <- HyperVision runs HERE  │            │
   │   │  runs the query, measures CPU and RAM      │            │
   │   └────────────────────────────────────────────┘            │
   └─────────────────────────────────────────────────────────────┘
```

HyperVision runs **inside the guest** (that is where the query runs and where
CPU/RAM are measured). It sends the resize command to the **host** over ssh.
If you run HyperVision directly on the host, leave the host address out.

### Try it safely first (dry-run)

```
hypervision> vm setup dryrun demo-vm
hypervision> vm vcpus 4
  [dry-run] would run: xl vcpu-set demo-vm 4
```

Dry-run prints the exact command and changes nothing. Use it for demos.

### Real Xen

One-time preparation on the host (dom0):
1. In the VM's config file set a ceiling above what you want, e.g. `vcpus = 2`,
   `maxvcpus = 8`, `memory = 2048`, `maxmem = 8192`, then restart the VM once.
   (A running VM can only grow up to `maxvcpus` / `maxmem`.)
2. From the guest, make ssh login to the host work without a password:
   `ssh-keygen` then `ssh-copy-id root@<dom0-address>`.

Then, inside the guest:

```bash
python run.py --vm-backend xen --vm-name guest1 --vm-host root@192.168.1.10
```

or, from the console: `vm setup xen guest1 root@192.168.1.10`.

### Real KVM / libvirt

Same idea with `virsh`: set the maximum vCPUs / memory in the VM definition
(`virsh edit <name>`), then `vm setup kvm <name> root@<host-address>`.
(The VM needs the memory balloon device, which libvirt adds by default.)

### Using it

```
hypervision> vm status                       # hypervisor's view vs what this machine sees
hypervision> example 1                       # run a query
hypervision> apply 3                         # a recommendation tagged [apply · VM]
hypervision> vm restore                      # put the VM back to its original size
hypervision> vm vcpus 2                      # or resize by hand
hypervision> vm memory 4096
```

`[apply · VM]` recommendations appear only after `vm setup`, and depend on the
bottleneck: CPU-bound → more vCPUs (flagged as an experiment), memory pressure
→ more memory, saturation → fewer vCPUs. `apply all` never resizes the VM; you
must pick the number. For a VM change the order is: warm-up → "before" runs → resize →
wait until the guest reports the new size → "after" runs → comparison. These runs cannot be
interleaved, so they drift more: at least 5 runs per side are used and differences under 15%
are reported as "same" (engine-setting comparisons use 3 runs and 5%). Treat a small win
as unproven and repeat it.

### Honest limits

* More vCPUs only help when the work can use more cores. This engine's filter
  uses Python threads that share one interpreter lock, so expect little or no
  speed-up from extra vCPUs. The comparison reports whatever it measures.
* Memory changes make room for the guest; they do not make the query itself use less.
* After adding vCPUs, a Linux guest may need them switched on:
  `echo 1 | sudo tee /sys/devices/system/cpu/cpu*/online`.
* Hot-resizing memory needs the balloon driver in the guest.
* The Xen / KVM command path is tested here with stand-in `xl`, `virsh` and `ssh`
  programs, not with a live hypervisor, so try dry-run first on your own setup.

## 5. Example queries to try

```sql
SELECT department, AVG(salary) FROM sales WHERE salary > 50000 GROUP BY department
SELECT region, COUNT(*) FROM sales GROUP BY region
SELECT department, SUM(salary) FROM sales WHERE region = North GROUP BY department
SELECT sales.department, orders.amount FROM sales JOIN orders ON sales.employee_id = orders.employee_id WHERE orders.amount > 4000
SELECT orders.amount, sales.department FROM orders JOIN sales ON orders.employee_id = sales.employee_id WHERE sales.salary > 90000
```

Paste a query at the `hypervision>` prompt, then look at:
- **Query Plan** — the operator steps the query went through (SCAN → FILTER → HASH_GROUP_BY, etc.)
- **Latency / CPU time / Peak CPU / Peak RAM** — measured while the query ran
- **Bottleneck Analysis** — which of the 5 categories this run was classified as
- **Recommendations** — ranked actions; try `apply 1`
- **`rerun N`** — re-run the same query with more simulated "vCPUs"

**`txn`** runs a separate demo of the lock
manager: two transactions request two tables in opposite order, and the
tool reports the second conflicting request as a detected deadlock.

## 6. Tests

```bash
python -m unittest discover -s tests
```

72 tests cover: every combination of optimisation settings returns the same
data as the default engine, the recommender's rules and ranking, the
comparator's maths, the A/B experiment flow, the console commands, and the VM
controller (Xen/KVM commands, ssh wrapping, dry-run, failures).

---

## 7. How to explain this project to your evaluator

**One-line summary:**
"We built a small SQL engine ourselves, ran queries against it while
measuring real CPU/memory usage, and used simple rules to figure out
*why* a query was slow and what to change — the same idea Xen + VMI
would apply at the virtual-machine level, just running locally instead
of inside a real hypervisor."

**Map of report concept → code file:**

| Report concept | Where it is in the code |
|---|---|
| SQL Parser | `backend/mini_dbms/parser.py` |
| Query Plan / Optimizer | `backend/mini_dbms/optimizer.py` |
| Execution operators (Scan, Filter, Join, Aggregate) | `backend/mini_dbms/operators.py` |
| Transactions / locks / deadlock detection | `backend/mini_dbms/transactions.py` |
| OS-level resource observation | `backend/monitor/resource_monitor.py` |
| VMI (guest introspection) layer | `backend/monitor/vmi_adapter.py` |
| Correlation of DB events + resource events | `backend/analysis/correlator.py` |
| Bottleneck classification | `backend/analysis/classifier.py` |
| Recommendation engine (actions for CPU / RAM / latency) | `backend/analysis/recommender.py` |
| Apply a change, rerun, compare | `backend/analysis/comparator.py`, `ExperimentManager.compare_settings` |
| Tuning knobs the recommendations change | `backend/mini_dbms/settings.py` |
| Changing vCPUs / memory of the VM | `backend/vm/controller.py` |
| Experiment ID / rerun / logging | `backend/experiments/experiment_manager.py` |
| Dashboard (terminal) | `backend/cli/` |

**Likely evaluator questions and short answers:**

- *"Where is Xen in this?"* — We use Xen and LibVMI as installed,
  pre-built components in the full design (not built from source). Since
  running a real Xen hypervisor needs dedicated hardware, this build
  swaps in `resource_monitor.py` (real local CPU/RAM sampling) and
  `vmi_adapter.py` (a simulated adapter with the same interface a real
  LibVMI adapter would have) so the rest of the pipeline is fully
  runnable and demonstrable.
- *"Why threads instead of real vCPUs?"* — By default nothing touches a real VM. `executor.py`'s
  `filter_with_threads()` splits the filter step across N Python
  threads to simulate "more workers", so rerunning with more threads and
  comparing CPU usage mirrors the report's "change a resource, rerun,
  compare" loop. With `vm setup` the same loop can resize a real VM (section 4).
- *"How does the deadlock detection work?"* — `transactions.py` keeps a
  wait-for graph (a dict of transaction → transactions it's waiting on)
  and does a depth-first search for a cycle. A cycle means a deadlock.
- *"Are the recommendations real or just text?"* — Each `[apply]` recommendation changes a
  real engine setting. `apply` reruns the query before and after, compares median CPU time,
  heap peak and latency, and checks the result data is identical.
- *"How is the bottleneck decided?"* — `classifier.py` is plain
  if/elif rules over peak CPU %, peak memory, and latency — no machine
  learning, deliberately simple and explainable.

---

## 8. Troubleshooting

- **"No tables loaded"** → type `generate` in the console, or run
  `python backend/data/sample_data_generator.py` first.
- **Colours look odd** → set the environment variable `NO_COLOR=1`.
- **CPU numbers look different on your machine** → the thresholds used
  for classification live at the top of `backend/analysis/classifier.py`
  as plain constants — adjust them if needed.
=======
# Hypervision
>>>>>>> 1fd2a370e0cfdd5f0357cae459eb7cb66652fee0
