"""
run.py
------
Entry point for the whole project (terminal version).

Interactive mode:      python run.py
One-shot query:        python run.py -q "SELECT region, COUNT(*) FROM sales GROUP BY region" -t 4
Deadlock demo only:    python run.py --txn-demo
"""

import os
import sys

# add the backend folder to Python's import path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

from cli import main

if __name__ == "__main__":
    main()
