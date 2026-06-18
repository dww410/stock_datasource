#!/usr/bin/env python3
"""Run financial statement plugins to extract data from TuShare API and load to backup database."""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from stock_datasource.core.plugin_manager import plugin_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configure to use backup database only when backup settings are provided
for backup_key, primary_key in {
    'BACKUP_CLICKHOUSE_HOST': 'CLICKHOUSE_HOST',
    'BACKUP_CLICKHOUSE_PORT': 'CLICKHOUSE_PORT',
    'BACKUP_CLICKHOUSE_USER': 'CLICKHOUSE_USER',
    'BACKUP_CLICKHOUSE_PASSWORD': 'CLICKHOUSE_PASSWORD',
    'BACKUP_CLICKHOUSE_DATABASE': 'CLICKHOUSE_DATABASE',
}.items():
    value = os.getenv(backup_key)
    if value:
        os.environ[primary_key] = value

PLUGINS = [
    'tushare_income',
    'tushare_balancesheet',
    'tushare_cashflow',
    'tushare_express',
    'tushare_forecast'
]

def run_plugin(plugin_name):
    """Run a single plugin to extract and load data."""
    print(f"\n{'='*60}")
    print(f"[{datetime.now()}] Running plugin: {plugin_name}")
    print(f"{'='*60}\n")

    try:
        # Discover and get plugin
        plugin = plugin_manager.get_plugin(plugin_name)
        if not plugin:
            print(f"❌ Plugin {plugin_name} not found")
            return False

        print(f"[{datetime.now()}] Running plugin lifecycle...")
        result = plugin.run()
        status = result.get('status')
        load_result = result.get('steps', {}).get('load', {})

        if status == 'success':
            loaded = load_result.get('loaded_records', load_result.get('total_records', 0))
            print(f"✅ Successfully loaded {loaded} records to backup database")
            return True
        if status == 'no_data':
            print(f"⚠️ No data extracted from TuShare API for {plugin_name}")
            return True

        print(f"❌ Failed to run plugin: {result.get('error', 'Unknown error')}")
        return False

    except Exception as e:
        print(f"❌ Error running plugin {plugin_name}: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all financial statement plugins."""
    print(f"\n{'='*60}")
    print(f"[{datetime.now()}] Starting financial statement plugins")
    print("Target: configured ClickHouse database")
    print(f"{'='*60}\n")

    # Discover all plugins
    print(f"[{datetime.now()}] Discovering plugins...")
    plugin_manager.discover_plugins()

    # Run each plugin
    results = {}
    for plugin_name in PLUGINS:
        results[plugin_name] = run_plugin(plugin_name)

    # Summary
    print(f"\n{'='*60}")
    print(f"[{datetime.now()}] Plugin Execution Summary")
    print(f"{'='*60}\n")

    success_count = 0
    for plugin_name, success in results.items():
        status = "✅" if success else "❌"
        print(f"{status} {plugin_name}")
        if success:
            success_count += 1

    print(f"\nTotal: {success_count}/{len(PLUGINS)} plugins completed successfully")
    print(f"{'='*60}\n")

    return success_count == len(PLUGINS)

if __name__ == '__main__':
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print(f"\n\n[{datetime.now()}] ⚠️ Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
