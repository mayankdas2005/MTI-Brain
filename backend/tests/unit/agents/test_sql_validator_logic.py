"""Unit tests for sql_validator_logic — 14+ validation gates."""

import pytest

from app.services.agents.sql_validator_logic import (
    validate_sql,
    try_fix_cte_refs,
    validate_column_names,
    validate_filter_types,
    validate_entity_filter_grounding,
)


class TestGate1_StatementType:
    def test_valid_select(self):
        ok, msg = validate_sql("SELECT id, name FROM lpp.users")
        assert ok is True
        assert msg == ""

    def test_valid_select_with_cte(self):
        sql = "WITH cte AS (SELECT id FROM lpp.users) SELECT id FROM cte"
        ok, msg = validate_sql(sql)
        assert ok is True

    def test_valid_union(self):
        sql = "SELECT id FROM lpp.users UNION ALL SELECT id FROM lpp.accounts"
        ok, msg = validate_sql(sql)
        assert ok is True

    def test_reject_insert(self):
        ok, msg = validate_sql("INSERT INTO lpp.users (name) VALUES ('test')")
        assert ok is False
        assert "DDL/DML rejected" in msg

    def test_reject_update(self):
        ok, msg = validate_sql("UPDATE lpp.users SET name = 'hacked'")
        assert ok is False

    def test_reject_delete(self):
        ok, msg = validate_sql("DELETE FROM lpp.users WHERE id = 1")
        assert ok is False

    def test_reject_drop(self):
        ok, msg = validate_sql("DROP TABLE lpp.users")
        assert ok is False

    def test_reject_create(self):
        ok, msg = validate_sql("CREATE TABLE lpp.evil (id INT)")
        assert ok is False

    def test_reject_alter(self):
        ok, msg = validate_sql("ALTER TABLE lpp.users ADD COLUMN x INT")
        assert ok is False

    def test_reject_truncate(self):
        ok, msg = validate_sql("TRUNCATE TABLE lpp.users")
        assert ok is False

    def test_empty_sql(self):
        ok, msg = validate_sql("")
        assert ok is False

    def test_multiple_statements(self):
        ok, msg = validate_sql("SELECT 1 FROM lpp.a; SELECT 2 FROM lpp.b")
        assert ok is False
        assert "Multiple statements" in msg


class TestGate2_ForbiddenKeywords:
    def test_exec_keyword(self):
        ok, msg = validate_sql("SELECT EXEC xp_cmdshell('ls') FROM lpp.t")
        assert ok is False

    def test_execute_keyword(self):
        ok, msg = validate_sql("SELECT EXECUTE ('DROP TABLE foo') FROM lpp.t")
        assert ok is False

    def test_drop_in_select_context(self):
        ok, msg = validate_sql("SELECT DROP 'table' FROM lpp.t")
        assert ok is False

    def test_insert_in_select_context(self):
        ok, msg = validate_sql("SELECT INSERT INTO lpp.evil FROM lpp.t")
        assert ok is False


class TestGate25_PosixRegex:
    def test_tilde_star(self):
        ok, msg = validate_sql("SELECT * FROM lpp.t WHERE name ~* 'pattern'")
        assert ok is False
        assert "POSIX regex" in msg

    def test_not_tilde(self):
        ok, msg = validate_sql("SELECT * FROM lpp.t WHERE name !~ 'pattern'")
        assert ok is False
        assert "POSIX regex" in msg

    def test_ilike_allowed(self):
        ok, msg = validate_sql("SELECT * FROM lpp.t WHERE name ILIKE '%value%'")
        assert ok is True


class TestGate35_CTETableRefs:
    def test_valid_cte_refs(self):
        sql = """
        WITH base AS (
            SELECT id, amount FROM lpp.transactions
        )
        SELECT base.id, base.amount FROM base
        """
        ok, msg = validate_sql(sql)
        assert ok is True

    def test_invalid_qualified_ref_not_in_scope(self):
        sql = """
        WITH base AS (
            SELECT id FROM lpp.transactions
        ),
        agg AS (
            SELECT lpp.transactions.amount FROM base
        )
        SELECT * FROM agg
        """
        ok, msg = validate_sql(sql)
        assert ok is False
        assert "not in this CTE's FROM clause" in msg


class TestGate36_CTEColumnForwarding:
    def test_valid_column_forwarding(self):
        sql = """
        WITH base AS (
            SELECT id, total FROM lpp.orders
        ),
        agg AS (
            SELECT id, total FROM base
        )
        SELECT id, total FROM agg
        """
        ok, msg = validate_sql(sql)
        assert ok is True

    def test_missing_column_from_upstream_cte(self):
        sql = """
        WITH base AS (
            SELECT id, total FROM lpp.orders
        )
        SELECT id, rate FROM base
        """
        ok, msg = validate_sql(sql)
        assert ok is False
        assert "not exported" in msg or "references bare column" in msg


class TestGate37_AmbiguousJoinOn:
    def test_qualified_join_ok(self):
        sql = """
        SELECT a.id, b.id
        FROM lpp.orders a
        JOIN lpp.items b ON a.id = b.order_id
        """
        ok, msg = validate_sql(sql)
        assert ok is True


