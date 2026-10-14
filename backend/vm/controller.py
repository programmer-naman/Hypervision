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
    def __init__(self, backend="dryrun", vm_name=None, host=None, runner=None):
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
        self.runner = runner or self._run_for_real   # runner(args) -> (returncode, stdout, stderr)
        self.original_state = None                   # VM size before our first change
        self.simulated = None                        # pretend VM size used by dry-run mode
        self.history = []                            # every command we ran or would run

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
        """Small dict handed to the recommender on every query (cheap, purely local)."""
        view = self.guest_view()
        if self.dry_run and self.simulated:
            view = dict(self.simulated)
        return {"enabled": True, "backend": self.backend, "dry_run": self.dry_run,
                "vm_name": self.vm_name, "vcpus": view["vcpus"], "memory_mb": view["memory_mb"]}

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
        return "VM vCPUs set to %d" % count

    def set_memory_mb(self, megabytes):
        megabytes = int(megabytes)
        if megabytes < MIN_MEMORY_MB:
            raise VMError("Memory must be at least %d MB." % MIN_MEMORY_MB)
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
        return "VM memory set to %d MB" % megabytes

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
        try:
            done = subprocess.run(full_command, capture_output=True, text=True, timeout=60)
        except FileNotFoundError:
            return 127, "", "Command not found: %s (is it installed and on PATH?)" % full_command[0]
        except subprocess.TimeoutExpired:
            return 124, "", "Command timed out after 60 seconds."
        return done.returncode, done.stdout, done.stderr
