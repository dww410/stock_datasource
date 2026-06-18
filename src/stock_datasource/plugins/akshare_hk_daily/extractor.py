"""AKShare Hong Kong daily data extractor - independent implementation."""

import json
import logging
import time
from pathlib import Path

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


OUTPUT_COLUMNS = [
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


COLUMN_ALIASES = {
    "date": "trade_date",
    "日期": "trade_date",
    "open": "open",
    "开盘": "open",
    "high": "high",
    "最高": "high",
    "low": "low",
    "最低": "low",
    "close": "close",
    "收盘": "close",
    "volume": "vol",
    "成交量": "vol",
}


def ts_code_to_akshare(ts_code: str) -> str:
    """Convert TuShare HK code (00700.HK) to AKShare symbol (00700)."""
    if not ts_code:
        return ts_code
    return str(ts_code).split(".")[0]


def akshare_to_ts_code(symbol: str) -> str:
    """Convert AKShare HK symbol (00700) to TuShare HK code (00700.HK)."""
    if not symbol:
        return symbol
    symbol = str(symbol)
    if symbol.upper().endswith(".HK"):
        return f"{symbol.rsplit('.', 1)[0]}.HK"
    return f"{symbol}.HK"


def map_akshare_to_tushare(df: pd.DataFrame, ts_code: str) -> pd.DataFrame:
    """Map AKShare HK daily rows to the ods_hk_daily ts_code-style shape."""
    if df is None or df.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    mapped = pd.DataFrame()
    for source_col, target_col in COLUMN_ALIASES.items():
        if source_col in df.columns:
            mapped[target_col] = df[source_col]

    required_columns = ["trade_date", "open", "high", "low", "close", "vol"]
    missing_columns = [col for col in required_columns if col not in mapped.columns]
    if missing_columns:
        raise KeyError(f"Missing AKShare HK daily columns: {missing_columns}")

    mapped["ts_code"] = ts_code
    mapped["trade_date"] = pd.to_datetime(mapped["trade_date"], errors="coerce").dt.date
    for col in ["open", "high", "low", "close", "vol"]:
        mapped[col] = pd.to_numeric(mapped[col], errors="coerce")

    mapped = mapped.dropna(subset=["trade_date", "close"]).sort_values("trade_date")
    mapped["pre_close"] = mapped["close"].shift(1)
    mapped["change"] = mapped["close"] - mapped["pre_close"]
    mapped["pct_chg"] = (mapped["change"] / mapped["pre_close"] * 100).round(2)
    mapped["amount"] = None
    mapped = mapped.dropna(subset=["pre_close"])

    return mapped[OUTPUT_COLUMNS].reset_index(drop=True)


class HKDailyExtractor:
    """Independent extractor for AKShare Hong Kong daily data."""

    def __init__(self):
        # Load rate_limit from config.json
        config_file = Path(__file__).parent / "config.json"
        with open(config_file, encoding="utf-8") as f:
            config = json.load(f)
        self.rate_limit = config.get("rate_limit", 60)  # Default to 60 if not specified

        # Rate limiting
        self._last_call_time = 0
        self._min_interval = 60.0 / self.rate_limit

    def _rate_limit(self):
        """Apply rate limiting."""
        current_time = time.time()
        time_since_last = current_time - self._last_call_time

        if time_since_last < self._min_interval:
            sleep_time = self._min_interval - time_since_last
            time.sleep(sleep_time)

        self._last_call_time = time.time()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        reraise=True,
    )
    def _call_api(
        self, symbol: str, start_date: str | None = None, end_date: str | None = None
    ) -> pd.DataFrame:
        """Call AKShare API with rate limiting and retry."""
        self._rate_limit()

        try:
            import akshare as ak

            result = ak.stock_hk_daily(symbol=symbol, adjust="qfq")

            if result is None or result.empty:
                logger.warning(f"API returned empty data for symbol {symbol}")
                return pd.DataFrame()

            if "date" in result.columns:
                date_col = "date"
            elif "日期" in result.columns:
                date_col = "日期"
            else:
                date_col = None

            if date_col and (start_date or end_date):
                result = result.copy()
                result[date_col] = pd.to_datetime(result[date_col], errors="coerce")
                if start_date:
                    start_dt = pd.to_datetime(start_date, format="%Y%m%d", errors="coerce")
                    result = result[result[date_col] >= start_dt]
                if end_date:
                    end_dt = pd.to_datetime(end_date, format="%Y%m%d", errors="coerce")
                    result = result[result[date_col] <= end_dt]

            logger.info(f"API call successful for {symbol}, records: {len(result)}")
            return result

        except Exception as e:
            logger.error(f"API call failed for {symbol}: {e}")
            raise

    def extract(
        self, symbol: str, start_date: str | None = None, end_date: str | None = None
    ) -> pd.DataFrame:
        """Extract Hong Kong daily data.

        Args:
            symbol: Hong Kong stock symbol (e.g., 00700)
            start_date: Start date in YYYYMMDD format (optional)
            end_date: End date in YYYYMMDD format (optional)

        Returns:
            DataFrame with Hong Kong daily data
        """
        return self._call_api(ts_code_to_akshare(symbol), start_date, end_date)


# Global extractor instance
extractor = HKDailyExtractor()
