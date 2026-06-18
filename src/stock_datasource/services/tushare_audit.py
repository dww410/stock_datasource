"""TuShare data coverage audit - read-only inventory of current data state.

This service provides:
1. Plugin coverage check (which interfaces have plugins)
2. Table existence and row counts
3. Date range coverage (min/max date, empties)
4. Completeness metrics relative to trading calendar
"""

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from stock_datasource.models.database import db_client
from stock_datasource.services.tushare_interface_registry import (
    TuShareInterface,
    get_interface_registry,
)
from stock_datasource.utils.logger import logger


@dataclass
class TableCoverage:
    """Coverage statistics for a single table."""

    table_name: str
    plugin_name: str
    interface_name: str
    exists: bool = False
    row_count: int = 0
    is_empty: bool = True
    min_date: Optional[date] = None
    max_date: Optional[date] = None
    date_column: Optional[str] = None
    total_expected_days: int = 0
    loaded_days: int = 0
    missing_days: int = 0
    completeness_pct: float = 0.0
    latest_3y_complete: bool = False
    last_updated: Optional[datetime] = None
    schema_mismatch: bool = False
    missing_columns: list[str] = field(default_factory=list)


@dataclass
class AuditResult:
    """Complete audit result."""

    generated_at: datetime
    total_interfaces: int
    total_tables: int
    empty_tables: int
    tables_missing_plugin: int
    plugins_without_table: int
    overall_completeness_pct: float
    three_year_coverage_pct: float
    tables: list[TableCoverage]
    summary_by_category: dict[str, Any]
    summary_by_role: dict[str, Any]
    issues: list[dict[str, str]]


