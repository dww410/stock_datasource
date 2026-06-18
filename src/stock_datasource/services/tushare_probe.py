"""Safe TuShare API probe - discover which interfaces actually return data.

This service probes the TuShare API with minimal requests to determine:
1. Which interfaces are accessible with the current token
2. Which interfaces return data vs. empty
3. Which require unavailable parameters
4. Which are deprecated or rate-limited

No probe data is written to ClickHouse - results are only for plugin planning.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

import pandas as pd

from stock_datasource.config.settings import settings
from stock_datasource.services.tushare_interface_registry import (
    TuShareInterface,
    get_interface_registry,
)
from stock_datasource.utils.logger import logger


class ProbeStatus(str, Enum):
    """Result status for a probed interface."""

    SUPPORTED_WITH_DATA = "supported_with_data"
    SUPPORTED_EMPTY = "supported_empty"
    PERMISSION_DENIED = "permission_denied"
    DEPRECATED_OR_MISSING = "deprecated_or_missing"
    REQUIRES_UNAVAILABLE_PARAMS = "requires_unavailable_params"
    API_ERROR = "api_error"
    RATE_LIMITED = "rate_limited"
    NOT_TESTED = "not_tested"


@dataclass
class ProbeResult:
    """Result for a single interface probe."""

    interface_name: str
    status: ProbeStatus
    plugin_name: Optional[str] = None
    plugin_exists: bool = False
    message: str = ""
    row_count: Optional[int] = None
    sample_columns: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    required_params: list[str] = field(default_factory=list)
    suggested_date_param: Optional[str] = None


@dataclass
class ProbeReport:
    """Complete probe report."""

    generated_at: datetime
    total_probed: int
    with_plugin: int
    without_plugin: int
    supported_with_data: int
    supported_empty: int
    permission_denied: int
    requires_params: int
    api_errors: int
    results: list[ProbeResult]
    duration_seconds: float
    rate_limit_hits: int


class TuShareProbe:
    """Safe TuShare API probe service."""

    def __init__(self):
        self.logger = logger.bind(component="TuShareProbe")
        self.registry = get_interface_registry()
        self._api = None
        self._rate_limit_delay = 1.0 / 120  # 120 requests per minute default

    def _get_api(self) -> Optional[Any]:
        """Get or create TuShare API client."""
        if self._api is None:
            try:
                import tushare as ts

                token = settings.TUSHARE_TOKEN
                if not token:
                    self.logger.warning("TUSHARE_TOKEN not configured")
                    return None

                ts.set_token(token)
                self._api = ts.pro_api()
                self.logger.info("TuShare API client initialized")
            except Exception as e:
                self.logger.error(f"Failed to initialize TuShare API: {e}")
                return None
        return self._api

    def probe_all(
        self,
        limit: Optional[int] = None,
        only_missing_plugins: bool = False,
        delay: Optional[float] = None,
    ) -> ProbeReport:
        """Probe all or a subset of TuShare interfaces.

        Args:
            limit: Maximum number of interfaces to probe (for testing)
            only_missing_plugins: Only probe interfaces without plugins
            delay: Override default delay between requests (seconds)

        Returns:
            Complete probe report
        """
        api = self._get_api()
        if not api:
            raise RuntimeError("TuShare API not available - check TUSHARE_TOKEN")

        start_time = time.time()
        results: list[ProbeResult] = []
        rate_limit_hits = 0

        # Determine which interfaces to probe
        if only_missing_plugins:
            # We'd probe interfaces not in the registry - but for now
            # we probe existing registry interfaces first
            to_probe = [iface for iface in self.registry.list_all()]
        else:
            to_probe = self.registry.list_all()

        if limit:
            to_probe = to_probe[:limit]

        self.logger.info(f"Starting probe of {len(to_probe)} interfaces")

        for i, iface in enumerate(to_probe):
            if delay is not None:
                time.sleep(delay)
            elif i > 0:
                time.sleep(self._rate_limit_delay)

            try:
                result = self._probe_interface(api, iface)
                results.append(result)

                if result.status == ProbeStatus.RATE_LIMITED:
                    rate_limit_hits += 1
                    time.sleep(2)  # Extra delay after rate limit hit

            except Exception as e:
                self.logger.error(f"Probe failed for {iface.interface_name}: {e}")
                results.append(
                    ProbeResult(
                        interface_name=iface.interface_name,
                        plugin_name=iface.plugin_name,
                        plugin_exists=True,
                        status=ProbeStatus.API_ERROR,
                        message=str(e),
                    )
                )

        duration = time.time() - start_time

        # Calculate summary stats
        supported_with_data = sum(
            1 for r in results if r.status == ProbeStatus.SUPPORTED_WITH_DATA
        )
        supported_empty = sum(
            1 for r in results if r.status == ProbeStatus.SUPPORTED_EMPTY
        )
        permission_denied = sum(
            1 for r in results if r.status == ProbeStatus.PERMISSION_DENIED
        )
        requires_params = sum(
            1 for r in results if r.status == ProbeStatus.REQUIRES_UNAVAILABLE_PARAMS
        )
        api_errors = sum(1 for r in results if r.status == ProbeStatus.API_ERROR)

        report = ProbeReport(
            generated_at=datetime.now(),
            total_probed=len(results),
            with_plugin=sum(1 for r in results if r.plugin_exists),
            without_plugin=sum(1 for r in results if not r.plugin_exists),
            supported_with_data=supported_with_data,
            supported_empty=supported_empty,
            permission_denied=permission_denied,
            requires_params=requires_params,
            api_errors=api_errors,
            results=results,
            duration_seconds=duration,
            rate_limit_hits=rate_limit_hits,
        )

        self.logger.info(
            f"Probe complete: {supported_with_data} with data, "
            f"{supported_empty} empty, {permission_denied} denied, "
            f"duration={duration:.1f}s"
        )

        return report

    def _probe_interface(self, api: Any, iface: TuShareInterface) -> ProbeResult:
        """Probe a single TuShare interface."""
        start_time = time.time()
        result = ProbeResult(
            interface_name=iface.interface_name,
            plugin_name=iface.plugin_name,
            plugin_exists=True,
        )

        # Try with minimal params first - just a limit of 1
        # Use the interface name from the plugin config
        api_name = iface.interface_name

        try:
            # Get the API method
            method = getattr(api, api_name, None)
            if method is None:
                result.status = ProbeStatus.DEPRECATED_OR_MISSING
                result.message = f"Method {api_name} not found on TuShare API"
                result.duration_ms = (time.time() - start_time) * 1000
                return result

            # Try 1: Most minimal request - just limit
            try:
                df = method(limit=1)
                result.duration_ms = (time.time() - start_time) * 1000

                if isinstance(df, pd.DataFrame):
                    result.row_count = len(df)
                    result.sample_columns = list(df.columns)

                    if len(df) > 0:
                        result.status = ProbeStatus.SUPPORTED_WITH_DATA
                        result.message = f"Returned {len(df)} rows"
                    else:
                        result.status = ProbeStatus.SUPPORTED_EMPTY
                        result.message = "API exists but returned empty DataFrame"

                    # Detect date parameter from response columns
                    date_cols = ["trade_date", "ann_date", "end_date", "date", "cal_date"]
                    for col in date_cols:
                        if col in result.sample_columns:
                            result.suggested_date_param = col
                            break

                    return result

            except Exception as e:
                error_msg = str(e)

                # Check for permission errors
                if "permission" in error_msg.lower() or "无权" in error_msg:
                    result.status = ProbeStatus.PERMISSION_DENIED
                    result.message = error_msg
                    result.duration_ms = (time.time() - start_time) * 1000
                    return result

                # Check for rate limit
                if "request limit" in error_msg.lower() or "频率" in error_msg:
                    result.status = ProbeStatus.RATE_LIMITED
                    result.message = error_msg
                    result.duration_ms = (time.time() - start_time) * 1000
                    return result

                # Check for required parameter errors
                if "required" in error_msg.lower() or "缺少" in error_msg or "参数" in error_msg:
                    # Try to extract required params from error message
                    result.status = ProbeStatus.REQUIRES_UNAVAILABLE_PARAMS
                    result.message = error_msg
                    result.required_params = self._extract_params_from_error(error_msg)
                    result.duration_ms = (time.time() - start_time) * 1000
                    return result

                # Fall through to general error
                result.status = ProbeStatus.API_ERROR
                result.message = error_msg
                result.duration_ms = (time.time() - start_time) * 1000
                return result

        except Exception as e:
            result.status = ProbeStatus.API_ERROR
            result.message = str(e)
            result.duration_ms = (time.time() - start_time) * 1000
            return result

    def _extract_params_from_error(self, error_msg: str) -> list[str]:
        """Extract required parameter names from TuShare error messages."""
        params = []
        # Common patterns: "param 'xxx' is required" or similar
        import re

        # Try to find quoted parameter names
        matches = re.findall(r"['\"](\w+)['\"]", error_msg)
        for match in matches:
            if len(match) > 1 and match not in params:
                # Filter out common non-parameter words
                if match not in ("code", "msg", "data"):
                    params.append(match)

        return params

    def get_implementable_interfaces(self, report: ProbeReport) -> list[ProbeResult]:
        """Get interfaces that could be implemented as plugins.

        Returns interfaces that:
        1. Return data
        2. Don't have a plugin yet
        3. Don't require unavailable parameters
        """
        return [
            r
            for r in report.results
            if r.status == ProbeStatus.SUPPORTED_WITH_DATA
            and not r.plugin_exists
        ]

    def save_report(self, report: ProbeReport, path: str) -> None:
        """Save probe report to JSON file."""
        data = {
            "generated_at": report.generated_at.isoformat(),
            "total_probed": report.total_probed,
            "with_plugin": report.with_plugin,
            "without_plugin": report.without_plugin,
            "supported_with_data": report.supported_with_data,
            "supported_empty": report.supported_empty,
            "permission_denied": report.permission_denied,
            "requires_params": report.requires_params,
            "api_errors": report.api_errors,
            "duration_seconds": report.duration_seconds,
            "rate_limit_hits": report.rate_limit_hits,
            "results": [
                {
                    "interface_name": r.interface_name,
                    "plugin_name": r.plugin_name,
                    "plugin_exists": r.plugin_exists,
                    "status": r.status.value,
                    "message": r.message,
                    "row_count": r.row_count,
                    "sample_columns": r.sample_columns,
                    "duration_ms": r.duration_ms,
                    "required_params": r.required_params,
                    "suggested_date_param": r.suggested_date_param,
                }
                for r in report.results
            ],
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self.logger.info(f"Probe report saved to: {path}")


# Global probe instance
_probe_instance: Optional[TuShareProbe] = None


def get_tushare_probe() -> TuShareProbe:
    """Get or create the global probe service."""
    global _probe_instance
    if _probe_instance is None:
        _probe_instance = TuShareProbe()
    return _probe_instance
