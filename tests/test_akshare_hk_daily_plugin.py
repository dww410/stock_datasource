"""Tests for AKShare HK daily plugin conversion and loading behavior."""

import os
from datetime import date

os.environ.setdefault("TUSHARE_TOKEN", "test-token")

import pandas as pd
import pytest

from stock_datasource.plugins.akshare_hk_daily import extractor as hk_extractor
from stock_datasource.plugins.akshare_hk_daily.plugin import AKShareHKDailyPlugin
from stock_datasource.plugins.akshare_hk_daily.service import AKShareHKDailyService


def test_code_conversion_between_tushare_and_akshare_formats():
    assert hk_extractor.ts_code_to_akshare("00700.HK") == "00700"
    assert hk_extractor.akshare_to_ts_code("00700") == "00700.HK"
    assert hk_extractor.akshare_to_ts_code("00700.hk") == "00700.HK"


def test_map_akshare_english_rows_to_tushare_shape_after_sorting():
    raw = pd.DataFrame(
        [
            {
                "date": "2026-02-05",
                "open": 305.0,
                "high": 310.0,
                "low": 303.0,
                "close": 309.0,
                "volume": 2000,
            },
            {
                "date": "2026-02-03",
                "open": 300.0,
                "high": 306.0,
                "low": 299.0,
                "close": 304.0,
                "volume": 1000,
            },
            {
                "date": "2026-02-04",
                "open": 304.0,
                "high": 308.0,
                "low": 302.0,
                "close": 307.0,
                "volume": 1500,
            },
        ]
    )

    mapped = hk_extractor.map_akshare_to_tushare(raw, "00700.HK")

    assert list(mapped.columns) == [
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "change",
        "pct_chg",
        "vol",
        "amount",
    ]
    assert mapped.to_dict("records") == [
        {
            "ts_code": "00700.HK",
            "trade_date": date(2026, 2, 4),
            "open": 304.0,
            "high": 308.0,
            "low": 302.0,
            "close": 307.0,
            "pre_close": 304.0,
            "change": 3.0,
            "pct_chg": 0.99,
            "vol": 1500,
            "amount": None,
        },
        {
            "ts_code": "00700.HK",
            "trade_date": date(2026, 2, 5),
            "open": 305.0,
            "high": 310.0,
            "low": 303.0,
            "close": 309.0,
            "pre_close": 307.0,
            "change": 2.0,
            "pct_chg": 0.65,
            "vol": 2000,
            "amount": None,
        },
    ]


def test_map_akshare_chinese_rows_to_tushare_shape():
    raw = pd.DataFrame(
        [
            {"日期": "2026-02-03", "开盘": 10.0, "最高": 11.0, "最低": 9.5, "收盘": 10.5, "成交量": 100},
            {"日期": "2026-02-04", "开盘": 10.5, "最高": 12.0, "最低": 10.2, "收盘": 11.0, "成交量": 120},
        ]
    )

    mapped = hk_extractor.map_akshare_to_tushare(raw, "00001.HK")

    assert mapped.to_dict("records") == [
        {
            "ts_code": "00001.HK",
            "trade_date": date(2026, 2, 4),
            "open": 10.5,
            "high": 12.0,
            "low": 10.2,
            "close": 11.0,
            "pre_close": 10.5,
            "change": 0.5,
            "pct_chg": 4.76,
            "vol": 120,
            "amount": None,
        }
    ]


