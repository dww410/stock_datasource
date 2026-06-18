"""Sync index daily data for 2026-01."""

import time

# 直接使用插件同步
from stock_datasource.core.plugin_manager import plugin_manager

# 确保插件已注册
plugin_manager.discover_plugins()

dates = ["20260105", "20260106", "20260107", "20260108", "20260109", "20260112", "20260113"]

print("Syncing index daily data...")
total_inserted = 0

for date in dates:
    try:
        plugin = plugin_manager.get_plugin("tushare_index_daily")
        if not plugin:
            print(f"  {date}: Plugin not found")
            continue

        result = plugin.run(trade_date=date)
        load_result = result.get("steps", {}).get("load", {})

        if result.get("status") == "success":
            extract_status = result.get("steps", {}).get("extract", {}).get("status")
            if extract_status == "no_data":
                print(f"  {date}: No data")
                continue

            count = load_result.get("total_records", load_result.get("loaded_records", 0))
            total_inserted += count
            print(f"  {date}: {count} records inserted")
        else:
            error = result.get("error") or load_result.get("error", "Unknown error")
            print(f"  {date}: Load failed - {error}")

        time.sleep(1)  # Rate limiting
    except Exception as e:
        print(f"  {date}: ERROR - {str(e)[:100]}")
        import traceback

        traceback.print_exc()

print(f"\nTotal: {total_inserted} records inserted")
