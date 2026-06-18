import importlib
import sys
import time
import types
from unittest.mock import Mock

import pandas as pd


def _load_plugin_with_stubbed_extractor(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    module_name = "stock_datasource.plugins.tushare_income.plugin"
    extractor_name = "stock_datasource.plugins.tushare_income.extractor"
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    monkeypatch.setitem(
        sys.modules,
        extractor_name,
        types.SimpleNamespace(extractor=types.SimpleNamespace(extract=Mock())),
    )
    module = importlib.import_module(module_name)
    return module, module.TuShareIncomePlugin


class FakeDB:
    def execute_query(self, query):
        return pd.DataFrame(
            {
                "ts_code": [
                    "000001.SZ",
                    "000002.SZ",
                    "000003.SZ",
                    "000004.SZ",
                    "000005.SZ",
                ]
            }
        )


def test_income_batch_applies_shard_and_max_stocks(monkeypatch):
    calls = []

    def fake_extract(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame(
            {
                "ts_code": [kwargs["ts_code"]],
                "end_date": ["20231231"],
                "total_revenue": [1],
            }
        )

    income_plugin_module, plugin_cls = _load_plugin_with_stubbed_extractor(monkeypatch)
    income_plugin_module.extractor.extract = fake_extract
    monkeypatch.setattr(time, "sleep", Mock())

    plugin = plugin_cls()
    plugin.db = FakeDB()

    data = plugin.extract_data(
        start_date="20200101",
        end_date="20231231",
        period="20231231",
        report_type="1",
        max_stocks=2,
        shard_index=1,
        shard_count=2,
    )

    assert data["ts_code"].tolist() == ["000002.SZ", "000004.SZ"]
    assert calls == [
        {
            "ts_code": "000002.SZ",
            "start_date": "20200101",
            "end_date": "20231231",
            "period": "20231231",
            "report_type": "1",
        },
        {
            "ts_code": "000004.SZ",
            "start_date": "20200101",
            "end_date": "20231231",
            "period": "20231231",
            "report_type": "1",
        },
    ]


def test_income_batch_uses_trade_date_as_end_date(monkeypatch):
    calls = []

    def fake_extract(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame({"ts_code": [kwargs["ts_code"]], "end_date": ["20260617"]})

    income_plugin_module, plugin_cls = _load_plugin_with_stubbed_extractor(monkeypatch)
    income_plugin_module.extractor.extract = fake_extract
    monkeypatch.setattr(time, "sleep", Mock())

    plugin = plugin_cls()
    plugin.db = FakeDB()

    plugin.extract_data(trade_date="2026-06-17", max_stocks=1)

    assert calls == [
        {
            "ts_code": "000001.SZ",
            "start_date": None,
            "end_date": "20260617",
            "period": None,
            "report_type": None,
        }
    ]
