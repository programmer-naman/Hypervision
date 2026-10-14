"""
console.py
----------
The interactive HyperVision terminal console. It replaces the old Flask
server + web page, using the same backend modules.

Typical session:
    hypervision> example 1          run a query (experiment)
    hypervision> apply 1 2          apply recommendations 1 and 2, rerun, compare
    hypervision> undo               go back to the previous settings
"""

import os
import sys

from mini_dbms.storage import Storage
from mini_dbms.parser import ParseError
from mini_dbms.transactions import LockManager
from mini_dbms.settings import (ExecutionSettings, TOGGLE_NAMES, parse_setting_value)
from experiments.experiment_manager import ExperimentManager, DEFAULT_COMPARE_RUNS
from vm.controller import VMController, VMError, BACKENDS

from cli.ui import bold, dim, red, green, yellow, cyan, paint, heading, print_table
from cli.render import (render, render_recommendations, render_comparison,
                        settings_of)

try:                      # arrow-key history / line editing where available
    import readline       # noqa: F401
except ImportError:
    pass

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.abspath(os.path.join(BACKEND_DIR, ".."))
DATA_DIR = os.path.join(PROJECT_ROOT, "sample_data")
LOG_PATH = os.path.join(PROJECT_ROOT, "logs", "experiments.json")

EXAMPLE_QUERIES = [
    ("Avg salary by dept",
     "SELECT department, AVG(salary) FROM sales WHERE salary > 50000 GROUP BY department"),
    ("Count by region",
     "SELECT region, COUNT(*) FROM sales GROUP BY region"),
    ("Join demo",
     "SELECT sales.department, orders.amount FROM sales JOIN orders "
     "ON sales.employee_id = orders.employee_id WHERE orders.amount > 4000"),
    ("Join, big table on the right",
     "SELECT orders.amount, sales.department FROM orders JOIN sales "
     "ON orders.employee_id = sales.employee_id WHERE sales.salary > 90000"),
]

DEFAULT_SQL = EXAMPLE_QUERIES[0][1]


