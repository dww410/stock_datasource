"""Tests for TuShare daily_info plugin conversion behavior."""

import importlib
import sys
import types
from datetime import date

import numpy as np
import pandas as pd


def _load_plugin_with_stubbed_extractor(monkeypatch):
    """Import the plugin without constructing the real daily_info extractor."""
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    module_name = "stock_datasource.plugins.tushare_daily_info.plugin"
    extractor_name = "stock_datasource.plugins.tushare_daily_info.extractor"
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    monkeypatch.setitem(
        sys.modules,
        extractor_name,
        types.SimpleNamespace(extractor=object()),
    )
    return importlib.import_module(module_name).TuShareDailyInfoPlugin


def test_transform_data_converts_dates_and_clickhouse_safe_values(monkeypatch):
    plugin_cls = _load_plugin_with_stubbed_extractor(monkeypatch)
    plugin = plugin_cls()
    data = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": "20220104",
                "ts_name": "平安银行",
                "exchange": "SZSE",
                "total_mv": np.float64(123.45),
                "float_mv": np.nan,
                "total_share": np.int64(1000),
                "com_count": np.int64(42),
            },
            {
                "ts_code": "000002.SZ",
                "trade_date": "2022-01-04",
                "ts_name": "万科A",
                "exchange": "SZSE",
                "total_mv": np.inf,
                "float_mv": np.float64(456.78),
                "total_share": pd.NA,
                "com_count": np.int64(43),
            },
            {
                "ts_code": "000003.SZ",
                "trade_date": pd.NaT,
                "ts_name": "缺失日期",
                "exchange": "SZSE",
                "total_mv": np.float64(789.01),
                "float_mv": np.float64(123.45),
                "total_share": np.int64(2000),
                "com_count": np.int64(44),
            },
        ]
    )

    transformed = plugin.transform_data(data)

    assert transformed.loc[0, "trade_date"] == date(2022, 1, 4)
    assert transformed.loc[1, "trade_date"] == date(2022, 1, 4)
    assert transformed.loc[0, "float_mv"] is None
    assert transformed.loc[1, "total_mv"] is None
    assert transformed.loc[1, "total_share"] is None
    assert type(transformed.loc[0, "com_count"]) is int
    assert type(transformed.loc[0, "total_share"]) is float
    assert type(transformed.loc[0, "total_mv"]) is float
    assert type(transformed.loc[1, "float_mv"]) is float


def test_safe_value_converts_pandas_nat_to_none(monkeypatch):
    plugin_cls = _load_plugin_with_stubbed_extractor(monkeypatch)

    assert plugin_cls._safe_value(pd.NaT) is None



def test_load_data_inserts_only_ods_daily_info_with_safe_values(monkeypatch):
    plugin_cls = _load_plugin_with_stubbed_extractor(monkeypatch)
    plugin = plugin_cls()

    class RecordingDb:
        def __init__(self):
            self.calls = []

        def insert_dataframe(self, table_name, data):
            self.calls.append((table_name, data.copy()))

    plugin.db = RecordingDb()
    data = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": "20220104",
                "ts_name": "平安银行",
                "exchange": "SZSE",
                "total_mv": np.float64(123.45),
                "float_mv": np.nan,
                "total_share": np.int64(1000),
            }
        ]
    )
    transformed = plugin.transform_data(data)

    result = plugin.load_data(transformed)

    assert result["status"] == "success"
    assert len(plugin.db.calls) == 1
    table_name, inserted = plugin.db.calls[0]
    assert table_name == "ods_daily_info"
    assert inserted.loc[0, "trade_date"] == date(2022, 1, 4)
    assert inserted.loc[0, "float_mv"] is None
    assert type(inserted.loc[0, "total_share"]) is int