def test_extract_data_supports_single_stock_symbol_or_ts_code(monkeypatch):
    plugin = AKShareHKDailyPlugin()
    calls = []

    def fake_extract(symbol, start_date=None, end_date=None):
        calls.append((symbol, start_date, end_date))
        return pd.DataFrame(
            [
                {"date": "2026-02-03", "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5, "volume": 100},
                {"date": "2026-02-04", "open": 10.5, "high": 12.0, "low": 10.2, "close": 11.0, "volume": 120},
            ]
        )

    monkeypatch.setattr("stock_datasource.plugins.akshare_hk_daily.plugin.extractor.extract", fake_extract)

    by_symbol = plugin.extract_data(symbol="00700", start_date="20260203", end_date="20260204")
    by_ts_code = plugin.extract_data(ts_code="00001.HK", start_date="20260203", end_date="20260204")

    assert calls == [("00700", "20260203", "20260204"), ("00001", "20260203", "20260204")]
    assert by_symbol.loc[0, "ts_code"] == "00700.HK"
    assert by_ts_code.loc[0, "ts_code"] == "00001.HK"
    assert "symbol" not in by_symbol.columns
    assert "volume" not in by_symbol.columns


def test_extract_data_rejects_conflicting_symbol_and_ts_code():
    plugin = AKShareHKDailyPlugin()

    with pytest.raises(ValueError, match="Conflicting symbol and ts_code"):
        plugin.extract_data(symbol="00700", ts_code="00001.HK")


def test_extract_data_batch_mode_uses_self_db_stock_universe(monkeypatch):
    plugin = AKShareHKDailyPlugin()

    class RecordingDb:
        def __init__(self):
            self.queries = []

        def execute_query(self, query):
            self.queries.append(query)
            return pd.DataFrame({"ts_code": ["00001.HK", "00700.HK", "09988.HK"]})

    plugin.db = RecordingDb()
    calls = []

    def fake_extract(symbol, start_date=None, end_date=None):
        calls.append((symbol, start_date, end_date))
        return pd.DataFrame(
            [
                {"date": "2026-02-03", "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5, "volume": 100},
                {"date": "2026-02-04", "open": 10.5, "high": 12.0, "low": 10.2, "close": 11.0, "volume": 120},
            ]
        )

    monkeypatch.setattr("stock_datasource.plugins.akshare_hk_daily.plugin.extractor.extract", fake_extract)

    extracted = plugin.extract_data(start_date="20260203", end_date="20260204", max_stocks=2)

    assert "FROM ods_hk_basic" in plugin.db.queries[0]
    assert calls == [("00001", "20260203", "20260204"), ("00700", "20260203", "20260204")]
    assert extracted["ts_code"].tolist() == ["00001.HK", "00700.HK"]


def test_extract_data_batch_mode_continues_after_per_stock_errors(monkeypatch):
    plugin = AKShareHKDailyPlugin()

    class RecordingDb:
        def execute_query(self, query):
            return pd.DataFrame({"ts_code": ["00001.HK", "00700.HK"]})

    plugin.db = RecordingDb()
    calls = []

    def fake_extract(symbol, start_date=None, end_date=None):
        calls.append(symbol)
        if symbol == "00001":
            raise RuntimeError("temporary AKShare failure")
        return pd.DataFrame(
            [
                {"date": "2026-02-03", "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5, "volume": 100},
                {"date": "2026-02-04", "open": 10.5, "high": 12.0, "low": 10.2, "close": 11.0, "volume": 120},
            ]
        )

    monkeypatch.setattr("stock_datasource.plugins.akshare_hk_daily.plugin.extractor.extract", fake_extract)

    extracted = plugin.extract_data(start_date="20260203", end_date="20260204")

    assert calls == ["00001", "00700"]
    assert extracted["ts_code"].tolist() == ["00700.HK"]


def test_akshare_hk_daily_service_queries_ts_code_and_vol_schema():
    service = AKShareHKDailyService()

    class RecordingDb:
        def __init__(self):
            self.queries = []

        def execute_query(self, query):
            self.queries.append(query)
            return pd.DataFrame(
                [
                    {
                        "ts_code": "00700.HK",
                        "trade_date": "20260204",
                        "open": 10.0,
                        "high": 11.0,
                        "low": 9.5,
                        "close": 10.5,
                        "vol": 100,
                        "amount": None,
                    }
                ]
            )

    service.db = RecordingDb()

    service.get_hk_daily("00700", start_date="20260203", end_date="20260204")
    service.get_latest_hk_daily("00700.hk", limit=5)

    daily_query, latest_query = service.db.queries
    assert "ts_code" in daily_query
    assert "vol" in daily_query
    assert "symbol" not in daily_query
    assert "volume" not in daily_query
    assert "WHERE ts_code = '00700.HK'" in daily_query
    assert "trade_date >= '20260203'" in daily_query
    assert "trade_date <= '20260204'" in daily_query
    assert "WHERE ts_code = '00700.HK'" in latest_query
    assert "LIMIT 5" in latest_query


def test_load_data_writes_ods_hk_daily_through_self_db_with_large_insert_settings():
    plugin = AKShareHKDailyPlugin()

    class RecordingDb:
        def __init__(self):
            self.calls = []

        def insert_dataframe(self, table_name, data, settings=None):
            self.calls.append((table_name, data.copy(), settings))

    plugin.db = RecordingDb()
    data = pd.DataFrame(
        [
            {
                "ts_code": "00700.HK",
                "trade_date": date(2026, 2, 4),
                "open": 10.5,
                "high": 12.0,
                "low": 10.2,
                "close": 11.0,
                "pre_close": 10.5,
                "change": 0.5,
                "pct_chg": 4.76,
                "vol": 120,
                "amount": None,
            }
        ]
    )

    result = plugin.load_data(data)

    assert result["status"] == "success"
    assert len(plugin.db.calls) == 1
    table_name, inserted, settings = plugin.db.calls[0]
    assert table_name == "ods_hk_daily"
    assert settings == {"max_partitions_per_insert_block": 1000}
    assert inserted.loc[0, "ts_code"] == "00700.HK"
    assert "version" in inserted.columns
    assert "_ingested_at" in inserted.columns
