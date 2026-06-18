"""AKShare Hong Kong daily data plugin implementation."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from stock_datasource.core.base_plugin import PluginCategory, PluginRole
from stock_datasource.plugins import BasePlugin

from .extractor import (
    akshare_to_ts_code,
    extractor,
    map_akshare_to_tushare,
    ts_code_to_akshare,
)


class AKShareHKDailyPlugin(BasePlugin):
    """AKShare Hong Kong daily data plugin."""

    @property
    def name(self) -> str:
        return "akshare_hk_daily"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "AKShare Hong Kong daily data from hk_daily API"

    @property
    def api_rate_limit(self) -> int:
        config_file = Path(__file__).parent / "config.json"
        with open(config_file, encoding="utf-8") as f:
            config = json.load(f)
        return config.get("rate_limit", 60)

    def get_category(self) -> PluginCategory:
        """Get plugin category - 港股."""
        return PluginCategory.HK_STOCK

    def get_role(self) -> PluginRole:
        """Get plugin role - 主数据."""
        return PluginRole.PRIMARY

    def get_dependencies(self) -> list[str]:
        """Get plugin dependencies - 依赖港股列表."""
        return ["akshare_hk_stock_list"]

    def get_schema(self) -> dict[str, Any]:
        """Get table schema from separate JSON file."""
        schema_file = Path(__file__).parent / "schema.json"
        with open(schema_file, encoding="utf-8") as f:
            return json.load(f)

    def extract_data(self, **kwargs) -> pd.DataFrame:
        """Extract Hong Kong daily data from AKShare."""
        symbol = kwargs.get("symbol")
        ts_code = kwargs.get("ts_code")
        start_date = kwargs.get("start_date")
        end_date = kwargs.get("end_date")
        max_stocks = kwargs.get("max_stocks")

        if symbol or ts_code:
            target_ts_code = akshare_to_ts_code(ts_code or symbol)
            target_symbol = ts_code_to_akshare(target_ts_code)
            if symbol and ts_code and akshare_to_ts_code(symbol) != target_ts_code:
                raise ValueError(
                    f"Conflicting symbol and ts_code: symbol={symbol}, ts_code={ts_code}"
                )
            return self._extract_single_stock(target_ts_code, target_symbol, start_date, end_date)

        if not self.db:
            raise ValueError("Database is required for batch HK daily extraction")

        stock_codes = self._get_hk_stock_list()
        if max_stocks:
            stock_codes = stock_codes[: int(max_stocks)]

        frames = []
        for stock_ts_code in stock_codes:
            stock_symbol = ts_code_to_akshare(stock_ts_code)
            try:
                frame = self._extract_single_stock(
                    stock_ts_code, stock_symbol, start_date, end_date
                )
            except Exception as e:
                self.logger.error(f"Failed to extract Hong Kong daily data for {stock_ts_code}: {e}")
                continue
            if not frame.empty:
                frames.append(frame)

        if not frames:
            self.logger.warning("No Hong Kong daily data found")
            return pd.DataFrame()

        return pd.concat(frames, ignore_index=True)

    def _extract_single_stock(
        self,
        ts_code: str,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """Extract and map one Hong Kong stock."""
        self.logger.info(f"Extracting Hong Kong daily data for {ts_code}")
        data = extractor.extract(symbol, start_date, end_date)
        if data.empty:
            self.logger.warning(f"No Hong Kong daily data found for {ts_code}")
            return pd.DataFrame()

        mapped = map_akshare_to_tushare(data, ts_code)
        self.logger.info(f"Extracted {len(mapped)} Hong Kong daily records for {ts_code}")
        return mapped

    def _get_hk_stock_list(self) -> list[str]:
        """Get listed Hong Kong stock universe from ods_hk_basic using self.db."""
        query = """
        SELECT DISTINCT ts_code
        FROM ods_hk_basic
        WHERE list_status = 'L'
        ORDER BY ts_code
        """
        data = self.db.execute_query(query)
        if data is None or data.empty:
            self.logger.warning("No HK stocks found in ods_hk_basic table")
            return []
        return data["ts_code"].dropna().astype(str).tolist()

    def validate_data(self, data: pd.DataFrame) -> bool:
        """Validate Hong Kong daily data."""
        if data.empty:
            self.logger.warning("Empty Hong Kong daily data")
            return False

        required_columns = ["ts_code", "trade_date", "close"]
        missing_columns = [col for col in required_columns if col not in data.columns]

        if missing_columns:
            self.logger.error(f"Missing required columns: {missing_columns}")
            return False

        # Check for null values in key fields
        null_symbols = data["ts_code"].isnull().sum()
        null_dates = data["trade_date"].isnull().sum()

        if null_symbols > 0 or null_dates > 0:
            self.logger.error(
                f"Found null values: ts_code={null_symbols}, trade_date={null_dates}"
            )
            return False

        self.logger.info(
            f"Hong Kong daily data validation passed for {len(data)} records"
        )
        return True

    def transform_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """Transform data for database insertion."""
        # Data is already properly formatted in extract_data
        self.logger.info(f"Transformed {len(data)} Hong Kong daily records")
        return data

    def load_data(self, data: pd.DataFrame) -> dict[str, Any]:
        """Load Hong Kong daily data into ODS table.

        Args:
            data: Hong Kong daily data to load

        Returns:
            Loading statistics
        """
        if not self.db:
            self.logger.error("Database not initialized")
            return {"status": "failed", "error": "Database not initialized"}

        if data.empty:
            self.logger.warning("No data to load")
            return {"status": "no_data", "loaded_records": 0}

        # Deduplicate: delete existing data for the dates being loaded (idempotent)
        try:
            # Check for existing data - skip if exists (incremental sync mode)
            should_load = self._deduplicate_before_load("ods_hk_daily", data, date_column="trade_date", skip_if_exists=True)
            if not should_load:
                return {"status": "success", "skipped": True, "message": "Data already exists"}
        except Exception as e:
            self.logger.warning(f"Deduplication failed: {e}")


        try:
            self.logger.info(f"Loading {len(data)} records into ods_hk_daily")
            ods_data = data.copy()
            ods_data["version"] = int(datetime.now().timestamp())
            ods_data["_ingested_at"] = datetime.now()

            # Prepare data types
            ods_data = self._prepare_data_for_insert("ods_hk_daily", ods_data)

            # Add ClickHouse settings for large inserts
            settings = {"max_partitions_per_insert_block": 1000}
            self.db.insert_dataframe("ods_hk_daily", ods_data, settings=settings)

            self.logger.info(f"Loaded {len(ods_data)} records into ods_hk_daily")
            return {
                "status": "success",
                "table": "ods_hk_daily",
                "loaded_records": len(ods_data),
            }

        except Exception as e:
            self.logger.error(f"Failed to load data: {e}")
            return {"status": "failed", "error": str(e)}
