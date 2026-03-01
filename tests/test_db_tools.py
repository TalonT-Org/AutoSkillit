"""Tests for autoskillit.execution.db module."""

from __future__ import annotations

import base64
import sqlite3
from pathlib import Path

import pytest

from autoskillit.execution.db import (
    _execute_readonly_query,
    _row_to_dict,
    _validate_select_only,
)


class TestValidateSelectOnly:
    def test_valid_select(self):
        _validate_select_only("SELECT * FROM foo")

    def test_accepts_select_with_where(self):
        _validate_select_only("SELECT id, name FROM users WHERE age > ?")

    def test_accepts_select_with_join(self):
        _validate_select_only("SELECT a.id FROM a JOIN b ON a.id = b.id")

    def test_accepts_select_with_subquery(self):
        _validate_select_only("SELECT * FROM (SELECT id FROM users)")

    def test_accepts_leading_whitespace(self):
        _validate_select_only("  \n  SELECT 1")

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            _validate_select_only("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            _validate_select_only("   ")

    def test_drop_raises(self):
        with pytest.raises(ValueError):
            _validate_select_only("DROP TABLE foo")

    def test_insert_raises(self):
        with pytest.raises(ValueError):
            _validate_select_only("INSERT INTO foo VALUES (1)")

    def test_update_raises(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("UPDATE users SET name = 'x'")

    def test_delete_raises(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("DELETE FROM users")

    def test_alter_raises(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("ALTER TABLE users ADD COLUMN x")

    def test_attach_raises(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("ATTACH DATABASE 'other.db' AS other")

    def test_create_raises(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("CREATE TABLE evil (id INT)")

    def test_pragma_raises(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("PRAGMA table_info(users)")

    def test_non_select_start_raises(self):
        with pytest.raises(ValueError, match="must begin with SELECT"):
            _validate_select_only("WITH cte AS (SELECT 1) SELECT * FROM cte")

    def test_comment_hiding_write_raises(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("SELECT 1; -- \nDROP TABLE users")


class TestRowToDict:
    def test_plain_values(self):
        result = _row_to_dict(["a", "b"], (1, "x"))
        assert result == {"a": 1, "b": "x"}

    def test_bytes_base64_encoded(self):
        result = _row_to_dict(["col"], (b"hello",))
        assert result["col"] == base64.b64encode(b"hello").decode("ascii")

    def test_none_value_preserved(self):
        result = _row_to_dict(["col"], (None,))
        assert result["col"] is None

    def test_multiple_columns(self):
        result = _row_to_dict(["id", "name", "score"], (42, "Alice", 9.5))
        assert result == {"id": 42, "name": "Alice", "score": 9.5}


class TestExecuteReadonlyQuery:
    def _make_db(self, tmp_path: Path) -> str:
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, data BLOB)")
        conn.execute("INSERT INTO items VALUES (1, 'alpha', NULL)")
        conn.execute("INSERT INTO items VALUES (2, 'beta', NULL)")
        conn.commit()
        conn.close()
        return db_path

    def test_basic_select(self, tmp_path):
        db_path = self._make_db(tmp_path)
        result = _execute_readonly_query(db_path, "SELECT id, name FROM items", [], 30, 1000)
        assert result["row_count"] == 2
        assert result["column_names"] == ["id", "name"]
        assert result["rows"][0] == {"id": 1, "name": "alpha"}

    def test_truncated_when_max_rows_exceeded(self, tmp_path):
        db_path = self._make_db(tmp_path)
        result = _execute_readonly_query(db_path, "SELECT * FROM items", [], 30, 1)
        assert result["truncated"] is True
        assert result["row_count"] == 1

    def test_not_truncated_when_within_max_rows(self, tmp_path):
        db_path = self._make_db(tmp_path)
        result = _execute_readonly_query(db_path, "SELECT * FROM items", [], 30, 1000)
        assert result["truncated"] is False
        assert result["row_count"] == 2

    def test_write_blocked(self, tmp_path):
        db_path = self._make_db(tmp_path)
        with pytest.raises(Exception):
            _execute_readonly_query(
                db_path, "INSERT INTO items VALUES (99, 'evil', NULL)", [], 30, 1000
            )

    def test_parameterized_query(self, tmp_path):
        db_path = self._make_db(tmp_path)
        result = _execute_readonly_query(
            db_path, "SELECT id, name FROM items WHERE id = ?", [1], 30, 1000
        )
        assert result["row_count"] == 1
        assert result["rows"][0]["name"] == "alpha"
