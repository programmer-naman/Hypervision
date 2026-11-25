"""
indexes.py
----------
Database indexing engine for the HyperVision Mini DBMS.
Supports both HASH indexes (O(1) equality lookup) and BTREE indexes (ordered,
supporting both equality and range queries <, <=, >, >=).
"""

from __future__ import annotations
import bisect
from typing import Any, Dict, List, Optional, Set, Tuple


class BaseIndex:
    """Base interface for all database indexes."""

    def __init__(self, name: str, table_name: str, column: str, index_type: str):
        self.name = name
        self.table_name = table_name
        self.column = column
        self.index_type = index_type.upper()

    def insert(self, row: Dict[str, Any]) -> None:
        raise NotImplementedError

    def delete(self, row: Dict[str, Any]) -> None:
        raise NotImplementedError

    def update(self, old_row: Dict[str, Any], new_row: Dict[str, Any]) -> None:
        self.delete(old_row)
        self.insert(new_row)

    def lookup_equal(self, value: Any) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def lookup_range(self, op: str, value: Any) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def rebuild(self, rows: List[Dict[str, Any]]) -> None:
        raise NotImplementedError

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "table_name": self.table_name,
            "column": self.column,
            "index_type": self.index_type,
            "entries_count": self.size(),
        }

    def size(self) -> int:
        raise NotImplementedError


class HashIndex(BaseIndex):
    """
    Hash-based database index.
    Offers O(1) average-time lookups for equality comparisons (col = value).
    """

    def __init__(self, name: str, table_name: str, column: str):
        super().__init__(name, table_name, column, "HASH")
        # Map: value -> list of row dictionaries
        self._map: Dict[Any, List[Dict[str, Any]]] = {}
        self._total_entries = 0

    def insert(self, row: Dict[str, Any]) -> None:
        val = row.get(self.column)
        if val not in self._map:
            self._map[val] = []
        self._map[val].append(row)
        self._total_entries += 1

    def delete(self, row: Dict[str, Any]) -> None:
        val = row.get(self.column)
        if val in self._map:
            bucket = self._map[val]
            # Remove exact row object
            for idx, r in enumerate(bucket):
                if r is row or r == row:
                    del bucket[idx]
                    self._total_entries -= 1
                    break
            if not bucket:
                del self._map[val]

    def lookup_equal(self, value: Any) -> List[Dict[str, Any]]:
        return list(self._map.get(value, []))

    def lookup_range(self, op: str, value: Any) -> List[Dict[str, Any]]:
        # Hash indexes do not naturally support range queries without full scan
        results = []
        for key, rows in self._map.items():
            if key is None:
                continue
            if op == ">" and key > value:
                results.extend(rows)
            elif op == ">=" and key >= value:
                results.extend(rows)
            elif op == "<" and key < value:
                results.extend(rows)
            elif op == "<=" and key <= value:
                results.extend(rows)
            elif op == "!=" and key != value:
                results.extend(rows)
        return results

    def rebuild(self, rows: List[Dict[str, Any]]) -> None:
        self._map.clear()
        self._total_entries = 0
        for row in rows:
            self.insert(row)

    def size(self) -> int:
        return self._total_entries


class BTreeIndex(BaseIndex):
    """
    B-Tree / Ordered index implementation.
    Maintains keys in sorted order. Offers O(log N) lookup for equality
    and efficient range scans (>, >=, <, <=).
    """

    def __init__(self, name: str, table_name: str, column: str):
        super().__init__(name, table_name, column, "BTREE")
        self._keys: List[Any] = []  # Sorted list of unique non-None keys
        self._map: Dict[Any, List[Dict[str, Any]]] = {}
        self._null_rows: List[Dict[str, Any]] = []
        self._total_entries = 0

    def insert(self, row: Dict[str, Any]) -> None:
        val = row.get(self.column)
        if val is None:
            self._null_rows.append(row)
            self._total_entries += 1
            return

        if val not in self._map:
            # Insert key into sorted keys list maintaining sort order
            bisect.insort(self._keys, val)
            self._map[val] = []
        self._map[val].append(row)
        self._total_entries += 1

    def delete(self, row: Dict[str, Any]) -> None:
        val = row.get(self.column)
        if val is None:
            for idx, r in enumerate(self._null_rows):
                if r is row or r == row:
                    del self._null_rows[idx]
                    self._total_entries -= 1
                    break
            return

        if val in self._map:
            bucket = self._map[val]
            for idx, r in enumerate(bucket):
                if r is row or r == row:
                    del bucket[idx]
                    self._total_entries -= 1
                    break
            if not bucket:
                del self._map[val]
                # Remove key from sorted list
                k_idx = bisect.bisect_left(self._keys, val)
                if k_idx < len(self._keys) and self._keys[k_idx] == val:
                    del self._keys[k_idx]

    def lookup_equal(self, value: Any) -> List[Dict[str, Any]]:
        if value is None:
            return list(self._null_rows)
        return list(self._map.get(value, []))

    def lookup_range(self, op: str, value: Any) -> List[Dict[str, Any]]:
        if value is None:
            return []

        results = []
        # Find bisect point
        if op == ">":
            # Keys strictly greater than value
            idx = bisect.bisect_right(self._keys, value)
            for k in self._keys[idx:]:
                results.extend(self._map[k])
        elif op == ">=":
            idx = bisect.bisect_left(self._keys, value)
            for k in self._keys[idx:]:
                results.extend(self._map[k])
        elif op == "<":
            idx = bisect.bisect_left(self._keys, value)
            for k in self._keys[:idx]:
                results.extend(self._map[k])
        elif op == "<=":
            idx = bisect.bisect_right(self._keys, value)
            for k in self._keys[:idx]:
                results.extend(self._map[k])
        elif op == "!=":
            for k in self._keys:
                if k != value:
                    results.extend(self._map[k])
        return results

    def rebuild(self, rows: List[Dict[str, Any]]) -> None:
        self._keys.clear()
        self._map.clear()
        self._null_rows.clear()
        self._total_entries = 0
        for row in rows:
            self.insert(row)

    def size(self) -> int:
        return self._total_entries