class TuShareAudit:
    """Read-only audit service for TuShare data coverage."""

    def __init__(self, db=None):
        self.logger = logger.bind(component="TuShareAudit")
        self.db = db or db_client
        self.registry = get_interface_registry()
        self._three_years_ago = date.today() - timedelta(days=3 * 365)

    def run_audit(self, years: Optional[int] = None) -> AuditResult:
        """Run full audit of all TuShare interfaces.

        Args:
            years: Optional limit for "recent" coverage calculation (default: 3)

        Returns:
            Complete audit result with per-table statistics
        """
        if years:
            self._three_years_ago = date.today() - timedelta(days=years * 365)

        self.logger.info(f"Starting TuShare audit (recent={years or 3}y)")

        tables: list[TableCoverage] = []
        issues: list[dict[str, str]] = []

        for iface in self.registry.list_all():
            try:
                coverage = self._audit_table(iface)
                tables.append(coverage)

                # Collect issues
                if not coverage.exists:
                    issues.append(
                        {
                            "type": "missing_table",
                            "plugin": iface.plugin_name,
                            "table": iface.table_name,
                            "message": f"Table {iface.table_name} does not exist",
                        }
                    )
                elif coverage.is_empty:
                    issues.append(
                        {
                            "type": "empty_table",
                            "plugin": iface.plugin_name,
                            "table": iface.table_name,
                            "message": f"Table {iface.table_name} is empty",
                        }
                    )
                elif coverage.completeness_pct < 50:
                    issues.append(
                        {
                            "type": "low_coverage",
                            "plugin": iface.plugin_name,
                            "table": iface.table_name,
                            "message": f"Table {iface.table_name} has only {coverage.completeness_pct:.1f}% coverage",
                        }
                    )

            except Exception as e:
                self.logger.error(f"Failed to audit {iface.plugin_name}: {e}")
                issues.append(
                    {
                        "type": "audit_error",
                        "plugin": iface.plugin_name,
                        "table": iface.table_name,
                        "message": str(e),
                    }
                )

        # Calculate aggregates
        existing_tables = [t for t in tables if t.exists]
        non_empty = [t for t in existing_tables if not t.is_empty]

        overall_completeness = (
            sum(t.completeness_pct for t in non_empty) / len(non_empty)
            if non_empty
            else 0.0
        )

        three_year_coverage = (
            sum(1 for t in non_empty if t.latest_3y_complete) / len(non_empty)
            if non_empty
            else 0.0
        )

        result = AuditResult(
            generated_at=datetime.now(),
            total_interfaces=len(tables),
            total_tables=len(existing_tables),
            empty_tables=len([t for t in existing_tables if t.is_empty]),
            tables_missing_plugin=len(
                [t for t in tables if not self.registry.get_all(t.plugin_name)]
            ),
            plugins_without_table=len([t for t in tables if not t.exists]),
            overall_completeness_pct=overall_completeness,
            three_year_coverage_pct=three_year_coverage * 100,
            tables=tables,
            summary_by_category=self._summarize_by_category(tables),
            summary_by_role=self._summarize_by_role(tables),
            issues=issues,
        )

        self.logger.info(
            f"Audit complete: {result.total_tables} tables, "
            f"{result.empty_tables} empty, "
            f"completeness={result.overall_completeness_pct:.1f}%"
        )

        return result

    def _audit_table(self, iface: TuShareInterface) -> TableCoverage:
        """Audit a single table's coverage."""
        coverage = TableCoverage(
            table_name=iface.table_name,
            plugin_name=iface.plugin_name,
            interface_name=iface.interface_name,
            date_column=iface.date_column,
        )

        # Check if table exists
        if not iface.table_name or not self.db.table_exists(iface.table_name):
            return coverage

        coverage.exists = True

        # Get row count
        try:
            result = self.db.execute_query(f"SELECT COUNT(*) as cnt FROM {iface.table_name}")
            coverage.row_count = int(result.iloc[0, 0]) if not result.empty else 0
            coverage.is_empty = coverage.row_count == 0

            if coverage.is_empty:
                return coverage
        except Exception as e:
            self.logger.warning(f"Failed to count rows in {iface.table_name}: {e}")
            return coverage

        # Get date range coverage if table has a date column
        if iface.date_column:
            try:
                coverage = self._get_date_coverage(coverage, iface)
            except Exception as e:
                self.logger.warning(f"Failed to get date coverage for {iface.table_name}: {e}")

        return coverage

    def _get_date_coverage(self, coverage: TableCoverage, iface: TuShareInterface) -> TableCoverage:
        """Calculate date-based coverage statistics."""
        table_name = iface.table_name
        date_col = iface.date_column

        # Get min/max dates
        result = self.db.execute_query(
            f"SELECT MIN({date_col}) as min_d, MAX({date_col}) as max_d FROM {table_name}"
        )

        if not result.empty:
            min_val = result.iloc[0]["min_d"]
            max_val = result.iloc[0]["max_d"]

            if isinstance(min_val, date):
                coverage.min_date = min_val
                coverage.max_date = max_val
            elif isinstance(min_val, str):
                # Try parsing YYYYMMDD string dates
                try:
                    coverage.min_date = datetime.strptime(min_val, "%Y%m%d").date()
                    coverage.max_date = datetime.strptime(max_val, "%Y%m%d").date()
                except Exception:
                    pass

        # Calculate completeness against trading calendar
        if coverage.min_date and coverage.max_date:
            try:
                # Get all trading days in range
                cal_result = self.db.execute_query(
                    "SELECT cal_date FROM ods_trade_calendar "
                    "WHERE cal_date >= %(s)s AND cal_date <= %(e)s AND is_open = 1",
                    params={"s": coverage.min_date, "e": coverage.max_date},
                )

                if not cal_result.empty:
                    coverage.total_expected_days = len(cal_result)

                    # Get actual loaded dates
                    loaded_result = self.db.execute_query(
                        f"SELECT DISTINCT {date_col} FROM {table_name} FINAL "
                        "WHERE {date_col} >= %(s)s AND {date_col} <= %(e)s",
                        params={"s": coverage.min_date, "e": coverage.max_date},
                    )

                    coverage.loaded_days = len(loaded_result) if not loaded_result.empty else 0
                    coverage.missing_days = coverage.total_expected_days - coverage.loaded_days

                    if coverage.total_expected_days > 0:
                        coverage.completeness_pct = (
                            coverage.loaded_days / coverage.total_expected_days * 100
                        )

                    # Check 3-year recency
                    three_year_start = self._three_years_ago
                    if coverage.max_date:
                        three_year_end = min(coverage.max_date, date.today())

                        # Get trading days in 3-year window
                        cal_3y = self.db.execute_query(
                            "SELECT cal_date FROM ods_trade_calendar "
                            "WHERE cal_date >= %(s)s AND cal_date <= %(e)s AND is_open = 1",
                            params={"s": three_year_start, "e": three_year_end},
                        )

                        if not cal_3y.empty:
                            total_3y = len(cal_3y)

                            loaded_3y_result = self.db.execute_query(
                                f"SELECT DISTINCT {date_col} FROM {table_name} FINAL "
                                "WHERE {date_col} >= %(s)s AND {date_col} <= %(e)s",
                                params={"s": three_year_start, "e": three_year_end},
                            )

                            loaded_3y = len(loaded_3y_result) if not loaded_3y_result.empty else 0
                            coverage.latest_3y_complete = loaded_3y >= total_3y * 0.95  # 95% threshold

            except Exception as e:
                self.logger.warning(f"Failed to calculate completeness for {table_name}: {e}")

        return coverage

    def _summarize_by_category(self, tables: list[TableCoverage]) -> dict[str, Any]:
        """Generate summary statistics grouped by category."""
        result: dict[str, Any] = {}

        for iface in self.registry.list_all():
            cat = iface.category
            if cat not in result:
                result[cat] = {
                    "count": 0,
                    "tables_exist": 0,
                    "empty": 0,
                    "avg_completeness": 0.0,
                }

            table = next((t for t in tables if t.plugin_name == iface.plugin_name), None)
            result[cat]["count"] += 1

            if table:
                if table.exists:
                    result[cat]["tables_exist"] += 1
                if table.is_empty:
                    result[cat]["empty"] += 1
                result[cat]["avg_completeness"] += table.completeness_pct

        # Calculate averages
        for cat in result:
            total = result[cat]["tables_exist"] - result[cat]["empty"]
            if total > 0:
                result[cat]["avg_completeness"] /= total

        return result

    def _summarize_by_role(self, tables: list[TableCoverage]) -> dict[str, Any]:
        """Generate summary statistics grouped by role."""
        result: dict[str, Any] = {}

        for iface in self.registry.list_all():
            role = iface.role
            if role not in result:
                result[role] = {
                    "count": 0,
                    "tables_exist": 0,
                    "empty": 0,
                    "avg_completeness": 0.0,
                }

            table = next((t for t in tables if t.plugin_name == iface.plugin_name), None)
            result[role]["count"] += 1

            if table:
                if table.exists:
                    result[role]["tables_exist"] += 1
                if table.is_empty:
                    result[role]["empty"] += 1
                result[role]["avg_completeness"] += table.completeness_pct

        # Calculate averages
        for role in result:
            total = result[role]["tables_exist"] - result[role]["empty"]
            if total > 0:
                result[role]["avg_completeness"] /= total

        return result

    def get_empty_tables(self) -> list[dict[str, str]]:
        """Get list of empty or non-existent tables."""
        audit = self.run_audit()
        result = []

        for t in audit.tables:
            if not t.exists or t.is_empty:
                result.append(
                    {
                        "plugin_name": t.plugin_name,
                        "table_name": t.table_name,
                        "status": "missing" if not t.exists else "empty",
                    }
                )

        return result

    def get_low_coverage_tables(self, threshold_pct: float = 50.0) -> list[dict[str, Any]]:
        """Get tables with coverage below the given threshold."""
        audit = self.run_audit()
        result = []

        for t in audit.tables:
            if t.exists and not t.is_empty and t.completeness_pct < threshold_pct:
                result.append(
                    {
                        "plugin_name": t.plugin_name,
                        "table_name": t.table_name,
                        "completeness_pct": t.completeness_pct,
                        "loaded_days": t.loaded_days,
                        "missing_days": t.missing_days,
                        "min_date": t.min_date,
                        "max_date": t.max_date,
                    }
                )

        return sorted(result, key=lambda x: x["completeness_pct"])

    def get_all_empty_tables(self) -> list[dict[str, Any]]:
        """List ALL empty tables in the database (not just plugin tables).

        Returns list of dicts with table_name, engine, and plugin status.
        """
        result = []

        try:
            # Get all empty tables from ClickHouse
            rows = self.db.execute_query(
                "SELECT name, engine FROM system.tables "
                "WHERE database=currentDatabase() AND total_rows=0 "
                "ORDER BY name"
            )

            plugin_table_names = {iface.table_name for iface in self.registry.list_all()}

            for _, row in rows.iterrows():
                table_name = row["name"]
                is_plugin_table = table_name in plugin_table_names

                result.append({
                    "table_name": table_name,
                    "engine": row["engine"],
                    "is_plugin_table": is_plugin_table,
                    "plugin_name": self._get_plugin_for_table(table_name),
                })

        except Exception as e:
            self.logger.warning(f"Failed to list empty tables: {e}")

        return result

    def _get_plugin_for_table(self, table_name: str) -> Optional[str]:
        """Find the plugin that corresponds to a given table name."""
        for iface in self.registry.list_all():
            if iface.table_name == table_name:
                return iface.plugin_name
        return None

    def get_latest_dates(self, limit: int = 5) -> dict[str, Any]:
        """Get latest dates for key time-series tables.

        Replacement for check_dates.py functionality.
        """
        result = {
            "calendar_latest": None,
            "table_dates": [],
        }

        try:
            # Get latest calendar date
            cal_df = self.db.execute_query(
                "SELECT MAX(cal_date) as latest FROM ods_trade_calendar WHERE is_open=1"
            )
            if not cal_df.empty:
                result["calendar_latest"] = cal_df.iloc[0]["latest"]

            # Check key time-series tables
            key_tables = [
                ("ods_daily", "trade_date"),
                ("ods_daily_info", "trade_date"),
                ("ods_weekly", "trade_date"),
                ("ods_balancesheet", "end_date"),
                ("ods_income", "end_date"),
            ]

            for table_name, date_col in key_tables:
                try:
                    df = self.db.execute_query(
                        f"SELECT DISTINCT {date_col} as d FROM {table_name} "
                        f"ORDER BY {date_col} DESC LIMIT {limit}"
                    )
                    if not df.empty:
                        dates = df["d"].tolist()
                        result["table_dates"].append({
                            "table": table_name,
                            "latest": dates[0] if dates else None,
                            "recent": dates,
                        })
                except Exception as e:
                    result["table_dates"].append({
                        "table": table_name,
                        "latest": None,
                        "error": str(e),
                    })

        except Exception as e:
            self.logger.warning(f"Failed to get latest dates: {e}")

        return result


# Global audit instance
_audit_instance: Optional[TuShareAudit] = None


def get_tushare_audit() -> TuShareAudit:
    """Get or create the global audit service."""
    global _audit_instance
    if _audit_instance is None:
        _audit_instance = TuShareAudit()
    return _audit_instance
