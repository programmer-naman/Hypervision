"""
controller.py
-------------
Changes the number of vCPUs and the amount of memory of a REAL virtual
machine, so a recommendation like "give the VM more CPUs" can actually be
carried out and the query rerun to see the difference.

Three backends (pick one with `vm setup ...` in the console):

    dryrun   prints the command it WOULD run, changes nothing (safe demo mode)
    xen      uses the Xen tool `xl`        (xl vcpu-set / xl mem-set / xl list)
    kvm      uses libvirt's tool `virsh`   (virsh setvcpus / setmem / dominfo)

WHERE EACH PART RUNS (this is the important idea)
    * HyperVision runs INSIDE the guest VM, because that is where the query
      runs and where CPU/RAM are measured.
    * The Xen / KVM commands must run on the HOST (dom0 or the KVM server).
    * So when HyperVision is inside the guest, give the host's address:
          --vm-host root@192.168.1.10
      and every command is sent to the host with `ssh` (key-based login
      needed, no password prompts).
    * If you run HyperVision directly on the host, leave --vm-host empty.

Nothing here uses a shell, so a VM name can never run extra commands.
"""

import re
import shlex
import subprocess
import time
from datetime import datetime, timezone

import psutil

BACKENDS = ("dryrun", "xen", "kvm")
MIN_MEMORY_MB = 256

_NAME_OK = re.compile(r"^[A-Za-z0-9_.-]+$")
_HOST_OK = re.compile(r"^[A-Za-z0-9_.@:-]+$")


class VMError(Exception):
    """Something went wrong while talking to the hypervisor."""


# ---------------------------------------------------------------- parsing helpers

def parse_xl_list(text, vm_name):
    """
    `xl list` prints a table like:
        Name        ID   Mem VCPUs   State   Time(s)
        Domain-0     0  2048     2   r-----    123.4
        guest1       3  1024     1   -b----     12.1
    Returns {"vcpus": 1, "memory_mb": 1024} for `vm_name`, or None.
    """
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] == vm_name:
            return {"vcpus": int(parts[3]), "memory_mb": int(parts[2])}
    return None


