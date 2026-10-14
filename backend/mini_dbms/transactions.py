"""
transactions.py
----------------
A small, educational lock manager. It demonstrates three OS/DBMS
concepts from the project report:

  1. Shared (S) and Exclusive (X) locks on a resource (here, a table name).
  2. A "wait-for graph": if transaction A is waiting for a lock held by
     transaction B, we record an edge A -> B.
  3. Deadlock detection: if the wait-for graph ever has a cycle
     (A waits for B, B waits for A), that is a deadlock.

This lock manager is NOT used inside the query engine itself (our
queries are all read-only SELECTs). It is a standalone demo, triggered
from the dashboard's "Run Concurrency / Deadlock Demo" button, to show
that we understand these concepts even though the mini DBMS does not
need real locking for SELECT-only queries.
"""

import threading


class LockManager:
    def __init__(self):
        self.mutex = threading.Lock()      # protects the data structures below
        self.locks = {}                    # resource -> {"mode": "S"/"X", "holders": set(txn_id)}
        self.wait_for = {}                 # txn_id -> set of txn_ids it is waiting on
        self.next_txn_id = 1

    def new_txn(self):
        txn_id = self.next_txn_id
        self.next_txn_id += 1
        return txn_id

    def acquire(self, txn_id, resource, mode="S"):
        """
        Try to acquire a lock on `resource` for `txn_id`.
        Returns (granted, reason). reason is None on success, otherwise
        "blocked" or "deadlock_detected".
        """
        with self.mutex:
            current = self.locks.get(resource)

            if current is None:
                self.locks[resource] = {"mode": mode, "holders": {txn_id}}
                return True, None

            if current["holders"] == {txn_id}:
                current["mode"] = mode
                return True, None

            if current["mode"] == "S" and mode == "S":
                current["holders"].add(txn_id)
                return True, None

            # Otherwise this transaction must wait for the current holder(s).
            waiting_on = current["holders"] - {txn_id}
            self.wait_for.setdefault(txn_id, set()).update(waiting_on)

            if self.creates_cycle(txn_id):
                self.wait_for[txn_id].discard(txn_id)
                return False, "deadlock_detected"

            return False, "blocked"

    def release_all(self, txn_id):
        with self.mutex:
            for resource in list(self.locks.keys()):
                entry = self.locks[resource]
                entry["holders"].discard(txn_id)
                if not entry["holders"]:
                    del self.locks[resource]

            self.wait_for.pop(txn_id, None)
            for waiters in self.wait_for.values():
                waiters.discard(txn_id)

    def creates_cycle(self, start_txn):
        """
        Simple depth-first search over the wait-for graph, starting from
        start_txn. If we can walk from start_txn back to start_txn, there
        is a cycle -> deadlock.
        """
        visited = set()
        stack = [start_txn]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            for neighbour in self.wait_for.get(node, ()):
                if neighbour == start_txn:
                    return True
                stack.append(neighbour)
        return False
