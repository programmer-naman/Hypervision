"""VM controller tests: command building, dry-run, ssh wrapping, failures, recommender, console."""
import contextlib
import io
import os
import stat
import tempfile
import unittest

import _bootstrap  # noqa: F401
from test_engine import make_storage
from vm.controller import VMController, VMError, parse_xl_list, parse_virsh_dominfo
from mini_dbms.settings import ExecutionSettings
from analysis.recommender import recommend_actions
from cli.console import HyperVisionCLI

XL_LIST = """Name                 ID   Mem VCPUs\tState\tTime(s)
Domain-0              0  2048     2     r-----     123.4
guest1                3  1024     1     -b----      12.1
"""
DOMINFO = "Id:  4\nName: g\nCPU(s):         2\nMax memory:     4194304 KiB\nUsed memory:    2097152 KiB\n"


class Recorder:
    """A fake command runner that remembers every command and answers like a hypervisor."""
    def __init__(self, fail=False):
        self.commands, self.fail = [], fail

    def __call__(self, command):
        self.commands.append(command)
        if self.fail:
            return 1, "", "error: boom"
        text = " ".join(command)
        if "xl list" in text:
            return 0, XL_LIST, ""
        if "dominfo" in text:
            return 0, DOMINFO, ""
        return 0, "", ""


class Parsing(unittest.TestCase):
    def test_xl_list(self):
        self.assertEqual(parse_xl_list(XL_LIST, "guest1"), {"vcpus": 1, "memory_mb": 1024})
        self.assertIsNone(parse_xl_list(XL_LIST, "nobody"))

    def test_virsh_dominfo(self):
        self.assertEqual(parse_virsh_dominfo(DOMINFO), {"vcpus": 2, "memory_mb": 2048})
        self.assertIsNone(parse_virsh_dominfo("garbage"))


class Validation(unittest.TestCase):
    def test_bad_input_rejected(self):
        for args in (("nope",), ("xen",), ("xen", "bad name"), ("xen", "g;rm -rf /"),
                     ("xen", "g", "root@h; reboot")):
            with self.assertRaises(VMError):
                VMController(*args)

    def test_numbers_checked(self):
        vm = VMController("dryrun")
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(VMError):
                vm.set_vcpus(0)
            with self.assertRaises(VMError):
                vm.set_memory_mb(10)


class Commands(unittest.TestCase):
    def run_vm(self, backend, host=None, fail=False):
        rec = Recorder(fail)
        vm = VMController(backend, "guest1", host, runner=rec)
        return vm, rec

    def test_xen_commands(self):
        vm, rec = self.run_vm("xen")
        with contextlib.redirect_stdout(io.StringIO()):
            vm.set_vcpus(4)
            vm.set_memory_mb(2048)
        self.assertIn(["xl", "vcpu-set", "guest1", "4"], rec.commands)
        self.assertIn(["xl", "mem-set", "guest1", "2048m"], rec.commands)

    def test_kvm_commands(self):
        vm, rec = self.run_vm("kvm")
        with contextlib.redirect_stdout(io.StringIO()):
            vm.set_vcpus(3)
            vm.set_memory_mb(2048)
        self.assertIn(["virsh", "setvcpus", "guest1", "3", "--live"], rec.commands)
        self.assertIn(["virsh", "setmem", "guest1", str(2048 * 1024), "--live"], rec.commands)

    def test_ssh_wrapping_and_quoting(self):
        vm, rec = self.run_vm("xen", host="root@10.0.0.5")
        with contextlib.redirect_stdout(io.StringIO()):
            vm.set_vcpus(2)
        cmd = [c for c in rec.commands if "vcpu-set" in " ".join(c)][0]
        self.assertEqual(cmd[0], "ssh")
        self.assertIn("BatchMode=yes", cmd)
        self.assertEqual(cmd[-2:], ["root@10.0.0.5", "xl vcpu-set guest1 2"])

    def test_original_state_remembered_once_and_restored(self):
        vm, rec = self.run_vm("xen")
        with contextlib.redirect_stdout(io.StringIO()):
            vm.set_vcpus(4)
            vm.set_vcpus(8)
            self.assertEqual(vm.original_state, {"vcpus": 1, "memory_mb": 1024})
            vm.restore()
        self.assertIn(["xl", "vcpu-set", "guest1", "1"], rec.commands)
        self.assertIn(["xl", "mem-set", "guest1", "1024m"], rec.commands)
        self.assertIsNone(vm.original_state)
        with self.assertRaises(VMError):
            vm.restore()

    def test_failure_raises_with_hint_and_output(self):
        vm, _ = self.run_vm("xen", fail=True)
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(VMError) as ctx:
                vm.set_vcpus(64)
        self.assertIn("boom", str(ctx.exception))
        self.assertIn("maxvcpus", str(ctx.exception))

    def test_hypervisor_state(self):
        vm, _ = self.run_vm("kvm")
        self.assertEqual(vm.hypervisor_state(), {"vcpus": 2, "memory_mb": 2048})


