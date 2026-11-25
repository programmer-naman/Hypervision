"""
run.py
------
Entry point for the whole project (terminal version).

Interactive mode:      python run.py
One-shot query:        python run.py -q "SELECT region, COUNT(*) FROM sales GROUP BY region" -t 4
History only:          python run.py --history
"""

import os
import sys

# add the backend folder to Python's import path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

if "--web" in sys.argv:
    from web_app import app
    port = int(os.environ.get("PORT", "5000"))
    print(f"HyperVision Web App: http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
else:
    from cli import main
    if __name__ == "__main__":
        main()
