"""
vmi_adapter.py
---------------
In the full HyperVision design, this part would use LibVMI to look
"from outside" into a real Xen virtual machine (the guest) and read its
memory / process information, without running any extra code inside the
guest itself.

Setting up an actual Xen hypervisor is not realistic on a personal
laptop, so for this project we simulate that layer with SimulatedVMI: it
reports a fake domain name and a slowly-growing "event count", just so
the rest of the project (correlation, dashboard) has real numbers to
work with and display.

If this project is later connected to a real, installed Xen + LibVMI
setup, only this file would need to change -- everything else just
calls get_vmi_adapter() and does not care how the numbers are produced.
"""

import random


class SimulatedVMI:
    def __init__(self):
        self.domain_name = None
        self.event_count = 0

    def connect(self, domain_name="local-simulated-domU"):
        self.domain_name = domain_name
        self.event_count = 0
        return True

    def read_event_count(self):
        # A real LibVMI adapter would return the actual number of
        # memory-access / event traps captured from the guest VM.
        self.event_count += random.randint(3, 12)
        return self.event_count

    def disconnect(self):
        self.domain_name = None


def get_vmi_adapter():
    """Returns the VMI adapter currently in use by the project."""
    return SimulatedVMI()
