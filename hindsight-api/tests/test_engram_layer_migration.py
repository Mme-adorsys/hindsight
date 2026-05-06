"""Shape test for Epic 25 Story 02 alembic migration `e25a02layer`.

Pure unit: imports the migration module with `alembic.op` mocked to a
MagicMock, drives `upgrade()` / `downgrade()`, and asserts the emitted SQL
contains the expected substrings. No real Postgres needed; the
end-to-end DB check belongs in the integration test suite.
"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

MIGRATION_MODULE = "hindsight_api.alembic.versions.e25a02layer_engram_layer_constraint"


def _load_migration_with_mocked_op(target_schema: str | None = None):
    """Import the migration with `alembic.op` patched to a MagicMock.

    The mock returns ``target_schema`` from ``get_context().config.get_main_option``
    so we can exercise both default and multi-tenant code paths.
    """
    op_mock = MagicMock()
    op_mock.get_context.return_value.config.get_main_option.return_value = target_schema
    with patch.dict("sys.modules", {"alembic": MagicMock(op=op_mock)}):
        # Patch the module-level `op` directly. Reimport ensures a clean
        # binding even if this test runs after another that imported it.
        if MIGRATION_MODULE in importlib.sys.modules:
            del importlib.sys.modules[MIGRATION_MODULE]
        with patch(f"{MIGRATION_MODULE}.op", op_mock):
            module = importlib.import_module(MIGRATION_MODULE)
            return module, op_mock


def _all_executed_sql(op_mock: MagicMock) -> str:
    """Concatenate every SQL string passed to op.execute() for substring asserts."""
    return "\n".join(call.args[0] for call in op_mock.execute.call_args_list)


class TestUpgradeShape:
    def test_revision_chained_on_d6e7f8a9b0c1(self):
        module, _ = _load_migration_with_mocked_op()
        assert module.revision == "e25a02layer"
        assert module.down_revision == "d6e7f8a9b0c1"

    def test_upgrade_adds_audit_column(self):
        module, op_mock = _load_migration_with_mocked_op()
        module.upgrade()
        # First call must be add_column with migrated_from_neocortex_at.
        first_add_column = op_mock.add_column.call_args_list[0]
        assert first_add_column.args[0] == "engram_dictionary"
        column = first_add_column.args[1]
        assert column.name == "migrated_from_neocortex_at"

    def test_upgrade_folds_neocortex_to_buffer(self):
        module, op_mock = _load_migration_with_mocked_op()
        module.upgrade()
        sql = _all_executed_sql(op_mock)
        assert "UPDATE engram_dictionary" in sql
        assert "SET layer = 'buffer'" in sql
        assert "migrated_from_neocortex_at = NOW()" in sql
        assert "WHERE layer = 'neocortex'" in sql

    def test_upgrade_tightens_check_constraint(self):
        module, op_mock = _load_migration_with_mocked_op()
        module.upgrade()
        sql = _all_executed_sql(op_mock)
        assert "DROP CONSTRAINT IF EXISTS engram_dictionary_layer_check" in sql
        assert "CHECK (layer IN ('working', 'buffer'))" in sql
        # Must NOT re-add 'neocortex' to the constraint.
        assert "'neocortex'" not in sql.split("ADD CONSTRAINT")[-1]

    def test_upgrade_respects_target_schema(self):
        module, op_mock = _load_migration_with_mocked_op(target_schema="tenant_42")
        module.upgrade()
        sql = _all_executed_sql(op_mock)
        assert '"tenant_42".engram_dictionary' in sql


class TestDowngradeShape:
    def test_downgrade_restores_neocortex_check(self):
        module, op_mock = _load_migration_with_mocked_op()
        module.downgrade()
        sql = _all_executed_sql(op_mock)
        assert "CHECK (layer IN ('working', 'buffer', 'neocortex'))" in sql

    def test_downgrade_unfolds_migrated_rows(self):
        module, op_mock = _load_migration_with_mocked_op()
        module.downgrade()
        sql = _all_executed_sql(op_mock)
        assert "SET layer = 'neocortex'" in sql
        assert "migrated_from_neocortex_at IS NOT NULL" in sql

    def test_downgrade_drops_audit_column(self):
        module, op_mock = _load_migration_with_mocked_op()
        module.downgrade()
        op_mock.drop_column.assert_called_once()
        assert op_mock.drop_column.call_args.args == (
            "engram_dictionary",
            "migrated_from_neocortex_at",
        )
