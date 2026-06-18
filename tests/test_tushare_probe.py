"""Tests for TuShare API probe service."""

import json
import tempfile
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from stock_datasource.services.tushare_probe import (
    ProbeReport,
    ProbeResult,
    ProbeStatus,
    TuShareProbe,
    get_tushare_probe,
)
from stock_datasource.services.tushare_interface_registry import TuShareInterface


class MockTuShareAPI:
    """Mock TuShare API for testing."""

    def daily(self, limit=None):
        """Mock daily interface with data."""
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20240101"],
                "close": [10.0],
                "open": [9.8],
                "high": [10.2],
                "low": [9.7],
                "vol": [1000000],
            }
        )

    def empty_interface(self, limit=None):
        """Mock interface that returns empty."""
        return pd.DataFrame()

    def permission_denied(self, limit=None):
        """Mock interface with permission error."""
        raise Exception("权限不足, permission denied")

    def requires_params(self, limit=None):
        """Mock interface that requires params."""
        raise Exception("缺少参数 'ts_code' is required")


class TestTuShareProbe:
    """Tests for the probe service."""

    def test_probe_initialization(self):
        """Probe service should initialize without errors."""
        probe = TuShareProbe()
        assert probe is not None

    @patch("stock_datasource.services.tushare_probe.ts")
    def test_probe_interface_with_data(self, mock_ts):
        """Should correctly detect interfaces that return data."""
        mock_api = MockTuShareAPI()
        mock_ts.pro_api.return_value = mock_api

        probe = TuShareProbe()
        probe._api = mock_api

        iface = TuShareInterface(
            interface_name="daily",
            plugin_name="tushare_daily",
            table_name="ods_daily",
            category="cn_stock",
            role="primary",
        )

        result = probe._probe_interface(mock_api, iface)

        assert result.status == ProbeStatus.SUPPORTED_WITH_DATA
        assert result.row_count == 1
        assert "trade_date" in result.sample_columns
        assert result.suggested_date_param == "trade_date"

    @patch("stock_datasource.services.tushare_probe.ts")
    def test_probe_interface_empty(self, mock_ts):
        """Should correctly detect empty interfaces."""
        mock_api = MockTuShareAPI()
        mock_ts.pro_api.return_value = mock_api

        probe = TuShareProbe()
        probe._api = mock_api

        iface = TuShareInterface(
            interface_name="empty_interface",
            plugin_name="tushare_empty",
            table_name="ods_empty",
            category="cn_stock",
            role="primary",
        )

        result = probe._probe_interface(mock_api, iface)

        assert result.status == ProbeStatus.SUPPORTED_EMPTY

    @patch("stock_datasource.services.tushare_probe.ts")
    def test_probe_interface_permission_denied(self, mock_ts):
        """Should correctly detect permission denied errors."""
        mock_api = MockTuShareAPI()
        mock_ts.pro_api.return_value = mock_api

        probe = TuShareProbe()
        probe._api = mock_api

        iface = TuShareInterface(
            interface_name="permission_denied",
            plugin_name="tushare_private",
            table_name="ods_private",
            category="cn_stock",
            role="primary",
        )

        result = probe._probe_interface(mock_api, iface)

        assert result.status == ProbeStatus.PERMISSION_DENIED

    @patch("stock_datasource.services.tushare_probe.ts")
    def test_probe_interface_requires_params(self, mock_ts):
        """Should correctly detect required parameter errors."""
        mock_api = MockTuShareAPI()
        mock_ts.pro_api.return_value = mock_api

        probe = TuShareProbe()
        probe._api = mock_api

        iface = TuShareInterface(
            interface_name="requires_params",
            plugin_name="tushare_needs_params",
            table_name="ods_needs_params",
            category="cn_stock",
            role="primary",
        )

        result = probe._probe_interface(mock_api, iface)

        assert result.status == ProbeStatus.REQUIRES_UNAVAILABLE_PARAMS
        assert "ts_code" in result.required_params

    def test_extract_params_from_error(self):
        """Should extract parameter names from error messages."""
        probe = TuShareProbe()

        params = probe._extract_params_from_error("缺少参数 'ts_code' is required")
        assert "ts_code" in params

        params = probe._extract_params_from_error("Parameter 'ann_date' is missing")
        assert "ann_date" in params

    def test_save_report(self):
        """Should save report to JSON file."""
        probe = TuShareProbe()

        result = ProbeResult(
            interface_name="test_api",
            plugin_name="tushare_test",
            plugin_exists=True,
            status=ProbeStatus.SUPPORTED_WITH_DATA,
            row_count=100,
        )

        report = ProbeReport(
            generated_at=datetime.now(),
            total_probed=1,
            with_plugin=1,
            without_plugin=0,
            supported_with_data=1,
            supported_empty=0,
            permission_denied=0,
            requires_params=0,
            api_errors=0,
            results=[result],
            duration_seconds=1.0,
            rate_limit_hits=0,
        )

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        try:
            probe.save_report(report, path)

            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)

            assert saved["total_probed"] == 1
            assert saved["supported_with_data"] == 1
            assert saved["results"][0]["interface_name"] == "test_api"

        finally:
            import os

            os.unlink(path)


class TestGetTushareProbe:
    """Tests for singleton accessor."""

    def test_returns_same_instance(self):
        """Should return the same instance on repeated calls."""
        probe1 = get_tushare_probe()
        probe2 = get_tushare_probe()
        assert probe1 is probe2


class TestProbeStatus:
    """Tests for ProbeStatus enum."""

    def test_all_statuses_exist(self):
        """All expected status values should be defined."""
        assert ProbeStatus.SUPPORTED_WITH_DATA == "supported_with_data"
        assert ProbeStatus.SUPPORTED_EMPTY == "supported_empty"
        assert ProbeStatus.PERMISSION_DENIED == "permission_denied"
        assert ProbeStatus.DEPRECATED_OR_MISSING == "deprecated_or_missing"
        assert ProbeStatus.REQUIRES_UNAVAILABLE_PARAMS == "requires_unavailable_params"
        assert ProbeStatus.API_ERROR == "api_error"
        assert ProbeStatus.RATE_LIMITED == "rate_limited"
        assert ProbeStatus.NOT_TESTED == "not_tested"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
