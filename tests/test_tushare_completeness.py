"""Tests for TuShare completeness planning service."""

import json
import tempfile
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from stock_datasource.services.tushare_completeness import (
    CompletenessPlan,
    MissingWindow,
    PluginCallSpec,
    PluginCompleteness,
    TuShareCompletenessPlanner,
    get_completeness_planner,
)
from stock_datasource.services.tushare_interface_registry import TuShareInterface


class TestTuShareCompletenessPlanner:
    """Tests for the completeness planner."""

    def test_planner_initialization(self):
        """Planner should initialize without errors."""
        planner = TuShareCompletenessPlanner()
        assert planner is not None

    def test_create_missing_window(self):
        """Should calculate missing window statistics correctly."""
        planner = TuShareCompletenessPlanner()

        start = date(2024, 1, 1)
        end = date(2024, 1, 10)
        overall_min = date(2024, 1, 1)
        overall_max = date(2024, 1, 31)
        loaded_set = {date(2024, 1, 3), date(2024, 1, 4)}

        window = planner._create_missing_window(start, end, overall_min, overall_max, loaded_set)

        assert window.start_date == start
        assert window.end_date == end
        assert window.loaded_days == 2

    def test_get_actions_by_priority(self):
        """Should group actions by priority level."""
        planner = TuShareCompletenessPlanner()

        plan = CompletenessPlan(
            generated_at=datetime.now(),
            reference_date=date.today(),
            three_year_start=date.today() - timedelta(days=3 * 365),
            total_plugins=3,
            tables_with_data=3,
            empty_tables=0,
            overall_completeness_pct=90.0,
            plugins_needing_backfill=1,
            plugins_needing_initial_load=0,
            plugin_reports=[],
            all_recommended_actions=[
                PluginCallSpec(
                    plugin_name="p1",
                    operation="run_backfill",
                    params={},
                    reason="test1",
                    priority=1,
                ),
                PluginCallSpec(
                    plugin_name="p2",
                    operation="run",
                    params={},
                    reason="test2",
                    priority=2,
                ),
                PluginCallSpec(
                    plugin_name="p3",
                    operation="run_backfill",
                    params={},
                    reason="test3",
                    priority=1,
                ),
            ],
            total_estimated_minutes=3.0,
        )

        by_priority = planner.get_actions_by_priority(plan)

        assert 1 in by_priority
        assert 2 in by_priority
        assert len(by_priority[1]) == 2
        assert len(by_priority[2]) == 1

    def test_save_plan(self):
        """Should save plan to JSON file."""
        planner = TuShareCompletenessPlanner()

        report = PluginCompleteness(
            plugin_name="test_plugin",
            interface_name="test_api",
            table_name="ods_test",
            table_exists=True,
            is_empty=False,
            completeness_pct=95.0,
            recommended_actions=[
                PluginCallSpec(
                    plugin_name="test_plugin",
                    operation="run_backfill",
                    params={"start_date": "20240101", "end_date": "20240131"},
                    reason="Test backfill",
                    priority=1,
                )
            ],
        )

        plan = CompletenessPlan(
            generated_at=datetime.now(),
            reference_date=date.today(),
            three_year_start=date.today() - timedelta(days=3 * 365),
            total_plugins=1,
            tables_with_data=1,
            empty_tables=0,
            overall_completeness_pct=95.0,
            plugins_needing_backfill=0,
            plugins_needing_initial_load=0,
            plugin_reports=[report],
            all_recommended_actions=report.recommended_actions,
            total_estimated_minutes=1.0,
        )

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        try:
            planner.save_plan(plan, path)

            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)

            assert saved["total_plugins"] == 1
            assert saved["overall_completeness_pct"] == 95.0
            assert saved["plugin_reports"][0]["plugin_name"] == "test_plugin"
            assert saved["plugin_reports"][0]["recommended_actions"][0]["priority"] == 1

        finally:
            import os

            os.unlink(path)


class TestMissingWindow:
    """Tests for MissingWindow dataclass."""

    def test_window_properties(self):
        """Window should have correct calculated properties."""
        window = MissingWindow(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 10),
            total_days=8,
            loaded_days=6,
            missing_days=2,
            completeness_pct=75.0,
        )

        assert window.completeness_pct == 75.0
        assert window.missing_days == 2


class TestPluginCallSpec:
    """Tests for PluginCallSpec dataclass."""

    def test_spec_properties(self):
        """Spec should store all required information."""
        spec = PluginCallSpec(
            plugin_name="tushare_daily",
            operation="run_backfill",
            params={"start_date": "20240101", "end_date": "20240131"},
            reason="Missing recent data",
            priority=1,
            estimated_minutes=5.0,
        )

        assert spec.plugin_name == "tushare_daily"
        assert spec.operation == "run_backfill"
        assert spec.params["start_date"] == "20240101"
        assert spec.priority == 1


class TestGetCompletenessPlanner:
    """Tests for singleton accessor."""

    def test_returns_same_instance(self):
        """Should return the same instance on repeated calls."""
        planner1 = get_completeness_planner()
        planner2 = get_completeness_planner()
        assert planner1 is planner2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