class HyperVisionCLI:
    def __init__(self, data_dir=DATA_DIR, log_path=LOG_PATH):
        self.data_dir = data_dir
        self.storage = Storage(data_dir)
        self.manager = ExperimentManager(self.storage, log_path)
        self.lock_manager = LockManager()
        self.settings = ExecutionSettings()      # settings used by new queries
        self.previous_settings = None            # for `undo`
        self.last_record = None                  # newest experiment shown
        self.compare_runs = DEFAULT_COMPARE_RUNS
        self.vm = None                           # VMController, after `vm setup`
        self.manager.vm_info_provider = lambda: self.vm.snapshot() if self.vm else None

    # ---- data -------------------------------------------------------------

    def generate_data(self):
        from data.sample_data_generator import (
            generate_sales_csv, generate_orders_csv,
            SALES_ROW_COUNT, ORDERS_ROW_COUNT)
        print("Generating sample data (this takes a few seconds)...")
        generate_sales_csv(os.path.join(self.data_dir, "sales.csv"), SALES_ROW_COUNT)
        generate_orders_csv(os.path.join(self.data_dir, "orders.csv"), ORDERS_ROW_COUNT, SALES_ROW_COUNT)
        self.storage.load_all_csv_files()
        print(green("Sample data generated in: %s" % self.data_dir))
        print("  sales.csv  (%d rows)" % SALES_ROW_COUNT)
        print("  orders.csv (%d rows)" % ORDERS_ROW_COUNT)

    def show_tables(self):
        if not self.storage.tables:
            print(red("No tables loaded. Type `generate` to create the sample data "
                      "(or run: python backend/data/sample_data_generator.py)"))
            return
        heading("Tables")
        for name, table in self.storage.tables.items():
            print("  %s  (%d rows)" % (bold(name), len(table.rows)))
            print(dim("    columns: " + ", ".join(table.columns)))

    # ---- experiments --------------------------------------------------------

    def run_sql(self, sql_text):
        sql_text = sql_text.strip()
        if sql_text == "":
            print(red("sql is required"))
            return
        print(dim("Running experiment..."))
        try:
            record = self.manager.run_query(sql_text, settings=self.settings)
        except (ParseError, ValueError) as e:
            print(red("Error: %s" % e))
            return
        except Exception as e:
            print(red("Error: Execution failed: %s" % e))
            return
        self.last_record = record
        print(green("%s complete." % record["experiment_id"]))
        render(record)

    def rerun(self, args):
        experiment_id = self.last_record["experiment_id"] if self.last_record else None
        thread_count = self.settings.thread_count
        for a in args:
            if a.isdigit():
                thread_count = max(1, int(a))
            else:
                experiment_id = a.upper()
        if not experiment_id:
            print(red("Nothing to rerun yet. Run a query first."))
            return
        print(dim("Rerunning %s with %d threads..." % (experiment_id, thread_count)))
        try:
            record = self.manager.rerun(experiment_id, thread_count)
        except ValueError as e:
            print(red("Error: %s" % e))
            return
        except Exception as e:
            print(red("Error: Execution failed: %s" % e))
            return
        self.last_record = record
        print(green("%s complete (rerun of %s)." % (record["experiment_id"], experiment_id)))
        render(record)

    def show_history(self):
        experiments = self.manager.list_experiments()[:25]
        heading("Experiment History")
        if not experiments:
            print(dim("  No experiments yet."))
            return
        rows = []
        for e in experiments:
            s = settings_of(e)
            opts = [n.split("_")[0] for n in TOGGLE_NAMES if getattr(s, n)]
            rows.append((e["experiment_id"], " ".join(e["sql"].split()), s.thread_count,
                         ",".join(opts) or "-",
                         "%.1f ms" % e["latency_ms"], e["classification"]["classification"],
                         e["row_count"]))
        print_table(["ID", "SQL", "Threads", "Options", "Latency", "Classification", "Rows"],
                    rows, max_width=60)
        print(dim("  Use `view <ID>` to open one, `compare <ID> <ID>` to compare two."))

    def view(self, args):
        if not args:
            print(red("Usage: view <experiment id>   (e.g. view EXP-1001)"))
            return
        exp = self.manager.get_experiment(args[0].upper())
        if not exp:
            print(red("Error: not found"))
            return
        self.last_record = exp
        print("SQL: " + exp["sql"])
        render(exp)

    # ---- recommendations: apply / compare / settings -----------------------------

    def show_recs(self):
        if not self.last_record:
            print(red("Run a query first."))
            return
        render_recommendations(self.last_record)

    def apply(self, args):
        """apply 1 2 | apply 1,3 | apply all  -> change settings, rerun, compare."""
        record = self.last_record
        if not record:
            print(red("Run a query first, then apply one of its recommendations."))
            return
        actions = record.get("recommendations") or []
        if not actions:
            print(red("This experiment has no recommendations to apply."))
            return

        text = " ".join(args).replace(",", " ").split()
        if not text:
            print(red("Usage: apply <numbers>   e.g. apply 1 2   or   apply all"))
            return
        if text == ["all"]:
            chosen = [a for a in actions if a["kind"] == "setting" and not a.get("experimental")]
        else:
            chosen, by_rank = [], {a["rank"]: a for a in actions}
            for t in text:
                if not t.isdigit() or int(t) not in by_rank:
                    print(red("No recommendation numbered '%s'." % t))
                    return
                chosen.append(by_rank[int(t)])

        manual = [a for a in chosen if a["kind"] not in ("setting", "vm")]
        auto = [a for a in chosen if a["kind"] in ("setting", "vm")]
        for a in manual:
            print(yellow("[%d] %s is a change to your SQL, so it cannot be applied automatically." % (a["rank"], a["title"])))
            print(dim("    Edit the query, run it, then use: compare %s <new id>" % record["experiment_id"]))
        if not auto:
            if not manual:
                print(red("Nothing to apply."))
            return

        engine_changes, vm_changes = {}, {}
        for a in auto:
            for name, value in a["changes"].items():
                (vm_changes if name.startswith("vm_") else engine_changes)[name] = value
        if vm_changes and self.vm is None:
            print(red("That recommendation resizes the VM, but no VM is set up. Use `vm setup` first."))
            return
        base = settings_of(record)
        new_settings = base.with_changes(engine_changes)
        environment_change = self._make_vm_change(vm_changes) if vm_changes else None
        if vm_changes and self.vm.dry_run:
            print(yellow("DRY RUN: the VM commands are only printed, so expect no real difference."))

        titles = [a["title"] for a in auto]
        print(dim("Applying: %s" % "; ".join(titles)))
        print(dim("Running %s experiment(s) per side plus a memory probe (a few seconds)..." % self.compare_runs))
        try:
            out = self.manager.compare_settings(
                record["experiment_id"], new_settings, runs=self.compare_runs, applied=titles,
                environment_change=environment_change,
                simulated_only=bool(vm_changes and self.vm.dry_run and not base.diff(new_settings)))
        except VMError as e:
            print(red("VM error: %s" % e))
            print(dim("  Nothing was compared. `vm status` shows the current VM size."))
            return
        except Exception as e:
            print(red("Error: Execution failed: %s" % e))
            return

        candidate = out["candidate"]
        self.previous_settings = self.settings
        self.settings = new_settings
        self.last_record = candidate
        print(green("%s saved (settings applied)." % candidate["experiment_id"]))
        render_comparison(out["comparison"], titles)
        print(dim("\n  New queries now run with: " + self.settings.describe()))
        print(dim("  `undo` restores the previous settings, `reset` restores the defaults."))
        if vm_changes:
            print(dim("  `vm restore` puts the VM back to its original size."))
        if candidate.get("recommendations"):
            render_recommendations(candidate)

    def compare(self, args):
        if len(args) != 2:
            print(red("Usage: compare <before id> <after id>   e.g. compare EXP-1001 EXP-1002"))
            return
        try:
            comp = self.manager.compare_saved(args[0].upper(), args[1].upper())
        except ValueError as e:
            print(red("Error: %s" % e))
            return
        render_comparison(comp)
        print(dim("  (Saved experiments carry one run each, and heap numbers only exist for runs "
                  "made by `apply`; use `apply` for a cleaner A/B test.)"))

    def show_settings(self):
        heading("Settings used for new queries")
        print("  threads (thread_count)      %s" % self.settings.thread_count)
        for name in TOGGLE_NAMES:
            on = getattr(self.settings, name)
            print("  %-27s %s" % (name, green("on") if on else dim("off")))
        print(dim("\n  Change with:  set <name> <on|off|N>    e.g. set pushdown_filter on"))
        print(dim("  Compare runs per side: %d  (change with `runs N`)" % self.compare_runs))

    def set_setting(self, args):
        if len(args) != 2:
            print(red("Usage: set <name> <on|off|N>   (names: thread_count, %s)" % ", ".join(TOGGLE_NAMES)))
            return
        name = args[0].lower()
        if name == "threads":
            name = "thread_count"
        try:
            value = parse_setting_value(name, args[1])
            self.previous_settings, self.settings = self.settings, self.settings.with_changes({name: value})
        except ValueError as e:
            print(red("Error: %s" % e))
            return
        print(green("%s = %s" % (name, args[1].lower())))

    def set_threads(self, args):
        if args and args[0].isdigit() and int(args[0]) >= 1:
            self.set_setting(["thread_count", args[0]])
        else:
            print("Current threads: %d  (usage: threads N, e.g. 1, 2, 4, 8)" % self.settings.thread_count)

    def undo(self):
        if self.previous_settings is None:
            print(red("Nothing to undo."))
            return
        self.settings, self.previous_settings = self.previous_settings, self.settings
        print(green("Settings restored: " + self.settings.describe()))
        print(dim("  (`undo` again switches back.)"))

    def reset(self):
        self.previous_settings, self.settings = self.settings, ExecutionSettings()
        print(green("Settings reset to defaults: " + self.settings.describe()))

    def set_runs(self, args):
        if args and args[0].isdigit() and 1 <= int(args[0]) <= 15:
            self.compare_runs = int(args[0])
            print(green("apply/compare will use %d run(s) per side." % self.compare_runs))
        else:
            print("Runs per side: %d  (usage: runs N, between 1 and 15)" % self.compare_runs)

    # ---- virtual machine control ----------------------------------------------

    def _make_vm_change(self, vm_changes):
        """Returns a function that resizes the VM, waits for the guest, and describes what happened."""
        def change():
            notes = []
            if "vm_vcpus" in vm_changes:
                notes.append(self.vm.set_vcpus(vm_changes["vm_vcpus"]))
            if "vm_memory_mb" in vm_changes:
                notes.append(self.vm.set_memory_mb(vm_changes["vm_memory_mb"]))
            print(dim("  waiting for the guest to notice the change..."))
            ok, message = self.vm.wait_for_guest(vcpus=vm_changes.get("vm_vcpus"),
                                                 memory_mb=vm_changes.get("vm_memory_mb"))
            notes.append(message)
            if not ok:
                print(yellow("  Warning: " + message))
            return "; ".join(notes)
        return change

    def configure_vm(self, backend, vm_name=None, host=None):
        self.vm = VMController(backend, vm_name, host)
        print(green("VM control ready: " + self.vm.describe()))
        if self.vm.dry_run:
            print(dim("  Dry-run mode: commands are printed, nothing on a real VM changes."))
        else:
            print(dim("  Real mode: recommendations tagged [apply · VM] will resize the VM."))

    def vm_command(self, args):
        """vm | vm status | vm setup <backend> [name] [user@host] | vm vcpus N | vm memory MB | vm restore | vm off"""
        sub = args[0].lower() if args else "status"
        try:
            if sub == "setup":
                if len(args) < 2:
                    print(red("Usage: vm setup <%s> [vm name] [user@host]" % "|".join(BACKENDS)))
                    return
                self.configure_vm(args[1].lower(), args[2] if len(args) > 2 else None,
                                  args[3] if len(args) > 3 else None)
            elif sub == "off":
                self.vm = None
                print(green("VM control switched off."))
            elif self.vm is None:
                print(red("No VM is set up yet."))
                print("  Try the safe demo:  vm setup dryrun")
                print("  Xen:   vm setup xen <vm name> [user@dom0-address]")
                print("  KVM:   vm setup kvm <vm name> [user@host-address]")
            elif sub == "status":
                self.show_vm_status()
            elif sub in ("vcpus", "vcpu", "cpus"):
                self._need_number(args, "vm vcpus N")
                print(green(self.vm.set_vcpus(args[1])))
                self._report_guest(vcpus=int(args[1]))
            elif sub in ("memory", "mem", "ram"):
                self._need_number(args, "vm memory MB")
                print(green(self.vm.set_memory_mb(args[1])))
                self._report_guest(memory_mb=int(args[1]))
            elif sub == "restore":
                print(green(self.vm.restore()))
            else:
                print(red("Unknown vm command '%s'. Try: status, setup, vcpus N, memory MB, restore, off" % sub))
        except VMError as e:
            print(red("VM error: %s" % e))

    @staticmethod
    def _need_number(args, usage):
        if len(args) < 2 or not args[1].isdigit():
            raise VMError("Usage: " + usage)

    def _report_guest(self, vcpus=None, memory_mb=None):
        ok, message = self.vm.wait_for_guest(vcpus=vcpus, memory_mb=memory_mb)
        print((dim if ok else yellow)("  " + message))

    def show_vm_status(self):
        heading("Virtual machine")
        print("  " + self.vm.describe())
        try:
            state = self.vm.hypervisor_state()
            print("  Hypervisor reports : %d vCPUs, %d MB%s" % (
                state["vcpus"], state["memory_mb"], "  (simulated)" if self.vm.dry_run else ""))
        except VMError as e:
            print(red("  Hypervisor reports : unavailable (%s)" % str(e).splitlines()[0]))
        guest = self.vm.guest_view()
        print("  This machine sees  : %d CPUs, %d MB RAM" % (guest["vcpus"], guest["memory_mb"]))
        if self.vm.original_state:
            o = self.vm.original_state
            print(dim("  Original size      : %d vCPUs, %d MB  (`vm restore` goes back)" % (o["vcpus"], o["memory_mb"])))

    # ---- deadlock demo --------------------------------------------------------

    def txn_demo(self):
        lm = self.lock_manager
        txn1, txn2 = lm.new_txn(), lm.new_txn()
        steps = []

        def try_lock(txn_id, resource, mode):
            granted, reason = lm.acquire(txn_id, resource, mode)
            steps.append((txn_id, resource, mode, granted, reason))

        try_lock(txn1, "table:sales", "X")
        try_lock(txn2, "table:orders", "X")
        try_lock(txn2, "table:sales", "X")
        try_lock(txn1, "table:orders", "X")

        lm.release_all(txn1)
        lm.release_all(txn2)

        heading("Concurrency demo (Txn %d vs Txn %d)" % (txn1, txn2))
        for txn, resource, mode, granted, reason in steps:
            if granted:
                result = green("GRANTED")
            elif reason == "deadlock_detected":
                result = red(bold("DEADLOCK DETECTED"))
            else:
                result = paint("BLOCKED", "33")
            print("  Txn %s → %s (%s): %s" % (txn, resource, mode, result))
        print(green("Concurrency demo complete."))

    # ---- misc ----------------------------------------------------------------------

    def show_examples(self):
        heading("Example queries")
        for i, (label, sql) in enumerate(EXAMPLE_QUERIES, 1):
            print("  %d. %s" % (i, bold(label)))
            print("     " + sql)
        print(dim("  Run one with: example 1   (or example 2, 3, 4)"))

    def show_help(self):
        heading("Commands")
        rows = [
            ("<SQL>", "run a query, e.g. SELECT region, COUNT(*) FROM sales GROUP BY region"),
            ("examples / example N", "list / run the example queries"),
            ("recs", "show the recommendations of the latest experiment"),
            ("apply N [N..] | all", "apply recommendation(s), rerun, and compare before/after"),
            ("undo / reset", "restore previous settings / restore defaults"),
            ("vm setup BACKEND [NAME] [HOST]", "connect VM control: dryrun | xen | kvm (HOST = user@address of the hypervisor)"),
            ("vm status | vcpus N | memory MB | restore", "show / resize / restore the virtual machine"),
            ("settings", "show the current tuning settings"),
            ("set NAME VALUE", "change a setting by hand, e.g. set streaming_aggregation on"),
            ("threads N", "shortcut for: set thread_count N"),
            ("compare ID1 ID2", "compare two saved experiments"),
            ("runs N", "runs per side used by apply (default %d)" % DEFAULT_COMPARE_RUNS),
            ("rerun [ID] [N]", "rerun last (or given) experiment with N threads"),
            ("txn", "run the concurrency / deadlock demo"),
            ("history", "show experiment history (newest 25)"),
            ("view ID", "show a saved experiment, e.g. view EXP-1001"),
            ("tables", "show loaded tables"),
            ("generate", "(re)generate the sample CSV data"),
            ("help / quit", "this list / exit"),
        ]
        w = max(len(r[0]) for r in rows)
        for cmd, desc in rows:
            print("  %s  %s" % (bold(cmd.ljust(w)), desc))
        print(dim("  End a line with \\ to continue the SQL on the next line."))
        print(dim("  Supported SQL: SELECT ... FROM t [JOIN u ON ..] [WHERE a AND b] "
                  "[GROUP BY ..] [ORDER BY ..]"))

    def read_input(self):
        line = input(bold(green("hypervision> ")))
        while line.rstrip().endswith("\\"):
            line = line.rstrip()[:-1] + " " + input("        ...> ")
        return line.strip()

    def handle(self, line):
        """Returns False when the user wants to quit."""
        if not line:
            return True
        parts = line.split()
        cmd, args = parts[0].lower(), parts[1:]

        if cmd in ("quit", "exit", "q"):
            return False
        elif cmd in ("help", "?", "h"):
            self.show_help()
        elif cmd == "threads":
            self.set_threads(args)
        elif cmd == "examples":
            self.show_examples()
        elif cmd == "example":
            if args and args[0].isdigit() and 1 <= int(args[0]) <= len(EXAMPLE_QUERIES):
                sql = EXAMPLE_QUERIES[int(args[0]) - 1][1]
                print(dim("SQL: " + sql))
                self.run_sql(sql)
            else:
                self.show_examples()
        elif cmd == "recs":
            self.show_recs()
        elif cmd == "apply":
            self.apply(args)
        elif cmd == "compare":
            self.compare(args)
        elif cmd == "vm":
            self.vm_command(args)
        elif cmd == "settings":
            self.show_settings()
        elif cmd == "set":
            self.set_setting(args)
        elif cmd == "undo":
            self.undo()
        elif cmd == "reset":
            self.reset()
        elif cmd == "runs":
            self.set_runs(args)
        elif cmd == "rerun":
            self.rerun(args)
        elif cmd in ("txn", "demo", "deadlock"):
            self.txn_demo()
        elif cmd == "history":
            self.show_history()
        elif cmd == "view":
            self.view(args)
        elif cmd == "tables":
            self.show_tables()
        elif cmd == "generate":
            self.generate_data()
        else:
            if cmd != "select":
                print(dim("(not a command — treating it as SQL; type `help` for commands)"))
            self.run_sql(line)
        return True

    def loop(self):
        print(bold(cyan("HYPERVISION")) + " — Integrated DBMS + Resource-Aware Experiment Console")
        print(dim("Type SQL to run an experiment, or `help` for commands. Default query:"))
        print(dim("  " + DEFAULT_SQL))

        if not self.storage.tables:
            print(red("\nNo tables loaded."))
            try:
                answer = input("Generate the sample data now? [Y/n] ").strip().lower()
            except EOFError:
                answer = "n"
            if answer in ("", "y", "yes"):
                self.generate_data()
        else:
            self.show_tables()

        while True:
            try:
                line = self.read_input()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            try:
                if not self.handle(line):
                    break
            except KeyboardInterrupt:
                print(red("\nInterrupted."))
        print("Goodbye.")
