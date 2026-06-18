"""TuShare completeness planning service.

Calculates data completeness and produces backfill plans:
1. For existing tables: find missing date windows
2. For new tables: plan 3-year initial loads
3. Produce plugin call specs with parameters for execution
"""

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional

import pandas as pd

from stock_datasource.core.trade_calendar import trade_calendar_service
from stock_datasource.services.tushare_interface_registry import (
    TuShareInterface,
    get_interface_registry,
)
from stock_datasource.services.tushare_audit import TuShareAudit, get_tushare_audit
from stock_datasource.utils.logger import logger


@dataclass
class MissingWindow:
    """A date window that has missing data."""

    start_date: date
    end_date: date
    total_days: int
    loaded_days: int
    missing_days: int
    completeness_pct: float


@dataclass
class PluginCallSpec:
    """Specification for a plugin run call."""

    plugin_name: str
    operation: str  # "run", "run_backfill", "full_reload"
    params: dict[str, Any]
    reason: str
    priority: int  # 1=highest, 5=lowest
    expected_rows: Optional[int] = None
    estimated_minutes: float = 1.0


@dataclass
class PluginCompleteness:
    """Completeness report for a single plugin/table."""

    plugin_name: str
    interface_name: str
    table_name: str
    table_exists: bool
    is_empty: bool
    date_column: Optional[str] = None
    min_date: Optional[date] = None
    max_date: Optional[date] = None
    total_expected_days: int = 0
    loaded_days: int = 0
    missing_days: int = 0
    completeness_pct: float = 0.0
    missing_windows: list[MissingWindow] = field(default_factory=list)
    recommended_actions: list[PluginCallSpec] = field(default_factory=list)


@dataclass
class CompletenessPlan:
    """Complete data completeness plan."""

    generated_at: datetime
    reference_date: date
    three_year_start: date
    total_plugins: int
    tables_with_data: int
    empty_tables: int
    overall_completeness_pct: float
    plugins_needing_backfill: int
    plugins_needing_initial_load: int
    plugin_reports: list[PluginCompleteness]
    all_recommended_actions: list[PluginCallSpec]
    total_estimated_minutes: float