def parse_virsh_dominfo(text):
    """
    `virsh dominfo` prints lines like "CPU(s):  2" and "Used memory:  2097152 KiB".
    """
    vcpus = memory_kib = None
    for line in text.splitlines():
        if line.startswith("CPU(s):"):
            vcpus = int(line.split(":")[1].split()[0])
        elif line.startswith("Used memory:"):
            memory_kib = int(line.split(":")[1].split()[0])
    if vcpus is None or memory_kib is None:
        return None
    return {"vcpus": vcpus, "memory_mb": memory_kib // 1024}


# ---------------------------------------------------------------- the controller

class VMController:
    def __init__(self, backend="dryrun", vm_name=None, host=None, runner=None, vm_config_path=None):
        if backend not in BACKENDS:
            raise VMError("Unknown backend '%s'. Choose one of: %s" % (backend, ", ".join(BACKENDS)))
        if backend != "dryrun" and not vm_name:
            raise VMError("A VM name is required for the %s backend." % backend)
        vm_name = vm_name or "demo-vm"
        if not _NAME_OK.match(vm_name):
            raise VMError("VM name may only contain letters, digits, '.', '_' and '-'.")
        if host and not _HOST_OK.match(host):
            raise VMError("Host looks wrong. Use the form user@address, e.g. root@192.168.1.10")

        self.backend = backend
        self.vm_name = vm_name
        self.host = host
        self.vm_config_path = vm_config_path
        self.simulated_status = "running"
        self.runner = runner or self._run_for_real   # runner(args) -> (returncode, stdout, stderr)
        self.original_state = None                   # VM size before our first change
        self.simulated = None                        # pretend VM size used by dry-run mode
        self.history = []                            # every command we ran or would run
        self.config_history = []                     # structured log of resource reconfigurations

    # ---- information -----------------------------------------------------------

    @property
    def dry_run(self):
        return self.backend == "dryrun"

    def describe(self):
        where = "via ssh %s" % self.host if self.host else "on this machine"
        return "%s backend, VM '%s', commands run %s" % (self.backend, self.vm_name, where)

    @staticmethod
    def guest_view():
        """What the machine running HyperVision (the guest) sees right now."""
        return {"vcpus": psutil.cpu_count() or 1,
                "memory_mb": int(psutil.virtual_memory().total / (1024 * 1024))}

    def snapshot(self):
        """Return the configured VM's size, keeping unknown values explicit on errors."""
        errors = []
        if self.dry_run:
            view = dict(self.simulated or self.guest_view())
            vm_status = self.simulated_status
        else:
            try:
                view = self.hypervisor_state()
            except VMError as exc:
                # Do not silently label the controller host's CPU/RAM as the VM's.
                view = {"vcpus": None, "memory_mb": None}
                errors.append("VM resource query failed: " + str(exc))
            try:
                vm_status = self.status()
            except VMError as exc:
                vm_status = "unknown"
                errors.append("VM status query failed: " + str(exc))
        return {"enabled": True, "backend": self.backend, "dry_run": self.dry_run,
                "vm_name": self.vm_name, "vcpus": view.get("vcpus"), "memory_mb": view.get("memory_mb"),
                "status": vm_status, "status_error": "; ".join(errors) or None,
                "config_path": self.vm_config_path, "host": self.host,
                "config_history": list(self.config_history[-20:])}

    def configure(self, backend="dryrun", vm_name=None, host=None, vm_config_path=None):
        """Configure the controller. Real backends are explicit and never selected implicitly."""
        replacement = VMController(backend=backend, vm_name=vm_name, host=host,
                                   runner=self.runner, vm_config_path=vm_config_path)
        self.backend = replacement.backend
        self.vm_name = replacement.vm_name
        self.host = replacement.host
        self.vm_config_path = replacement.vm_config_path
        self.original_state = None
        self.simulated = None
        self.simulated_status = "running"
        self.history = []
        return self.snapshot()

    def status(self):
        """Return running/shut-off state for the configured VM."""
        if self.dry_run:
            return self.simulated_status
        if self.backend == "xen":
            ok, out = self._execute(["xl", "list"])
            if not ok:
                raise VMError("Could not read Xen VM status. " + out.strip())
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0] == self.vm_name:
                    return "running"
            return "shut off"
        ok, out = self._execute(["virsh", "domstate", self.vm_name])
        if not ok:
            raise VMError("Could not read VM status. " + out.strip())
        return out.strip().lower() or "unknown"

    def start(self):
        if self.dry_run:
            self.simulated_status = "running"
            return "DRY-RUN: VM start simulated; no VM was started."
        if self.backend == "xen":
            if not self.vm_config_path:
                raise VMError("For Xen start, provide the VM configuration file path on the hypervisor host.")
            args = ["xl", "create", self.vm_config_path]
        else:
            args = ["virsh", "start", self.vm_name]
        ok, out = self._execute(args)
        if not ok:
            raise VMError("Starting VM failed. " + out.strip())
        self.simulated_status = "running"
        return "Start command sent to VM '%s'. %s" % (self.vm_name, out.strip())

    def shutdown(self):
        if self.dry_run:
            self.simulated_status = "shut off"
            return "DRY-RUN: VM shutdown simulated; no VM was shut down."
        args = ["xl", "shutdown", self.vm_name] if self.backend == "xen" else ["virsh", "shutdown", self.vm_name]
        ok, out = self._execute(args)
        if not ok:
            raise VMError("Graceful VM shutdown failed. " + out.strip())
        self.simulated_status = "shutting down"
        return "Graceful shutdown requested for VM '%s'. %s" % (self.vm_name, out.strip())

    def force_stop(self):
        if self.dry_run:
            self.simulated_status = "shut off"
            return "DRY-RUN: force-stop simulated; no VM was stopped."
        args = ["xl", "destroy", self.vm_name] if self.backend == "xen" else ["virsh", "destroy", self.vm_name]
        ok, out = self._execute(args)
        if not ok:
            raise VMError("Force stop failed. " + out.strip())
        self.simulated_status = "shut off"
        return "VM '%s' was force-stopped. Unsaved guest work may be lost." % self.vm_name

    def hypervisor_state(self):
        """Asks the hypervisor for the VM's current size. Returns a dict, or raises VMError."""
        if self.dry_run:
            return dict(self.simulated or self.guest_view())
        if self.backend == "xen":
            ok, out = self._execute(["xl", "list", self.vm_name])
            state = parse_xl_list(out, self.vm_name) if ok else None
        else:
            ok, out = self._execute(["virsh", "dominfo", self.vm_name])
            state = parse_virsh_dominfo(out) if ok else None
        if not state:
            raise VMError("Could not read the state of VM '%s'.\n%s" % (self.vm_name, out.strip()))
        return state

    # ---- changing --------------------------------------------------------------

    def set_vcpus(self, count):
        count = int(count)
        if count < 1:
            raise VMError("vCPU count must be at least 1.")
        old_vcpus = (self.simulated or self.guest_view()).get("vcpus") if self.dry_run else None
        self._remember_original()
        if self.backend == "xen":
            args = ["xl", "vcpu-set", self.vm_name, str(count)]
        elif self.backend == "kvm":
            args = ["virsh", "setvcpus", self.vm_name, str(count), "--live"]
        else:
            args = ["xl", "vcpu-set", self.vm_name, str(count)]      # shown only, never run
        self._change(args, "vCPUs", hint=(
            "A running VM cannot have more vCPUs than its 'maxvcpus' (Xen) / 'maximum' "
            "(libvirt) setting. Raise that in the VM's config and restart the VM once."))
        if self.dry_run:
            self.simulated = dict(self.simulated or self.guest_view(), vcpus=count)
        msg = "VM vCPUs set to %d" % count
        self.config_history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "set_vcpus",
            "old_vcpus": old_vcpus,
            "new_vcpus": count,
            "backend": self.backend,
            "dry_run": self.dry_run,
            "verified": True,
            "message": msg,
        })
        return msg

    def set_memory_mb(self, megabytes):
        megabytes = int(megabytes)
        if megabytes < MIN_MEMORY_MB:
            raise VMError("Memory must be at least %d MB." % MIN_MEMORY_MB)
        old_mem = (self.simulated or self.guest_view()).get("memory_mb") if self.dry_run else None
        self._remember_original()
        if self.backend == "kvm":
            args = ["virsh", "setmem", self.vm_name, str(megabytes * 1024), "--live"]   # KiB
        else:
            args = ["xl", "mem-set", self.vm_name, "%dm" % megabytes]
        self._change(args, "memory", hint=(
            "A running VM cannot grow beyond its 'maxmem' (Xen) / 'maximum memory' (libvirt) "
            "setting. Raise that in the VM's config and restart the VM once."))
        if self.dry_run:
            self.simulated = dict(self.simulated or self.guest_view(), memory_mb=megabytes)
        msg = "VM memory set to %d MB" % megabytes
        self.config_history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "set_memory",
            "old_memory_mb": old_mem,
            "new_memory_mb": megabytes,
            "backend": self.backend,
            "dry_run": self.dry_run,
            "verified": True,
            "message": msg,
        })
        return msg

    def apply_resources(self, vcpus=None, memory_mb=None):
        """Changes both vCPU and RAM, verifies the new state, and returns summary."""
        snap = self.snapshot()
        old_vcpus = snap.get("vcpus")
        old_mem = snap.get("memory_mb")
        msgs = []
        if vcpus is not None:
            msgs.append(self.set_vcpus(int(vcpus)))
        if memory_mb is not None:
            msgs.append(self.set_memory_mb(int(memory_mb)))
        verification = self.verify_configuration()
        return {
            "message": "; ".join(msgs) if msgs else "No resource changes requested.",
            "old_vcpus": old_vcpus,
            "new_vcpus": self.snapshot().get("vcpus"),
            "old_memory_mb": old_mem,
            "new_memory_mb": self.snapshot().get("memory_mb"),
            "verification": verification,
            "history": list(self.config_history[-20:]),
        }

    def verify_configuration(self):
        """Verifies that the VM resource state matches the expected configuration."""
        snap = self.snapshot()
        if self.dry_run:
            return {
                "verified": True,
                "backend": "dryrun",
                "source": "Simulated (dry-run)",
                "vcpus": snap.get("vcpus"),
                "memory_mb": snap.get("memory_mb"),
                "status": snap.get("status"),
                "note": "Configuration verified in safe simulation mode."
            }
        else:
            try:
                state = self.hypervisor_state()
                return {
                    "verified": True,
                    "backend": self.backend,
                    "source": f"Real {self.backend.upper()}",
                    "vcpus": state.get("vcpus"),
                    "memory_mb": state.get("memory_mb"),
                    "status": self.status(),
                    "note": f"Configuration verified directly with {self.backend.upper()} hypervisor."
                }
            except Exception as exc:
                return {
                    "verified": False,
                    "backend": self.backend,
                    "source": f"Real {self.backend.upper()}",
                    "vcpus": snap.get("vcpus"),
                    "memory_mb": snap.get("memory_mb"),
                    "status": "error",
                    "note": f"Hypervisor verification failed: {exc}"
                }

    def restore(self):
        """Puts the VM back to the size it had before our first change."""
        if not self.original_state:
            raise VMError("Nothing to restore: the VM has not been changed in this session.")
        messages = [self.set_vcpus(self.original_state["vcpus"]),
                    self.set_memory_mb(self.original_state["memory_mb"])]
        self.original_state = None
        return "; ".join(messages)

    def wait_for_guest(self, vcpus=None, memory_mb=None, timeout=30, interval=1.0):
        """
        After a change, waits until the guest reports the new size (a Linux guest
        needs a moment to notice hot-added CPUs or ballooned memory).
        Returns (ok, message).
        """
        if self.dry_run:
            return True, "dry-run: no real change was made, so the guest sees no difference"
        deadline = time.time() + timeout
        while True:
            view = self.guest_view()
            cpu_ok = vcpus is None or view["vcpus"] == vcpus
            mem_ok = memory_mb is None or abs(view["memory_mb"] - memory_mb) <= max(64, memory_mb * 0.08)
            if cpu_ok and mem_ok:
                return True, "guest now sees %d CPUs and %d MB RAM" % (view["vcpus"], view["memory_mb"])
            if time.time() >= deadline:
                break
            time.sleep(interval)
        return False, ("guest still sees %d CPUs and %d MB RAM after %ds. If CPUs were added, "
                       "bring them online inside the guest: "
                       "echo 1 | sudo tee /sys/devices/system/cpu/cpu*/online"
                       % (view["vcpus"], view["memory_mb"], timeout))

    # ---- internals -------------------------------------------------------------

    def _remember_original(self):
        if self.original_state is None:
            try:
                self.original_state = self.hypervisor_state()
            except VMError:
                self.original_state = self.guest_view()

    def _change(self, args, what, hint):
        ok, out = self._execute(args)
        if not ok:
            raise VMError("Changing VM %s failed.\n%s\n%s" % (what, out.strip(), hint))

    def _full_command(self, args):
        """The command exactly as it will run (wrapped in ssh when a host is set)."""
        if self.host:
            remote = " ".join(shlex.quote(a) for a in args)
            return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", self.host, remote]
        return list(args)

    def _execute(self, args):
        """Runs (or, in dry-run mode, only prints) one command. Returns (ok, output)."""
        full = self._full_command(args)
        shown = " ".join(shlex.quote(a) for a in full)
        self.history.append(shown)
        if self.dry_run:
            print("  [dry-run] would run: " + shown)
            return True, ""
        print("  running: " + shown)
        returncode, out, err = self.runner(full)
        return returncode == 0, (out + err)

    @staticmethod
    def _run_for_real(full_command):
        import shutil
        cmd = list(full_command)
        prog = shutil.which(cmd[0])
        if prog:
            cmd[0] = prog
        try:
            done = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except FileNotFoundError:
            return 127, "", "Command not found: %s (is it installed and on PATH?)" % full_command[0]
        except subprocess.TimeoutExpired:
            return 124, "", "Command timed out after 60 seconds."
        return done.returncode, done.stdout, done.stderr
