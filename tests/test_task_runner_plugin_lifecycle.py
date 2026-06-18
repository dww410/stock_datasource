"""Tests for TaskRunner plugin lifecycle integration."""

import os

os.environ.setdefault("TUSHARE_TOKEN", "test-token")

from stock_datasource.core.task_runner import TaskRunner


class FakePluginManager:
    def __init__(self, plugin):
        self.plugin = plugin

    def get_plugin(self, plugin_name):
        if plugin_name == self.plugin.name:
            return self.plugin
        return None


class NoopSchemaManager:
    def create_table_from_schema(self, schema):
        return None


class LifecyclePlugin:
    name = "lifecycle_plugin"

    def __init__(self):
        self.load_called = False
        self.run_kwargs = None

    def get_schema(self):
        return {
            "table_name": "plugin_specific_table_not_generic_loader",
            "columns": [],
        }

    def run(self, **kwargs):
        self.run_kwargs = kwargs
        self.load_called = True
        return {
            "plugin": self.name,
            "status": "success",
            "parameters": kwargs,
            "steps": {
                "extract": {"status": "success", "records": 3},
                "validate": {"status": "success"},
                "transform": {"status": "success", "records": 2},
                "load": {"status": "success", "loaded_records": 2},
            },
        }

    def extract_data(self, **kwargs):
        return [{"legacy_path": True}]

    def validate_data(self, data):
        return True

    def transform_data(self, data):
        return data


def run_lifecycle_task(plugin):
    runner = TaskRunner()
    runner.plugin_manager = FakePluginManager(plugin)
    runner.schema_manager = NoopSchemaManager()

    def fail_if_generic_loader_is_used(table_name, data):
        raise AssertionError(
            f"TaskRunner generic loader should not load {table_name}: {data}"
        )

    runner._load_data = fail_if_generic_loader_is_used

    return runner._run_plugin_task(
        {"name": "lifecycle task", "plugin_name": plugin.name},
        "task-123",
        trade_date="20260123",
    )


def test_plugin_task_uses_plugin_run_lifecycle_and_load_result_records():
    """Plugin tasks should delegate to plugin.run so plugin load_data is used."""
    plugin = LifecyclePlugin()

    result = run_lifecycle_task(plugin)

    assert plugin.load_called is True
    assert plugin.run_kwargs == {"trade_date": "20260123"}
    assert result["task_id"] == "task-123"
    assert result["task_name"] == "lifecycle task"
    assert result["status"] == "success"
    assert result["records_processed"] == 2
    assert result["error"] is None
    assert "start_time" in result
    assert "end_time" in result
    assert result["duration_seconds"] >= 0


def test_records_processed_uses_first_available_plugin_lifecycle_count():
    """loaded_records takes priority even when it is zero."""
    plugin = LifecyclePlugin()
    original_run = plugin.run

    def run_with_zero_loaded_records(**kwargs):
        result = original_run(**kwargs)
        result["steps"]["load"] = {"status": "success", "loaded_records": 0, "total_records": 5}
        result["steps"]["transform"] = {"status": "success", "records": 7}
        return result

    plugin.run = run_with_zero_loaded_records

    result = run_lifecycle_task(plugin)

    assert result["records_processed"] == 0
