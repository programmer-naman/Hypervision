"""
resource_monitor.py
--------------------
While a query runs, this class samples the host machine's CPU and
memory usage every `interval` seconds, using the `psutil` library.

In the full project design (report), these numbers would come from Xen
observing the guest VM's vCPU and memory usage. Since we cannot run a
real Xen hypervisor on a laptop, we sample the *local machine* instead
-- this is our practical stand-in for that layer, and is good enough to
demonstrate the "observe resource usage while a query runs" idea.
"""

import threading
import time
import psutil


class ResourceMonitor:
    def __init__(self, interval=0.05):
        self.interval = interval
        self.samples = []
        self.process = psutil.Process()   # this Python process
        self._stop_flag = threading.Event()
        self._thread = None

    def start(self):
        self.samples = []
        self._stop_flag.clear()

        # psutil needs one "priming" call before cpu_percent() gives real numbers
        psutil.cpu_percent(interval=None)
        self.process.cpu_percent(interval=None)

        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()

    def _sample_loop(self):
        while not self._stop_flag.is_set():
            try:
                sample = {
                    "ts": time.time(),
                    "cpu_percent": psutil.cpu_percent(interval=None),
                    "process_cpu_percent": self.process.cpu_percent(interval=None),
                    "memory_mb": round(self.process.memory_info().rss / (1024 * 1024), 2),
                    "system_memory_percent": psutil.virtual_memory().percent,
                }
                self.samples.append(sample)
            except Exception:
                # if a sample fails for any reason, just skip it and keep going
                pass
            time.sleep(self.interval)

    def stop(self):
        self._stop_flag.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        return self.summarize()

    def summarize(self):
        """Turns the list of raw samples into a few useful summary numbers."""
        if not self.samples:
            return {
                "avg_cpu_percent": 0,
                "peak_cpu_percent": 0,
                "avg_process_cpu_percent": 0,
                "peak_memory_mb": 0,
                "sample_count": 0,
            }

        cpu_values = [s["cpu_percent"] for s in self.samples]
        process_cpu_values = [s["process_cpu_percent"] for s in self.samples]
        memory_values = [s["memory_mb"] for s in self.samples]

        return {
            "avg_cpu_percent": round(sum(cpu_values) / len(cpu_values), 2),
            "peak_cpu_percent": round(max(cpu_values), 2),
            "avg_process_cpu_percent": round(sum(process_cpu_values) / len(process_cpu_values), 2),
            "peak_memory_mb": round(max(memory_values), 2),
            "sample_count": len(self.samples),
        }
