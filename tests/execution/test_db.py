"""L1 unit tests for execution/db.py — SQL validation and authorizer."""

from __future__ import annotations

import sqlite3

import pytest

from autoskillit.execution.db import _select_only_authorizer, _validate_select_only


class TestValidateSelectOnly:
    """SQL validation: pure function _validate_select_only."""

    def test_accepts_simple_select(self):
        _validate_select_only("SELECT * FROM users")

    def test_accepts_select_with_where(self):
        _validate_select_only("SELECT id, name FROM users WHERE age > ?")

    def test_accepts_select_with_join(self):
        _validate_select_only("SELECT a.id FROM a JOIN b ON a.id = b.id")

    def test_accepts_select_with_subquery(self):
        _validate_select_only("SELECT * FROM (SELECT id FROM users)")

    def test_accepts_leading_whitespace(self):
        _validate_select_only("  \n  SELECT 1")

    def test_rejects_insert(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("INSERT INTO users VALUES (1, 'a')")

    def test_rejects_update(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("UPDATE users SET name = 'x'")

    def test_rejects_delete(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("DELETE FROM users")

    def test_rejects_drop(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("DROP TABLE users")

    def test_rejects_alter(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("ALTER TABLE users ADD COLUMN x")

    def test_rejects_attach(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("ATTACH DATABASE 'other.db' AS other")

    def test_rejects_create(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("CREATE TABLE evil (id INT)")

    def test_rejects_pragma(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("PRAGMA table_info(users)")

    def test_rejects_non_select_start(self):
        with pytest.raises(ValueError, match="must begin with SELECT"):
            _validate_select_only("WITH cte AS (SELECT 1) SELECT * FROM cte")

    def test_rejects_empty_query(self):
        with pytest.raises(ValueError):
            _validate_select_only("")

    def test_rejects_comment_hiding_write(self):
        with pytest.raises(ValueError, match="forbidden"):
            _validate_select_only("SELECT 1; -- \nDROP TABLE users")


class TestSelectOnlyAuthorizer:
    """SQLite authorizer callback tests."""

    def test_allows_sqlite_select(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_SELECT, None, None, None, None)
            == sqlite3.SQLITE_OK
        )

    def test_allows_sqlite_read(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_READ, "users", "id", "main", None)
            == sqlite3.SQLITE_OK
        )

    def test_allows_sqlite_function(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_FUNCTION, None, "count", None, None)
            == sqlite3.SQLITE_OK
        )

    def test_denies_sqlite_insert(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_INSERT, "users", None, "main", None)
            == sqlite3.SQLITE_DENY
        )

    def test_denies_sqlite_delete(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_DELETE, "users", None, "main", None)
            == sqlite3.SQLITE_DENY
        )

    def test_denies_sqlite_update(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_UPDATE, "users", "name", "main", None)
            == sqlite3.SQLITE_DENY
        )

    def test_denies_sqlite_create_table(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_CREATE_TABLE, "evil", None, "main", None)
            == sqlite3.SQLITE_DENY
        )

    def test_denies_sqlite_drop_table(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_DROP_TABLE, "users", None, "main", None)
            == sqlite3.SQLITE_DENY
        )

    def test_denies_sqlite_attach(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_ATTACH, "other.db", None, None, None)
            == sqlite3.SQLITE_DENY
        )

    def test_denies_sqlite_pragma(self):
        assert (
            _select_only_authorizer(sqlite3.SQLITE_PRAGMA, "table_info", None, None, None)
            == sqlite3.SQLITE_DENY
        )