class DryRun(unittest.TestCase):
    def test_never_runs_anything(self):
        calls = []
        vm = VMController("dryrun", "demo", runner=lambda c: calls.append(c) or (0, "", ""))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            vm.set_vcpus(4)
            vm.set_memory_mb(2048)
        self.assertEqual(calls, [])
        self.assertIn("[dry-run] would run: xl vcpu-set demo 4", out.getvalue())
        self.assertEqual(vm.snapshot()["vcpus"], 4)
        self.assertEqual(vm.snapshot()["memory_mb"], 2048)
        self.assertTrue(vm.wait_for_guest(vcpus=4)[0])


class RealCommandPath(unittest.TestCase):
    """Runs the real subprocess code against stand-in `xl` and `ssh` programs."""
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        state = os.path.join(self.dir, "state")
        with open(state, "w") as f:
            f.write("1 1024")
        self.write("xl", '''#!/bin/sh
STATE="%s"
set -- "$@"
if [ "$1" = "list" ]; then
  read V M < "$STATE"
  echo "Name ID Mem VCPUs State Time"
  echo "$2 3 $M $V -b---- 1.0"
elif [ "$1" = "vcpu-set" ]; then
  read V M < "$STATE"; echo "$3 $M" > "$STATE"
elif [ "$1" = "mem-set" ]; then
  read V M < "$STATE"; echo "$V $(echo $3 | tr -d m)" > "$STATE"
fi
''' % state)
        self.write("ssh", '''#!/bin/sh
# last argument is the remote command; run it like a remote shell would
for last; do :; done
echo "$@" >> "%s/ssh.log"
exec sh -c "$last"
''' % self.dir)
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = self.dir + os.pathsep + self.old_path
        self.state = state

    def tearDown(self):
        os.environ["PATH"] = self.old_path

    def write(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)

    def test_xen_through_ssh(self):
        vm = VMController("xen", "guest1", "root@dom0")
        with contextlib.redirect_stdout(io.StringIO()):
            vm.set_vcpus(4)
            vm.set_memory_mb(2048)
            state = vm.hypervisor_state()
        self.assertEqual(state, {"vcpus": 4, "memory_mb": 2048})
        with open(self.state) as f:
            self.assertEqual(f.read().split(), ["4", "2048"])
        with open(os.path.join(self.dir, "ssh.log")) as f:
            self.assertIn("root@dom0", f.read())

    def test_missing_tool_gives_clear_error(self):
        os.environ["PATH"] = tempfile.mkdtemp()      # nothing installed
        vm = VMController("kvm", "guest1")
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(VMError) as ctx:
                vm.set_vcpus(2)
        self.assertIn("not found", str(ctx.exception))


class RecommenderVmActions(unittest.TestCase):
    def ctx(self, vcpus=2, memory=2048):
        return {"settings": ExecutionSettings(), "latency_ms": 1, "operator_stats": [],
                "has_where": False, "has_join": False, "has_group": False, "has_aggregate": False,
                "has_order": False, "table_rows": {"t": 10}, "left_table": "t", "right_table": None,
                "pushable_conditions": 0, "where_count": 0,
                "vm": {"enabled": True, "vm_name": "g", "vcpus": vcpus, "memory_mb": memory}}

    def vm_actions(self, classification, **kw):
        return [a for a in recommend_actions(classification, self.ctx(**kw)) if a["kind"] == "vm"]

    def test_cpu_bound_suggests_doubling_vcpus_as_experiment(self):
        (a,) = self.vm_actions("CPU-bound")
        self.assertEqual(a["changes"], {"vm_vcpus": 4})
        self.assertTrue(a["experimental"])

    def test_memory_pressure_suggests_more_memory_first(self):
        actions = recommend_actions("Memory pressure", self.ctx())
        self.assertEqual(actions[0]["id"], "vm_more_memory")
        self.assertEqual(actions[0]["changes"], {"vm_memory_mb": 3072})

    def test_saturation_suggests_fewer_vcpus_only_if_more_than_one(self):
        (a,) = self.vm_actions("Parallel/resource saturation", vcpus=8)
        self.assertEqual(a["changes"], {"vm_vcpus": 4})
        self.assertEqual(self.vm_actions("Parallel/resource saturation", vcpus=1), [])

    def test_no_vm_actions_without_a_vm(self):
        c = self.ctx()
        c["vm"] = None
        self.assertEqual([a for a in recommend_actions("CPU-bound", c) if a["kind"] == "vm"], [])

    def test_vcpu_cap(self):
        self.assertEqual(self.vm_actions("CPU-bound", vcpus=16), [])


