"""
storage.py
----------
Storage engine for the HyperVision Mini DBMS.
Represents tables in memory with schema awareness, index maintenance,
and dynamic table creation.
"""

from __future__ import annotations
import copy
import csv
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from .indexes import IndexManager, BaseIndex


class Table:
    """An in-memory database table: a name, rows, column names, and attached indexes."""

    def __init__(self, name: str, rows: List[Dict[str, Any]], columns: List[str], storage: Optional[Storage] = None):
        self.name = name
        self.rows: List[Dict[str, Any]] = rows          # list of dict -> one dict per row
        self.columns: List[str] = list(columns)        # list of column name strings
        self.storage = storage

    def get_column_types(self) -> Dict[str, str]:
        """Infers SQL data types from sample rows."""
        types = {}
        for col in self.columns:
            types[col] = "VARCHAR"
            for row in self.rows[:50]:
                val = row.get(col)
                if val is not None:
                    if isinstance(val, int):
                        types[col] = "INT"
                        break
                    elif isinstance(val, float):
                        types[col] = "DECIMAL"
                        break
                    elif isinstance(val, str):
                        types[col] = "VARCHAR"
                        break
        return types

    def insert_row(self, row: Dict[str, Any], index_manager: Optional[IndexManager] = None) -> Dict[str, Any]:
        """Inserts a new row into the table and notifies attached indexes."""
        # Normalize row to match table schema
        clean_row = {}
        for col in self.columns:
            clean_row[col] = row.get(col)
        self.rows.append(clean_row)

        idx_mgr = index_manager or (self.storage.index_manager if self.storage else None)
        if idx_mgr:
            idx_mgr.notify_insert(self.name, clean_row)
        return clean_row

    def update_row(
        self,
        old_row: Dict[str, Any],
        assignments: Dict[str, Any],
        index_manager: Optional[IndexManager] = None
    ) -> Dict[str, Any]:
        """Updates an existing row in place and notifies attached indexes."""
        new_row = dict(old_row)
        for col, val in assignments.items():
            if col in self.columns:
                new_row[col] = val

        idx_mgr = index_manager or (self.storage.index_manager if self.storage else None)
        if idx_mgr:
            idx_mgr.notify_update(self.name, old_row, new_row)

        old_row.update(new_row)
        return old_row

    def delete_row(self, row: Dict[str, Any], index_manager: Optional[IndexManager] = None) -> None:
        """Deletes a row from the table and notifies attached indexes."""
        idx_mgr = index_manager or (self.storage.index_manager if self.storage else None)
        if idx_mgr:
            idx_mgr.notify_delete(self.name, row)
        try:
            self.rows.remove(row)
        except ValueError:
            pass


class Storage:
    """
    Manages all database tables, indexes, and persistence.
    Loads every CSV file found inside `data_dir` as an in-memory table.
    """

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.tables: Dict[str, Table] = {}
        self.index_manager = IndexManager()
        self.load_all_csv_files()

    def load_all_csv_files(self) -> None:
        if not os.path.isdir(self.data_dir):
            return
        for file_name in os.listdir(self.data_dir):
            if file_name.endswith(".csv"):
                table_name = file_name[:-4]
                self.load_csv(table_name, os.path.join(self.data_dir, file_name))

    def load_csv(self, table_name: str, file_path: str) -> None:
        rows = []
        with open(file_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            columns = list(reader.fieldnames or [])
            for row in reader:
                clean_row = {}
                for key, value in row.items():
                    clean_row[key] = convert_value(value)
                rows.append(clean_row)
        self.tables[table_name] = Table(table_name, rows, columns, storage=self)

    def get_table(self, table_name: str) -> Table:
        if table_name not in self.tables:
            raise ValueError(
                f"Table '{table_name}' not found. Loaded tables: {list(self.tables.keys())}"
            )
        return self.tables[table_name]

    def create_table(self, table_name: str, columns: List[str]) -> Table:
        if table_name in self.tables:
            raise ValueError(f"Table '{table_name}' already exists.")
        if not columns:
            raise ValueError("Cannot create table without columns.")
        table = Table(table_name, [], list(columns), storage=self)
        self.tables[table_name] = table
        return table

    def drop_table(self, table_name: str) -> bool:
        if table_name not in self.tables:
            raise ValueError(f"Table '{table_name}' does not exist.")
        del self.tables[table_name]
        # Drop any attached indexes
        try:
            table_idxs = self.index_manager.get_table_indexes(table_name)
            for idx_name in list(table_idxs.keys()):
                self.index_manager.drop_index(idx_name, table_name)
        except Exception:
            pass
        return True

    def save_table(self, table_name: str) -> None:
        """Writes in-memory table back to CSV file in data_dir."""
        if table_name not in self.tables:
            raise ValueError(f"Table '{table_name}' not found.")
        table = self.tables[table_name]
        os.makedirs(self.data_dir, exist_ok=True)
        file_path = os.path.join(self.data_dir, f"{table_name}.csv")
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=table.columns)
            writer.writeheader()
            for r in table.rows:
                writer.writerow(r)


def convert_value(value: Any) -> Any:
    """Try to turn a CSV string into an int or float; otherwise keep it as a string."""
    if value is None or value == "":
        return None
    str_val = str(value).strip()
    if str_val.lower() == "null":
        return None
    try:
        if "." in str_val:
            return float(str_val)
        return int(str_val)
    except ValueError:
        return str_val
