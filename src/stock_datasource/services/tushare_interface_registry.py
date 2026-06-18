"""TuShare interface registry - canonical inventory of all TuShare interfaces, plugins, and tables."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from stock_datasource.core.plugin_manager import plugin_manager
from stock_datasource.utils.logger import logger


@dataclass
class TuShareInterface:
    """Represents a single TuShare API interface and its plugin mapping."""

    interface_name: str  # TuShare API name (e.g., "daily", "daily_basic")
    plugin_name: str  # Plugin name (e.g., "tushare_daily", "tushare_daily_basic")
    table_name: str  # Target ClickHouse table name
    category: str  # Plugin category (cn_stock, index, etc.)
    role: str  # Plugin role (primary, basic, derived, auxiliary)
    description: str = ""
    api_rate_limit: int = 120
    required_params: list[str] = field(default_factory=list)
    optional_params: list[str] = field(default_factory=list)
    date_column: Optional[str] = None  # Primary date column (e.g., "trade_date", "ann_date")
    three_year_limit: bool = True  # Whether new loads should use 3-year limit
    plugin_enabled: bool = True  # Whether plugin exists and is enabled
    plugin_exists: bool = True  # Whether plugin directory exists
    sync_mode: str = "incremental"  # incremental or full_replace
    default_start_date: Optional[str] = None
    default_end_date: Optional[str] = None


class TuShareInterfaceRegistry:
    """Registry of all TuShare interfaces and their plugin mappings."""

    def __init__(self, plugins_dir: Optional[Path] = None):
        self.logger = logger.bind(component="TuShareInterfaceRegistry")
        self._interfaces: dict[str, TuShareInterface] = {}
        self._plugins_dir = plugins_dir or Path(__file__).parent.parent / "plugins"
        self._load_from_plugins()
        self._add_known_missing_interfaces()

    def _load_from_plugins(self) -> None:
        """Load interface metadata from existing tushare_* plugins."""
        # Ensure plugins are discovered
        if not plugin_manager.plugins:
            plugin_manager.discover_plugins()
        plugin_names = plugin_manager.list_plugins()

        for plugin_name in plugin_names:
            if not plugin_name.startswith("tushare_"):
                continue

            try:
                plugin = plugin_manager.get_plugin(plugin_name)
                if not plugin:
                    continue

                config = plugin.get_config()
                schema = plugin.get_schema()
                api_name = config.get("api_name", plugin_name.replace("tushare_", ""))

                interface = TuShareInterface(
                    interface_name=api_name,
                    plugin_name=plugin_name,
                    table_name=schema.get("table_name", "") if schema else "",
                    category=plugin.get_category().value,
                    role=plugin.get_role().value,
                    description=config.get("description", ""),
                    api_rate_limit=plugin.get_rate_limit(),
                    required_params=self._extract_required_params(plugin),
                    optional_params=self._extract_optional_params(plugin),
                    date_column=self._detect_date_column(schema),
                    three_year_limit=self._should_use_three_year_limit(plugin, schema),
                    plugin_enabled=plugin.is_enabled(),
                    plugin_exists=True,
                    sync_mode=plugin.get_sync_mode(),
                    default_start_date=config.get("default_start_date"),
                    default_end_date=config.get("default_end_date"),
                )

                self._interfaces[plugin_name] = interface
                self.logger.debug(f"Registered interface: {api_name} -> {plugin_name}")

            except Exception as e:
                self.logger.warning(f"Failed to load plugin {plugin_name}: {e}")

        self.logger.info(f"Loaded {len(self._interfaces)} interfaces from plugins")

    def _extract_required_params(self, plugin: Any) -> list[str]:
        """Extract required parameters from plugin config or extractor signature."""
        params = []
        try:
            config = plugin.get_config()
            param_schema = config.get("parameters_schema", {})
            for name, props in param_schema.items():
                if props.get("required", False):
                    params.append(name)
        except Exception:
            pass
        return params

    def _extract_optional_params(self, plugin: Any) -> list[str]:
        """Extract optional parameters from plugin config."""
        params = []
        try:
            config = plugin.get_config()
            param_schema = config.get("parameters_schema", {})
            for name, props in param_schema.items():
                if not props.get("required", False):
                    params.append(name)
        except Exception:
            pass
        return params

    def _detect_date_column(self, schema: Optional[dict[str, Any]]) -> Optional[str]:
        """Detect the primary date column from schema."""
        if not schema:
            return None

        date_columns = ["trade_date", "ann_date", "end_date", "date", "cal_date", "report_date"]
        columns = schema.get("columns", [])
        col_names = [c.get("name", "") for c in columns]

        for date_col in date_columns:
            if date_col in col_names:
                return date_col

        return None

    def _should_use_three_year_limit(self, plugin: Any, schema: Optional[dict[str, Any]]) -> bool:
        """Determine if interface should use 3-year limit for new loads.

        Reference/dimension tables with no natural date range get full loads.
        Time-series data gets 3-year limit for new loads.
        """
        role = plugin.get_role().value
        category = plugin.get_category().value

        # Reference data - full load (no 3-year limit)
        if role == "auxiliary" or category in ("reference", "system"):
            return False

        # Dimension tables - full load
        if role == "basic":
            return False

        # All others use 3-year limit
        return True

    def _add_known_missing_interfaces(self) -> None:
        """Add known TuShare interfaces that don't yet have plugins.

        These are interfaces we want to implement eventually.
        The probe service will determine which actually return data.
        """
        # This is intentionally left mostly empty - the probe
        # will discover available interfaces from the API itself
        # based on what actually returns data
        pass

    def get_all(self, plugin_name: str) -> Optional[TuShareInterface]:
        """Get interface by plugin name."""
        return self._interfaces.get(plugin_name)

    def get_by_interface_name(self, interface_name: str) -> Optional[TuShareInterface]:
        """Get interface by TuShare API name."""
        for iface in self._interfaces.values():
            if iface.interface_name == interface_name:
                return iface
        return None

    def list_all(self) -> list[TuShareInterface]:
        """List all registered interfaces."""
        return list(self._interfaces.values())

    def list_missing_plugins(self) -> list[TuShareInterface]:
        """List interfaces where plugin does not exist or is disabled."""
        return [
            iface
            for iface in self._interfaces.values()
            if not iface.plugin_exists or not iface.plugin_enabled
        ]

    def list_by_category(self, category: str) -> list[TuShareInterface]:
        """List interfaces by category."""
        return [iface for iface in self._interfaces.values() if iface.category == category]

    def list_by_role(self, role: str) -> list[TuShareInterface]:
        """List interfaces by role."""
        return [iface for iface in self._interfaces.values() if iface.role == role]

    def to_dict(self) -> dict[str, Any]:
        """Convert registry to dictionary format for reporting."""
        result: dict[str, Any] = {
            "total_interfaces": len(self._interfaces),
            "by_category": {},
            "by_role": {},
            "interfaces": [],
        }

        for iface in self._interfaces.values():
            if iface.category not in result["by_category"]:
                result["by_category"][iface.category] = 0
            result["by_category"][iface.category] += 1

            if iface.role not in result["by_role"]:
                result["by_role"][iface.role] = 0
            result["by_role"][iface.role] += 1

            result["interfaces"].append(
                {
                    "interface_name": iface.interface_name,
                    "plugin_name": iface.plugin_name,
                    "table_name": iface.table_name,
                    "category": iface.category,
                    "role": iface.role,
                    "description": iface.description,
                    "plugin_enabled": iface.plugin_enabled,
                    "three_year_limit": iface.three_year_limit,
                    "date_column": iface.date_column,
                }
            )

        return result


# Global registry instance
_interface_registry: Optional[TuShareInterfaceRegistry] = None


def get_interface_registry() -> TuShareInterfaceRegistry:
    """Get or create the global interface registry."""
    global _interface_registry
    if _interface_registry is None:
        _interface_registry = TuShareInterfaceRegistry()
    return _interface_registry