GROUP_SQL = "SELECT department, AVG(salary) FROM sales GROUP BY department"


class ConsoleVm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = make_storage().data_dir

    def cli(self):
        return HyperVisionCLI(data_dir=self.folder,
                              log_path=os.path.join(tempfile.mkdtemp(), "logs", "e.json"))

    def lines(self, cli, *cmds):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            for c in cmds:
                cli.handle(c)
        return out.getvalue()

    def vm_rank(self, cli):
        """Which bottleneck is detected depends on live CPU readings, so give the test a fixed VM action."""
        cli.last_record["recommendations"] = [
            a for a in cli.last_record["recommendations"] if a["kind"] != "vm"]
        cli.last_record["recommendations"].append({
            "id": "vm_more_vcpus", "kind": "vm", "title": "Give the VM more vCPUs: 1 -> 2",
            "detail": "x", "reduces": ["Latency"], "changes": {"vm_vcpus": 2},
            "experimental": True, "tag": "adds CPU capacity", "rank": 99})
        return 99

    def test_no_vm_means_no_vm_recommendations(self):
        cli = self.cli()
        self.lines(cli, GROUP_SQL)
        self.assertFalse([a for a in cli.last_record["recommendations"] if a["kind"] == "vm"])
        self.vm_rank(cli)
        self.assertIn("no VM is set up", self.lines(cli, "apply 99"))
        self.assertIn("No VM is set up", self.lines(cli, "vm status"))

    def test_setup_status_resize_restore_in_dryrun(self):
        cli = self.cli()
        text = self.lines(cli, "vm setup dryrun demo", "vm vcpus 6", "vm memory 4096", "vm status", "vm restore")
        self.assertIn("would run: xl vcpu-set demo 6", text)
        self.assertIn("would run: xl mem-set demo 4096m", text)
        self.assertIn("Hypervisor reports : 6 vCPUs, 4096 MB", text)

    def test_apply_vm_recommendation_in_dryrun(self):
        cli = self.cli()
        cli.compare_runs = 1
        self.lines(cli, "vm setup dryrun demo", GROUP_SQL)
        text = self.lines(cli, "apply %d" % self.vm_rank(cli))
        self.assertIn("DRY RUN", text)
        self.assertIn("VM change:", text)
        self.assertIn("measurement noise", text)
        self.assertNotIn("the change helped", text)
        self.assertNotIn("made things worse", text)
        self.assertIn("Comparison: EXP-1001", text)
        self.assertEqual(len(cli.manager.list_experiments()), 2)

    def test_apply_vm_recommendation_really_calls_the_hypervisor(self):
        cli = self.cli()
        cli.compare_runs = 1
        rec = Recorder()
        cli.vm = VMController("xen", "guest1", runner=rec)
        cli.vm.wait_for_guest = lambda **kw: (True, "guest now sees the change")   # no real guest here
        self.lines(cli, GROUP_SQL)
        self.lines(cli, "apply %d" % self.vm_rank(cli))
        self.assertTrue(any(c[:3] == ["xl", "vcpu-set", "guest1"] for c in rec.commands))

    def test_apply_all_never_resizes_the_vm(self):
        cli = self.cli()
        cli.compare_runs = 1
        rec = Recorder()
        cli.vm = VMController("xen", "guest1", runner=rec)
        self.lines(cli, GROUP_SQL, "apply all")
        self.assertFalse(any("vcpu-set" in " ".join(c) or "mem-set" in " ".join(c) for c in rec.commands))

    def test_hypervisor_failure_is_reported_not_crashing(self):
        cli = self.cli()
        cli.compare_runs = 1
        cli.vm = VMController("xen", "guest1", runner=Recorder(fail=True))
        self.lines(cli, GROUP_SQL)
        text = self.lines(cli, "apply %d" % self.vm_rank(cli))
        self.assertIn("VM error", text)
        self.assertEqual(len(cli.manager.list_experiments()), 1)     # nothing saved

    def test_vm_commands_validate_input(self):
        cli = self.cli()
        self.assertIn("Usage: vm setup", self.lines(cli, "vm setup"))
        self.assertIn("VM error", self.lines(cli, "vm setup xen"))
        self.lines(cli, "vm setup dryrun")
        self.assertIn("Usage: vm vcpus N", self.lines(cli, "vm vcpus abc"))
        self.assertIn("Unknown vm command", self.lines(cli, "vm explode"))


if __name__ == "__main__":
    unittest.main()
