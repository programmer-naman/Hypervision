"""
storage.py
----------
This is our "storage engine". A real DBMS stores data on disk in special
file formats (pages, B-trees, etc). For this mini project we keep it
simple: we load a CSV file fully into memory as a list of Python
dictionaries (one dictionary per row). This is enough to demonstrate how
a DBMS represents a table internally, without getting into disk-level
storage formats.
"""

import csv
import os


class Table:
    """A simple in-memory table: a name, its rows, and its column names."""

    def __init__(self, name, rows, columns):
        self.name = name
        self.rows = rows          # list of dict -> one dict per row
        self.columns = columns    # list of column name strings


class Storage:
    """
    Loads every CSV file found inside `data_dir` as a table.
    Example: sample_data/sales.csv becomes a table called "sales".
    """

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.tables = {}
        self.load_all_csv_files()

    def load_all_csv_files(self):
        if not os.path.isdir(self.data_dir):
            return
        for file_name in os.listdir(self.data_dir):
            if file_name.endswith(".csv"):
                table_name = file_name.replace(".csv", "")
                self.load_csv(table_name, os.path.join(self.data_dir, file_name))

    def load_csv(self, table_name, file_path):
        rows = []
        with open(file_path, newline="") as f:
            reader = csv.DictReader(f)
            columns = reader.fieldnames
            for row in reader:
                # Convert numeric-looking strings to int/float so that
                # WHERE conditions like "salary > 50000" work correctly.
                clean_row = {}
                for key, value in row.items():
                    clean_row[key] = convert_value(value)
                rows.append(clean_row)
        self.tables[table_name] = Table(table_name, rows, columns)

    def get_table(self, table_name):
        if table_name not in self.tables:
            raise ValueError(
                "Table '%s' not found. Loaded tables: %s"
                % (table_name, list(self.tables.keys()))
            )
        return self.tables[table_name]


def convert_value(value):
    """Try to turn a CSV string into an int or float; otherwise keep it as a string."""
    if value is None or value == "":
        return None
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value
