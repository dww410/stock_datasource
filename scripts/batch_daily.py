#!/usr/bin/env python3
"""Batch pull A-share daily data for a date range, date by date."""
import subprocess
import sys
from datetime import date, timedelta

START = date(2022, 5, 30)
END = date(2026, 5, 30)
WORKDIR = "/app"

def trading_days():
    """Get trading days from ClickHouse."""
    import clickhouse_driver
    client = clickhouse_driver.Client(
        host="stock-clickhouse",
        port=9000,
        user="clickhouse",
        password="clickhouse",
        database="stock_datasource",
    )
    rows = client.execute(
        "SELECT cal_date FROM ods_trade_calendar "
        "WHERE cal_date >= %(start)s AND cal_date <= %(end)s AND is_open = 1 "
        "ORDER BY cal_date",
        {"start": START, "end": END},
    )
    return [r[0] for r in rows]

def run_date(d: date):
    ds = d.strftime("%Y%m%d")
    print(f"\n=== {ds} ===")
    r = subprocess.run(
        ["uv", "run", "--no-sync", "python", "cli.py", "run-plugin", "tushare_daily",
         "--trade-date", ds, "--no-quality-checks"],
        capture_output=True, text=True, timeout=90,
    )
    if r.returncode != 0:
        # Check if it's a "no data" issue (weekend/holiday not properly filtered)
        if "Empty DataFrame" in r.stderr + r.stdout:
            print(f"  {ds}: no data (skipped)")
            return True
        print(f"  FAILED: {r.stdout[-300:]}{r.stderr[-300:]}")
        return False
    # Extract record count
    for line in r.stdout.split("\n"):
        if "extract" in line and "records" in line:
            print(f"  {ds}: {line.strip()}")
            break
    return True

def main():
    days = trading_days()
    print(f"Total trading days: {len(days)}")
    ok = fail = 0
    for d in days:
        if run_date(d):
            ok += 1
        else:
            fail += 1
        if fail > 5:
            print(f"Too many failures ({fail}), aborting")
            sys.exit(1)
    print(f"\nDone: {ok} OK, {fail} failed")

if __name__ == "__main__":
    main()