class TuShareCompletenessPlanner:
    """Plans backfill and initial data loads based on completeness analysis."""

    def __init__(self, audit: Optional[TuShareAudit] = None):
        self.logger = logger.bind(component="TuShareCompletenessPlanner")
        self.audit = audit or get_tushare_audit()
        self.registry = self.audit.registry
        self._calendar = trade_calendar_service

    def generate_plan(
        self,
        years: int = 3,
        include_low_priority: bool = False,
        min_completeness_threshold: float = 95.0,
    ) -> CompletenessPlan:
        """Generate a complete data completeness plan.

        Args:
            years: Number of years to consider for "recent" data
            include_low_priority: Include low-priority (very old) backfills
            min_completeness_threshold: Minimum acceptable completeness percentage

        Returns:
            Complete plan with per-plugin analysis and recommendations
        """
        reference_date = date.today()
        three_year_start = reference_date - timedelta(days=years * 365)

        self.logger.info(
            f"Generating completeness plan (reference={reference_date}, "
            f"{years}y window starts={three_year_start})"
        )

        # Run audit to get current data state
        audit_result = self.audit.run_audit(years=years)

        plugin_reports: list[PluginCompleteness] = []
        all_actions: list[PluginCallSpec] = []

        for table_coverage in audit_result.tables:
            iface = self.registry.get_all(table_coverage.plugin_name)
            if not iface:
                continue

            report = self._analyze_plugin(
                iface=iface,
                table_coverage=table_coverage,
                three_year_start=three_year_start,
                reference_date=reference_date,
                min_threshold=min_completeness_threshold,
                include_low_priority=include_low_priority,
            )

            plugin_reports.append(report)
            all_actions.extend(report.recommended_actions)

        # Calculate summary stats
        tables_with_data = sum(1 for r in plugin_reports if not r.is_empty and r.table_exists)
        empty_tables = sum(1 for r in plugin_reports if r.is_empty and r.table_exists)

        overall_completeness = (
            sum(r.completeness_pct for r in plugin_reports if r.table_exists and not r.is_empty)
            / tables_with_data
            if tables_with_data > 0
            else 0.0
        )

        needing_backfill = sum(
            1
            for r in plugin_reports
            if r.table_exists and not r.is_empty and r.completeness_pct < min_completeness_threshold
        )

        needing_initial_load = sum(
            1
            for r in plugin_reports
            if not r.table_exists or r.is_empty
        )

        total_estimated = sum(a.estimated_minutes for a in all_actions)

        plan = CompletenessPlan(
            generated_at=datetime.now(),
            reference_date=reference_date,
            three_year_start=three_year_start,
            total_plugins=len(plugin_reports),
            tables_with_data=tables_with_data,
            empty_tables=empty_tables,
            overall_completeness_pct=overall_completeness,
            plugins_needing_backfill=needing_backfill,
            plugins_needing_initial_load=needing_initial_load,
            plugin_reports=plugin_reports,
            all_recommended_actions=all_actions,
            total_estimated_minutes=total_estimated,
        )

        self.logger.info(
            f"Plan generated: {needing_backfill} need backfill, "
            f"{needing_initial_load} need initial load, "
            f"{len(all_actions)} total actions, "
            f"est. {total_estimated:.1f} minutes"
        )

        return plan

    def _analyze_plugin(
        self,
        iface: TuShareInterface,
        table_coverage: Any,
        three_year_start: date,
        reference_date: date,
        min_threshold: float,
        include_low_priority: bool,
    ) -> PluginCompleteness:
        """Analyze a single plugin's data completeness."""
        report = PluginCompleteness(
            plugin_name=iface.plugin_name,
            interface_name=iface.interface_name,
            table_name=iface.table_name,
            table_exists=table_coverage.exists,
            is_empty=table_coverage.is_empty,
            date_column=iface.date_column,
        )

        if not report.table_exists or report.is_empty:
            # Recommend initial load
            if iface.three_year_limit:
                # 3-year limited load for time-series
                report.recommended_actions.append(
                    PluginCallSpec(
                        plugin_name=iface.plugin_name,
                        operation="run_backfill",
                        params={
                            "start_date": three_year_start.strftime("%Y%m%d"),
                            "end_date": reference_date.strftime("%Y%m%d"),
                        },
                        reason=f"Initial 3-year load (empty or missing table)",
                        priority=2,
                        estimated_minutes=5.0,
                    )
                )
            else:
                # Full reload for reference/dimension tables
                report.recommended_actions.append(
                    PluginCallSpec(
                        plugin_name=iface.plugin_name,
                        operation="run",
                        params={},
                        reason=f"Full reload (reference/dimension table)",
                        priority=3,
                        estimated_minutes=1.0,
                    )
                )
            return report

        # Copy coverage metrics
        report.min_date = table_coverage.min_date
        report.max_date = table_coverage.max_date
        report.total_expected_days = table_coverage.total_expected_days
        report.loaded_days = table_coverage.loaded_days
        report.missing_days = table_coverage.missing_days
        report.completeness_pct = table_coverage.completeness_pct

        # Only analyze if we have date coverage
        if report.min_date and report.max_date and iface.date_column:
            # Find missing date windows
            report.missing_windows = self._find_missing_windows(
                iface=iface,
                min_date=report.min_date,
                max_date=report.max_date,
            )

            # Generate backfill recommendations
            if report.completeness_pct < min_threshold:
                backfill_actions = self._generate_backfill_actions(
                    iface=iface,
                    report=report,
                    three_year_start=three_year_start,
                    include_low_priority=include_low_priority,
                )
                report.recommended_actions.extend(backfill_actions)

        return report

    def _find_missing_windows(
        self,
        iface: TuShareInterface,
        min_date: date,
        max_date: date,
    ) -> list[MissingWindow]:
        """Find contiguous windows of missing dates.

        Queries the actual table to find which dates are missing.
        """
        windows: list[MissingWindow] = []

        if not iface.date_column or not self.audit.db:
            return windows

        try:
            # Get all trading days in range
            cal_days = self._calendar.get_trading_days_between(min_date, max_date)
            if not cal_days:
                return windows

            # Get actually loaded days
            loaded_df = self.audit.db.execute_query(
                f"SELECT DISTINCT {iface.date_column} as d FROM {iface.table_name} FINAL "
                "WHERE {iface.date_column} >= %(s)s AND {iface.date_column} <= %(e)s",
                params={"s": min_date, "e": max_date},
            )

            loaded_set = set()
            if not loaded_df.empty:
                for d in loaded_df["d"]:
                    if isinstance(d, str):
                        try:
                            loaded_set.add(datetime.strptime(d, "%Y%m%d").date())
                        except ValueError:
                            pass
                    else:
                        loaded_set.add(d)

            # Find missing days
            missing_days = [d for d in cal_days if d not in loaded_set]

            # Group into contiguous windows
            if missing_days:
                current_start = missing_days[0]
                current_end = missing_days[0]

                for d in missing_days[1:]:
                    if (d - current_end).days == 1:
                        current_end = d
                    else:
                        windows.append(
                            self._create_missing_window(
                                current_start, current_end, min_date, max_date, loaded_set
                            )
                        )
                        current_start = d
                        current_end = d

                # Add final window
                windows.append(
                    self._create_missing_window(
                        current_start, current_end, min_date, max_date, loaded_set
                    )
                )

        except Exception as e:
            self.logger.warning(
                f"Failed to find missing windows for {iface.plugin_name}: {e}"
            )

        return windows

    def _create_missing_window(
        self,
        start: date,
        end: date,
        overall_min: date,
        overall_max: date,
        loaded_set: set[date],
    ) -> MissingWindow:
        """Create a MissingWindow with completeness calculation."""
        window_days = self._calendar.get_trading_days_between(start, end)
        window_loaded = [d for d in window_days if d in loaded_set]

        return MissingWindow(
            start_date=start,
            end_date=end,
            total_days=len(window_days),
            loaded_days=len(window_loaded),
            missing_days=len(window_days) - len(window_loaded),
            completeness_pct=(
                len(window_loaded) / len(window_days) * 100 if window_days else 0.0
            ),
        )

    def _generate_backfill_actions(
        self,
        iface: TuShareInterface,
        report: PluginCompleteness,
        three_year_start: date,
        include_low_priority: bool,
    ) -> list[PluginCallSpec]:
        """Generate plugin call specs for backfilling missing data."""
        actions: list[PluginCallSpec] = []

        # Group missing windows into date ranges
        # Prioritize recent data (last 3 years)
        recent_windows: list[MissingWindow] = []
        old_windows: list[MissingWindow] = []

        for window in report.missing_windows:
            if window.end_date >= three_year_start:
                recent_windows.append(window)
            else:
                old_windows.append(window)

        # Generate backfill actions - batch by year to keep requests reasonable
        if recent_windows:
            # Combine recent windows into a single backfill
            recent_start = min(w.start_date for w in recent_windows)
            recent_end = max(w.end_date for w in recent_windows)

            total_missing = sum(w.missing_days for w in recent_windows)

            actions.append(
                PluginCallSpec(
                    plugin_name=iface.plugin_name,
                    operation="run_backfill",
                    params={
                        "start_date": recent_start.strftime("%Y%m%d"),
                        "end_date": recent_end.strftime("%Y%m%d"),
                    },
                    reason=f"Backfill {total_missing} missing recent days "
                    f"({recent_start} to {recent_end})",
                    priority=1,
                    estimated_minutes=max(1.0, total_missing / 100.0),
                )
            )

        # Old windows - only if requested
        if old_windows and include_low_priority:
            old_start = min(w.start_date for w in old_windows)
            old_end = max(w.end_date for w in old_windows)
            total_missing = sum(w.missing_days for w in old_windows)

            actions.append(
                PluginCallSpec(
                    plugin_name=iface.plugin_name,
                    operation="run_backfill",
                    params={
                        "start_date": old_start.strftime("%Y%m%d"),
                        "end_date": old_end.strftime("%Y%m%d"),
                    },
                    reason=f"Backfill {total_missing} missing older days "
                    f"({old_start} to {old_end}) [low priority]",
                    priority=5,
                    estimated_minutes=max(1.0, total_missing / 100.0),
                )
            )

        return actions

    def get_actions_by_priority(self, plan: CompletenessPlan) -> dict[int, list[PluginCallSpec]]:
        """Group actions by priority level."""
        result: dict[int, list[PluginCallSpec]] = {}
        for action in plan.all_recommended_actions:
            if action.priority not in result:
                result[action.priority] = []
            result[action.priority].append(action)
        return result

    def save_plan(self, plan: CompletenessPlan, path: str) -> None:
        """Save completeness plan to JSON file."""
        data = {
            "generated_at": plan.generated_at.isoformat(),
            "reference_date": plan.reference_date.isoformat(),
            "three_year_start": plan.three_year_start.isoformat(),
            "total_plugins": plan.total_plugins,
            "tables_with_data": plan.tables_with_data,
            "empty_tables": plan.empty_tables,
            "overall_completeness_pct": plan.overall_completeness_pct,
            "plugins_needing_backfill": plan.plugins_needing_backfill,
            "plugins_needing_initial_load": plan.plugins_needing_initial_load,
            "total_estimated_minutes": plan.total_estimated_minutes,
            "plugin_reports": [
                {
                    "plugin_name": r.plugin_name,
                    "interface_name": r.interface_name,
                    "table_name": r.table_name,
                    "table_exists": r.table_exists,
                    "is_empty": r.is_empty,
                    "completeness_pct": r.completeness_pct,
                    "missing_windows": [
                        {
                            "start_date": w.start_date.isoformat(),
                            "end_date": w.end_date.isoformat(),
                            "missing_days": w.missing_days,
                        }
                        for w in r.missing_windows
                    ],
                    "recommended_actions": [
                        {
                            "plugin_name": a.plugin_name,
                            "operation": a.operation,
                            "params": a.params,
                            "reason": a.reason,
                            "priority": a.priority,
                            "estimated_minutes": a.estimated_minutes,
                        }
                        for a in r.recommended_actions
                    ],
                }
                for r in plan.plugin_reports
            ],
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self.logger.info(f"Completeness plan saved to: {path}")


# Global planner instance
_planner_instance: Optional[TuShareCompletenessPlanner] = None


def get_completeness_planner() -> TuShareCompletenessPlanner:
    """Get or create the global completeness planner."""
    global _planner_instance
    if _planner_instance is None:
        _planner_instance = TuShareCompletenessPlanner()
    return _planner_instance
