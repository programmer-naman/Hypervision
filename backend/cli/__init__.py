"""
cli package -- the terminal version of the HyperVision dashboard.

    python run.py                          interactive console
    python run.py -q "SELECT ..." -t 4     run one query and exit
    python run.py -q "SELECT ..." --apply all
                                           run it, apply every automatic
                                           recommendation, print the comparison
    python run.py --vm-backend xen --vm-name guest1 --vm-host root@192.168.1.10
                                           interactive console with real VM control
    python run.py --txn-demo | --history
"""

import argparse
import os
import sys

from cli.console import HyperVisionCLI
from cli.ui import red


def main():
    parser = argparse.ArgumentParser(description="HyperVision terminal console")
    parser.add_argument("-q", "--query", help="run one SQL query and exit")
    parser.add_argument("-t", "--threads", type=int, default=1,
                        help="simulated vCPU/threads for --query (default 1)")
    parser.add_argument("--apply", metavar="N", nargs="+",
                        help="after --query, apply recommendation numbers (or 'all') and compare")
    parser.add_argument("--vm-backend", choices=["dryrun", "xen", "kvm"],
                        help="connect VM control at start-up (see `vm` in the console)")
    parser.add_argument("--vm-name", help="name of the virtual machine (xen/kvm)")
    parser.add_argument("--vm-host", help="user@address of the hypervisor host; commands are sent with ssh")
    parser.add_argument("--txn-demo", action="store_true",
                        help="run the concurrency / deadlock demo and exit")
    parser.add_argument("--history", action="store_true", help="show experiment history and exit")
    args = parser.parse_args()

    try:
        _run(args)
    except BrokenPipeError:      # e.g. output piped into `head`
        try:
            sys.stdout = open(os.devnull, "w")
        except Exception:
            pass


def _run(args):
    app = HyperVisionCLI()
    app.settings = app.settings.with_changes({"thread_count": max(1, args.threads)})

    if args.vm_backend:
        try:
            app.configure_vm(args.vm_backend, args.vm_name, args.vm_host)
        except Exception as e:
            print(red("VM error: %s" % e))
            sys.exit(1)

    if args.txn_demo:
        app.txn_demo()
    elif args.history:
        app.show_history()
    elif args.query:
        if not app.storage.tables:
            print(red("No tables loaded. Run: python backend/data/sample_data_generator.py"))
            sys.exit(1)
        app.run_sql(args.query)
        if args.apply and app.last_record:
            app.apply(args.apply)
    else:
        app.loop()
