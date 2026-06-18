"""Tests for TuShare audit and interface registry services."""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from stock_datasource.services.tushare_interface_registry import (
    TuShareInterface,
    TuShareInterfaceRegistry,
    get_interface_registry,
)
from stock_datasource.services.tushare_audit import TableCoverage, TuShareAudit, get_tushare_audit


class TestTuShareInterfaceRegistry:
    """Tests for the interface registry."""

    def test_registry_initialization(self):
        """Registry should initialize without errors."""
        registry = TuShareInterfaceRegistry()
        assert registry is not None

    def test_registry_has_interfaces(self):
        """Registry should have at least some interfaces from existing plugins."""
        registry = get_interface_registry()
        interfaces = registry.list_all()
        # There should be at least a few tushare_* plugins
        assert len(interfaces) > 0
        # Check that known plugins are present
        plugin_names = {i.plugin_name for i in interfaces}
        assert "tushare_daily" in plugin_names or "tushare_daily_info" in plugin_names

    def test_list_by_category(self):
        """Should filter interfaces by category."""
        registry = get_interface_registry()
        cn_stock = registry.list_by_category("cn_stock")
        assert len(cn_stock) > 0
        assert all(i.category == "cn_stock" for i in cn_stock)

    def test_list_by_role(self):
        """Should filter interfaces by role."""
        registry = get_interface_registry()
        primary = registry.list_by_role("primary")
        assert len(primary) > 0
        assert all(i.role == "primary" for i in primary)

    def test_to_dict(self):
        """Should convert to dict format for reporting."""
        registry = get_interface_registry()
        result = registry.to_dict()
        assert "total_interfaces" in result
        assert "by_category" in result
        assert "by_role" in result
        assert "interfaces" in result
        assert result["total_interfaces"] == len(registry.list_all())


class TestTuShareAudit:
    """Tests for the audit service."""

    def test_audit_initialization(self):
        """Audit service should initialize without errors."""
        audit = TuShareAudit()
        assert audit is not None

    @patch("stock_datasource.services.tushare_audit.db_client")
    def test_run_audit_basic(self, mock_db):
        """Should run audit with mocked database."""
        # Setup mock
        mock_db.table_exists.return_value = True
        mock_db.execute_query.return_value = pd.DataFrame({"cnt": [100]})

        audit = TuShareAudit(db=mock_db)
        # Override registry to have just one test interface
        test_iface = TuShareInterface(
            interface_name="test_api",
            plugin_name="tushare_test",
            table_name="test_table",
            category="cn_stock",
            role="primary",
        )
        audit.registry._interfaces = {"tushare_test": test_iface}

        result = audit.run_audit()

        assert result is not None
        assert result.total_interfaces == 1
        mock_db.table_exists.assert_called_with("test_table")

    @patch("stock_datasource.services.tushare_audit.db_client")
    def test_audit_missing_table(self, mock_db):
        """Should handle missing tables correctly."""
        mock_db.table_exists.return_value = False

        audit = TuShareAudit(db=mock_db)
        test_iface = TuShareInterface(
            interface_name="test_api",
            plugin_name="tushare_test",
            table_name="nonexistent_table",
            category="cn_stock",
            role="primary",
        )
        audit.registry._interfaces = {"tushare_test": test_iface}

        result = audit.run_audit()

        assert result.tables[0].exists is False
        assert result.tables[0].is_empty is True
        assert any(i["type"] == "missing_table" for i in result.issues)

    @patch("stock_datasource.services.tushare_audit.db_client")
    def test_audit_empty_table(self, mock_db):
        """Should handle empty tables correctly."""
        mock_db.table_exists.return_value = True
        mock_db.execute_query.return_value = pd.DataFrame({"cnt": [0]})

        audit = TuShareAudit(db=mock_db)
        test_iface = TuShareInterface(
            interface_name="test_api",
            plugin_name="tushare_test",
            table_name="empty_table",
            category="cn_stock",
            role="primary",
        )
        audit.registry._interfaces = {"tushare_test": test_iface}

        result = audit.run_audit()

        assert result.tables[0].exists is True
        assert result.tables[0].is_empty is True
        assert any(i["type"] == "empty_table" for i in result.issues)

    @patch("stock_datasource.services.tushare_audit.db_client")
    def test_get_empty_tables(self, mock_db):
        """Should return empty and missing tables."""
        mock_db.table_exists.side_effect = lambda t: t == "existing_empty"
        mock_db.execute_query.return_value = pd.DataFrame({"cnt": [0]})

        audit = TuShareAudit(db=mock_db)
        test_iface1 = TuShareInterface(
            interface_name="test1",
            plugin_name="tushare_test1",
            table_name="existing_empty",
            category="cn_stock",
            role="primary",
        )
        test_iface2 = TuShareInterface(
            interface_name="test2",
            plugin_name="tushare_test2",
            table_name="missing_table",
            category="cn_stock",
            role="primary",
        )
        audit.registry._interfaces = {
            "tushare_test1": test_iface1,
            "tushare_test2": test_iface2,
        }

        empty = audit.get_empty_tables()

        assert len(empty) == 2
        statuses = {e["status"] for e in empty}
        assert "empty" in statuses
        assert "missing" in statuses


class TestTableCoverage:
    """Tests for TableCoverage dataclass."""

    def test_table_coverage_defaults(self):
        """Should have sensible default values."""
        coverage = TableCoverage(
            table_name="test",
            plugin_name="tushare_test",
            interface_name="test_api",
        )
        assert coverage.exists is False
        assert coverage.is_empty is True
        assert coverage.row_count == 0
        assert coverage.completeness_pct == 0.0


class TestGetTushareAudit:
    """Tests for singleton accessor."""

    def test_returns_same_instance(self):
        """Should return the same instance on repeated calls."""
        audit1 = get_tushare_audit()
        audit2 = get_tushare_audit()
        assert audit1 is audit2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