class TestTryFixCteRefs:
    def test_fixes_qualified_ref(self):
        sql = """
        WITH base AS (
            SELECT id, amount FROM lpp.transactions
        ),
        agg AS (
            SELECT lpp.transactions.amount FROM base
        )
        SELECT * FROM agg
        """
        fixed = try_fix_cte_refs(sql)
        assert fixed is not None
        assert "lpp.transactions.amount" not in fixed.lower() or "amount" in fixed.lower()

    def test_no_fix_needed(self):
        sql = "WITH base AS (SELECT id FROM lpp.t) SELECT id FROM base"
        result = try_fix_cte_refs(sql)
        assert result is None

    def test_invalid_sql_returns_none(self):
        result = try_fix_cte_refs("NOT VALID SQL {{{{")
        assert result is None


class TestValidateColumnNames:
    def test_valid_columns(self):
        schema = [
            {"table_fqn": "lpp.orders", "name": "id"},
            {"table_fqn": "lpp.orders", "name": "total"},
        ]
        sql = "SELECT o.id, o.total FROM lpp.orders o"
        ok, msg = validate_column_names(sql, schema)
        assert ok is True

    def test_hallucinated_column(self):
        schema = [
            {"table_fqn": "lpp.orders", "name": "id"},
            {"table_fqn": "lpp.orders", "name": "total"},
        ]
        sql = "SELECT o.id, o.nonexistent_col FROM lpp.orders o"
        ok, msg = validate_column_names(sql, schema)
        assert ok is False
        assert "nonexistent_col" in msg

    def test_empty_schema_passes(self):
        ok, msg = validate_column_names("SELECT x FROM lpp.t", [])
        assert ok is True

    def test_cte_scope_skipped(self):
        schema = [{"table_fqn": "lpp.orders", "name": "id"}]
        sql = "WITH cte AS (SELECT id FROM lpp.orders) SELECT cte.id FROM cte"
        ok, msg = validate_column_names(sql, schema)
        assert ok is True


class TestValidateFilterTypes:
    def test_boolean_with_valid_value(self):
        schema = [{"table_fqn": "lpp.flags", "name": "is_active", "data_type": "boolean"}]
        sql = "SELECT * FROM lpp.flags WHERE is_active = 'true'"
        ok, msg = validate_filter_types(sql, schema)
        assert ok is True

    def test_boolean_with_invalid_string(self):
        schema = [{"table_fqn": "lpp.flags", "name": "is_active", "data_type": "boolean"}]
        sql = "SELECT * FROM lpp.flags WHERE is_active = 'Includes Actual'"
        ok, msg = validate_filter_types(sql, schema)
        assert ok is False
        assert "boolean" in msg

    def test_empty_schema_passes(self):
        ok, msg = validate_filter_types("SELECT 1", [])
        assert ok is True


class TestValidateEntityFilterGrounding:
    def test_ilike_on_code_column_rejected(self):
        schema = [{"table_fqn": "lpp.accounts", "name": "account_code", "semantic_type": "code"}]
        sql = "SELECT * FROM lpp.accounts WHERE account_code ILIKE '%USA%'"
        ok, msg = validate_entity_filter_grounding(sql, schema)
        assert ok is False
        assert "code/identifier" in msg

    def test_equality_on_code_column_ok(self):
        schema = [{"table_fqn": "lpp.accounts", "name": "account_code", "semantic_type": "code"}]
        sql = "SELECT * FROM lpp.accounts WHERE account_code = 'US_001'"
        ok, msg = validate_entity_filter_grounding(sql, schema)
        assert ok is True

    def test_non_code_column_ilike_ok(self):
        schema = [{"table_fqn": "lpp.accounts", "name": "description", "semantic_type": "text"}]
        sql = "SELECT * FROM lpp.accounts WHERE description ILIKE '%cash%'"
        ok, msg = validate_entity_filter_grounding(sql, schema)
        assert ok is True

    def test_empty_schema_passes(self):
        ok, msg = validate_entity_filter_grounding("SELECT 1", [])
        assert ok is True


class TestComplexValidQueries:
    def test_window_function(self):
        sql = "SELECT id, ROW_NUMBER() OVER (PARTITION BY group_id ORDER BY date) FROM lpp.events"
        ok, msg = validate_sql(sql)
        assert ok is True

    def test_case_statement(self):
        sql = "SELECT CASE WHEN amount > 0 THEN 'pos' ELSE 'neg' END FROM lpp.transactions"
        ok, msg = validate_sql(sql)
        assert ok is True

    def test_subquery(self):
        sql = "SELECT * FROM lpp.orders WHERE id IN (SELECT order_id FROM lpp.items)"
        ok, msg = validate_sql(sql)
        assert ok is True

    def test_multiple_ctes(self):
        sql = """
        WITH a AS (SELECT id, total FROM lpp.orders),
             b AS (SELECT id, total FROM a WHERE total > 100)
        SELECT id, total FROM b
        """
        ok, msg = validate_sql(sql)
        assert ok is True
