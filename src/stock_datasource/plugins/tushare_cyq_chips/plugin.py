"""TuShare cyq_chips (筹码分布) plugin implementation."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from stock_datasource.core.base_plugin import PluginCategory, PluginRole
from stock_datasource.plugins import BasePlugin

from .extractor import extractor


class TuShareCyqChipsPlugin(BasePlugin):
    """TuShare cyq_chips (筹码分布) data plugin."""

    @property
    def name(self) -> str:
        return "tushare_cyq_chips"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "TuShare A股筹码分布数据"

    @property
    def api_rate_limit(self) -> int:
        config_file = Path(__file__).parent / "config.json"
        with open(config_file, encoding="utf-8") as f:
            config = json.load(f)
        return config.get("rate_limit", 120)

    def get_schema(self) -> dict[str, Any]:
        """Get table schema from separate JSON file."""
        schema_file = Path(__file__).parent / "schema.json"
        with open(schema_file, encoding="utf-8") as f:
            return json.load(f)

    def get_category(self) -> PluginCategory:
        """Get plugin category."""
        return PluginCategory.STOCK

    def get_role(self) -> PluginRole:
        """Get plugin role."""
        return PluginRole.DERIVED

    def get_dependencies(self) -> list[str]:
        """Get plugin dependencies."""
        return ["tushare_stock_basic"]

    def get_optional_dependencies(self) -> list[str]:
        """Get optional plugin dependencies."""
        return []

    def extract_data(self, **kwargs) -> pd.DataFrame:
        """Extract cyq_chips data from TuShare.

        Required kwargs:
            ts_code: Stock code (e.g., 600000.SH)

        Optional kwargs:
            trade_date: Trade date in YYYYMMDD format
            start_date: Start date in YYYYMMDD format
            end_date: End date in YYYYMMDD format
        """
        ts_code = kwargs.get("ts_code")
        trade_date = kwargs.get("trade_date")
        start_date = kwargs.get("start_date")
        end_date = kwargs.get("end_date")

        # Batch mode: extract for all stocks if ts_code not provided
        if not ts_code:
            if not self.db:
                raise ValueError("Database not initialized for batch mode")

            self.logger.info("Extracting cyq_chips data for all stocks (batch mode)")

            # Get all stock codes from stock_basic table
            stocks_query = (
                "SELECT DISTINCT ts_code FROM ods_stock_basic WHERE list_status = 'L'"
            )
            stocks_df = self.db.execute_query(stocks_query)

            if stocks_df.empty:
                self.logger.warning("No stocks found in stock_basic table")
                return pd.DataFrame()

            all_data = []
            total_records = 0
            for idx, row in stocks_df.iterrows():
                stock_code = row["ts_code"]
                try:
                    # Report progress every 10 stocks
                    if idx % 10 == 0 or idx == len(stocks_df) - 1:
                        progress = ((idx + 1) / len(stocks_df)) * 100
                        self.update_progress(progress, total_records)

                    data = extractor.extract(
                        ts_code=stock_code,
                        trade_date=trade_date,
                        start_date=start_date,
                        end_date=end_date,
                    )

                    if not data.empty:
                        all_data.append(data)
                        total_records += len(data)

                    # Rate limiting between API calls
                    import time

                    time.sleep(0.1)

                except Exception as e:
                    self.logger.warning(
                        f"Failed to extract cyq_chips for {stock_code}: {e}"
                    )
                    continue

            if not all_data:
                self.logger.warning("No cyq_chips data extracted for any stock")
                return pd.DataFrame()

            combined_data = pd.concat(all_data, ignore_index=True)
            # Add system columns for batch mode
            combined_data["version"] = int(datetime.now().timestamp())
            combined_data["_ingested_at"] = datetime.now()
            self.logger.info(
                f"Extracted {len(combined_data)} cyq_chips records from {len(all_data)} stocks"
            )
            return combined_data

        # Single stock mode
        self.logger.info(f"Extracting cyq_chips data for {ts_code}")

        data = extractor.extract(
            ts_code=ts_code,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
        )

        if data.empty:
            self.logger.warning(f"No cyq_chips data found for {ts_code}")
            return pd.DataFrame()

        # Add system columns
        data["version"] = int(datetime.now().timestamp())
        data["_ingested_at"] = datetime.now()

        self.logger.info(f"Extracted {len(data)} cyq_chips records for {ts_code}")
        return data

    def validate_data(self, data: pd.DataFrame) -> bool:
        """Validate cyq_chips data."""
        if data.empty:
            self.logger.warning("Empty cyq_chips data")
            return False

        required_columns = ["ts_code", "trade_date", "price", "percent"]
        missing_columns = [col for col in required_columns if col not in data.columns]

        if missing_columns:
            self.logger.error(f"Missing required columns: {missing_columns}")
            return False

        # Check for null values in key fields
        null_ts_codes = data["ts_code"].isnull().sum()
        if null_ts_codes > 0:
            self.logger.error(f"Found {null_ts_codes} null ts_code values")
            return False

        # Validate price is positive
        invalid_prices = data[data["price"] <= 0]
        if len(invalid_prices) > 0:
            self.logger.warning(
                f"Found {len(invalid_prices)} records with non-positive prices"
            )

        # Validate percent is in reasonable range (0-100)
        invalid_percent = data[(data["percent"] < 0) | (data["percent"] > 100)]
        if len(invalid_percent) > 0:
            self.logger.warning(
                f"Found {len(invalid_percent)} records with invalid percent values"
            )

        self.logger.info(f"Cyq_chips data validation passed for {len(data)} records")
        return True

    def transform_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """Transform data for database insertion."""
        # Ensure proper data types
        numeric_columns = ["price", "percent"]
        for col in numeric_columns:
            if col in data.columns:
                data[col] = pd.to_numeric(data[col], errors="coerce")

        # Convert date format
        if "trade_date" in data.columns:
            data["trade_date"] = pd.to_datetime(
                data["trade_date"], format="%Y%m%d"
            ).dt.date

        self.logger.info(f"Transformed {len(data)} cyq_chips records")
        return data

    def load_data(self, data: pd.DataFrame) -> dict[str, Any]:
        """Load cyq_chips data into ClickHouse.

        Args:
            data: Cyq_chips data to load

        Returns:
            Loading statistics
        """
        if not self.db:
            self.logger.error("Database not initialized")
            return {"status": "failed", "error": "Database not initialized"}

        if data.empty:
            self.logger.warning("No data to load")
            return {"status": "no_data", "loaded_records": 0}

        results = {"status": "success", "tables_loaded": [], "total_records": 0}

        # Deduplicate: delete existing data for the dates being loaded
        try:
            # Check for existing data - skip if exists (incremental sync mode)
            should_load = self._deduplicate_before_load("ods_cyq_chips", data, date_column="trade_date", skip_if_exists=True)
            if not should_load:
                return {"status": "success", "skipped": True, "message": "Data already exists"}
        except Exception as e:
            self.logger.warning(f"Deduplication failed: {e}")


        try:
            table_name = "ods_cyq_chips"
            self.logger.info(f"Loading {len(data)} records into {table_name}")

            ods_data = data.copy()
            ods_data["version"] = int(datetime.now().timestamp())
            ods_data["_ingested_at"] = datetime.now()

            # Prepare data types
            ods_data = self._prepare_data_for_insert(table_name, ods_data)
            self.db.insert_dataframe(table_name, ods_data)

            results["tables_loaded"].append(
                {"table": table_name, "records": len(ods_data)}
            )
            results["total_records"] += len(ods_data)
            self.logger.info(f"Loaded {len(ods_data)} records into {table_name}")

        except Exception as e:
            self.logger.error(f"Failed to load data: {e}")
            results["status"] = "failed"
            results["error"] = str(e)

        return results


if __name__ == "__main__":
    """Allow plugin to be executed as a standalone script."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="TuShare Cyq_chips Data Plugin")
    parser.add_argument("--ts_code", required=True, help="Stock code (e.g., 600000.SH)")
    parser.add_argument("--date", help="Trade date in YYYYMMDD format")
    parser.add_argument("--start_date", help="Start date in YYYYMMDD format")
    parser.add_argument("--end_date", help="End date in YYYYMMDD format")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    # Initialize plugin
    plugin = TuShareCyqChipsPlugin()

    # Build kwargs
    run_kwargs = {"ts_code": args.ts_code}
    if args.date:
        run_kwargs["trade_date"] = args.date
    if args.start_date:
        run_kwargs["start_date"] = args.start_date
    if args.end_date:
        run_kwargs["end_date"] = args.end_date

    # Run pipeline
    result = plugin.run(**run_kwargs)

    # Print result
    print(f"\n{'=' * 60}")
    print(f"Plugin: {result['plugin']}")
    print(f"Status: {result['status']}")
    print(f"{'=' * 60}")

    for step, step_result in result.get("steps", {}).items():
        status = step_result.get("status", "unknown")
        records = step_result.get("records", 0)
        print(f"{step:15} : {status:10} ({records} records)")

    if result["status"] != "success":
        if "error" in result:
            print(f"\nError: {result['error']}")
        sys.exit(1)

    sys.exit(0)