class IndexManager:
    """Manages all database indexes across tables."""

    def __init__(self):
        # table_name -> dict of index_name -> BaseIndex
        self._indexes: Dict[str, Dict[str, BaseIndex]] = {}

    def create_index(
        self,
        index_name: str,
        table_name: str,
        column: str,
        index_type: str = "HASH",
        table_rows: Optional[List[Dict[str, Any]]] = None,
    ) -> BaseIndex:
        table_indexes = self._indexes.setdefault(table_name, {})
        if index_name in table_indexes:
            raise ValueError(f"Index '{index_name}' already exists on table '{table_name}'.")

        # Check if an index already exists for this column
        for existing in table_indexes.values():
            if existing.column == column and existing.index_type == index_type.upper():
                raise ValueError(
                    f"A {index_type.upper()} index already exists on column '{column}' in table '{table_name}'."
                )

        if index_type.upper() == "BTREE":
            index = BTreeIndex(index_name, table_name, column)
        else:
            index = HashIndex(index_name, table_name, column)

        if table_rows:
            index.rebuild(table_rows)

        table_indexes[index_name] = index
        return index

    def drop_index(self, index_name: str, table_name: Optional[str] = None) -> bool:
        if table_name:
            if table_name in self._indexes and index_name in self._indexes[table_name]:
                del self._indexes[table_name][index_name]
                return True
            raise ValueError(f"Index '{index_name}' not found on table '{table_name}'.")

        # Search across all tables
        found = False
        for tbl, idxs in list(self._indexes.items()):
            if index_name in idxs:
                del idxs[index_name]
                found = True
        if not found:
            raise ValueError(f"Index '{index_name}' not found.")
        return True

    def get_index_for_column(
        self, table_name: str, column: str, op: str = "="
    ) -> Optional[BaseIndex]:
        """
        Returns a suitable index for querying `column` on `table_name`.
        If op is '=' both HASH and BTREE are suitable (prefers HASH for O(1)).
        If op is a range operator, prefers BTREE.
        """
        table_indexes = self._indexes.get(table_name, {})
        matching = [idx for idx in table_indexes.values() if idx.column == column]
        if not matching:
            return None

        if op in (">", ">=", "<", "<="):
            # Prefer BTREE for range lookups
            for idx in matching:
                if isinstance(idx, BTreeIndex):
                    return idx
            # Fall back to hash index range check
            return matching[0]

        # For equality '=', prefer HashIndex, else BTreeIndex
        for idx in matching:
            if isinstance(idx, HashIndex):
                return idx
        return matching[0]

    def get_index(self, table_name: str, index_name: str) -> Optional[BaseIndex]:
        return self._indexes.get(table_name, {}).get(index_name)

    def get_table_indexes(self, table_name: str) -> Dict[str, BaseIndex]:
        return dict(self._indexes.get(table_name, {}))

    def list_all_indexes(self) -> List[Dict[str, Any]]:
        result = []
        for tbl, idxs in self._indexes.items():
            for idx in idxs.values():
                result.append(idx.to_dict())
        return result

    def notify_insert(self, table_name: str, row: Dict[str, Any]) -> None:
        for idx in self._indexes.get(table_name, {}).values():
            idx.insert(row)

    def notify_delete(self, table_name: str, row: Dict[str, Any]) -> None:
        for idx in self._indexes.get(table_name, {}).values():
            idx.delete(row)

    def notify_update(
        self, table_name: str, old_row: Dict[str, Any], new_row: Dict[str, Any]
    ) -> None:
        for idx in self._indexes.get(table_name, {}).values():
            idx.update(old_row, new_row)
