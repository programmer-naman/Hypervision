"""
sample_data_generator.py
-------------------------
Creates two sample CSV files so we have something to query:

  sales.csv  -> employee_id, department, region, salary   (100,000 rows)
  orders.csv -> order_id, employee_id, amount              (10,000 rows)

Run this once before starting the server:

    python backend/data/sample_data_generator.py

The sales table is big enough that a WHERE + GROUP BY query does
noticeable CPU work -- which is what lets the resource monitor and the
CPU-bound classifier actually show something meaningful.
"""

import csv
import os
import random

random.seed(42)   # fixed seed -> same data every time, useful for demos

DEPARTMENTS = ["Engineering", "Sales", "Marketing", "Support", "Finance", "HR"]
REGIONS = ["North", "South", "East", "West"]

SALES_ROW_COUNT = 100000
ORDERS_ROW_COUNT = 10000


def generate_sales_csv(file_path, row_count):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["employee_id", "department", "region", "salary"])
        for employee_id in range(1, row_count + 1):
            department = random.choice(DEPARTMENTS)
            region = random.choice(REGIONS)
            salary = random.randint(30000, 150000)
            writer.writerow([employee_id, department, region, salary])


def generate_orders_csv(file_path, row_count, max_employee_id):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["order_id", "employee_id", "amount"])
        for order_id in range(1, row_count + 1):
            employee_id = random.randint(1, max_employee_id)
            amount = round(random.uniform(20, 5000), 2)
            writer.writerow([order_id, employee_id, amount])


if __name__ == "__main__":
    sample_data_dir = os.path.join(os.path.dirname(__file__), "..", "..", "sample_data")
    sample_data_dir = os.path.abspath(sample_data_dir)

    generate_sales_csv(os.path.join(sample_data_dir, "sales.csv"), SALES_ROW_COUNT)
    generate_orders_csv(os.path.join(sample_data_dir, "orders.csv"), ORDERS_ROW_COUNT, SALES_ROW_COUNT)

    print("Sample data generated in:", sample_data_dir)
    print("  sales.csv  (%d rows)" % SALES_ROW_COUNT)
    print("  orders.csv (%d rows)" % ORDERS_ROW_COUNT)
