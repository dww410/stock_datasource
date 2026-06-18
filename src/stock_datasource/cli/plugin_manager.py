"""Plugin management CLI commands (``plugin list / run / backfill``).

Enables backfill of historical data for any ``BasePlugin`` subclass without
writing ad-hoc scripts.  Every operation goes through the standard
``extract → validate → transform → load`` pipeline.
"""

from __future__ import annotations

from typing import Any

import click

from stock_datasource.core.base_plugin import BasePlugin


# ---------------------------------------------------------------------------
# Plugin discovery
# ---------------------------------------------------------------------------


def _discover_plugins() -> dict[str, type[BasePlugin]]:
    """Discover all ``BasePlugin`` subclasses in the plugins package.

    Returns:
        ``{plugin_name: plugin_class}`` mapping.
    """
    import importlib
    import inspect
    import pkgutil
    from pathlib import Path

    import stock_datasource.plugins as plugins_pkg

    discovered: dict[str, type[BasePlugin]] = {}
    package_path = Path(plugins_pkg.__file__).parent
    package_full = "stock_datasource.plugins"

    for finder, name, ispkg in pkgutil.iter_modules([str(package_path)]):
        if name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{package_full}.{name}")
        except Exception:
            continue
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, BasePlugin)
                and obj is not BasePlugin
                and not getattr(obj, "__abstractmethods__", None)
            ):
                try:
                    instance = obj()
                    discovered[instance.name] = obj
                except Exception:
                    pass

    return discovered


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _format_plugin_row(
    name: str, cls: type[BasePlugin], idx: int, total: int
) -> str:
    """Format a single plugin table row."""
    try:
        inst = cls()
        desc = (inst.description or "")[:60]
        cat = inst.get_category().value if hasattr(inst, "get_category") else ""
        role = inst.get_role().value if hasattr(inst, "get_role") else ""
        enabled = "✓" if inst.is_enabled() else "✗"
        has_data = "✓" if inst.has_data() else " "
    except Exception as e:
        desc = f"[error: {e}]"
        cat = role = ""
        enabled = "?"
        has_data = "?"

    return (
        f"  {idx:>3}/{total}  {enabled}  {name:<30}  {cat:<12}  {role:<10}"
        f"  {has_data}  {desc}"
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group("plugin")
def plugin():
    """Manage data plugins and backfill missing historical data."""


@plugin.command("list")
@click.option("--enabled", is_flag=True, help="Show only enabled plugins")
@click.option("--disabled", is_flag=True, help="Show only disabled plugins")
def list_plugins(enabled: bool, disabled: bool):
    """List all available data plugins with status."""
    plugins = _discover_plugins()
    if not plugins:
        click.echo("No plugins found.")
        return

    # Filter
    filtered: dict[str, type[BasePlugin]] = {}
    for name, cls in plugins.items():
        try:
            inst = cls()
            is_enabled = inst.is_enabled()
        except Exception:
            is_enabled = True
        if enabled and not is_enabled:
            continue
        if disabled and is_enabled:
            continue
        filtered[name] = cls

    click.echo(f"\n  {'':>5}  {'Name':<30}  {'Category':<12}  {'Role':<10}  {'OK?'}  Description")
    click.echo(f"  {'─' * 5}  {'─' * 30}  {'─' * 12}  {'─' * 10}  {'───'}  {'─' * 40}")
    for idx, (name, cls) in enumerate(sorted(filtered.items()), 1):
        click.echo(_format_plugin_row(name, cls, idx, len(filtered)))
    click.echo(f"\n  Total: {len(filtered)} plugin(s)\n")


@plugin.command("run")
@click.argument("name")
@click.option("--trade-date", default=None, help="Trading date (YYYYMMDD)")
@click.option("--start-date", default=None, help="Start date (YYYYMMDD)")
@click.option("--end-date", default=None, help="End date (YYYYMMDD)")
@click.option("--ts-code", default=None, help="Optional ts_code filter")
def run_plugin(name: str, trade_date: str | None, start_date: str | None,
               end_date: str | None, ts_code: str | None):
    """Run a single plugin's extract → validate → transform → load pipeline.

    At minimum, ``--trade-date`` or ``--start-date/--end-date`` must be
    provided (depends on the plugin's ``extract_data`` implementation).
    """
    plugins = _discover_plugins()
    cls = plugins.get(name)
    if not cls:
        click.echo(f"Plugin '{name}' not found. Use 'plugin list' to see available plugins.")
        raise click.Abort()

    try:
        inst = cls()
    except Exception as e:
        click.echo(f"Failed to instantiate plugin '{name}': {e}")
        raise click.Abort()

    kwargs: dict[str, Any] = {}
    if trade_date:
        kwargs["trade_date"] = trade_date
    if start_date:
        kwargs["start_date"] = start_date
    if end_date:
        kwargs["end_date"] = end_date
    if ts_code:
        kwargs["ts_code"] = ts_code

    if not kwargs:
        click.echo("No parameters provided. Use --trade-date or --start-date/--end-date.")
        raise click.Abort()

    click.echo(f"Running plugin '{name}' with {kwargs} ...")
    result = inst.run(**kwargs)
    status = result.get("status", "unknown")
    if status == "success":
        click.echo(f"  Status: {click.style(status, fg='green')}")
    elif status == "no_data":
        click.echo(f"  Status: {click.style('no_data', fg='yellow')}")
    else:
        click.echo(f"  Status: {click.style(status, fg='red')}")

    steps = result.get("steps", {})
    for step_name, step_result in steps.items():
        click.echo(f"  [{step_name}] {step_result}")

    loaded = result.get("loaded_records", 0) or result.get("steps", {}).get("load", {}).get("loaded_records", 0)
    if loaded:
        click.echo(f"  Loaded: {loaded} records")


@plugin.command("backfill")
@click.argument("name")
@click.option("--start-date", default=None, help="Start date YYYYMMDD (overrides config.json)")
@click.option("--end-date", default=None, help="End date YYYYMMDD (overrides config.json)")
@click.option("--max-retries", default=None, type=int, help="Max retries per date")
@click.option("--max-fails", default=None, type=int, help="Abort after N consecutive failures")
@click.option("--dry-run", is_flag=True, help="Show what would be done without executing")
def backfill(name: str, start_date: str | None, end_date: str | None,
             max_retries: int | None, max_fails: int | None, dry_run: bool):
    """Backfill missing historical data for a plugin.

    Scans all trading days from ``ods_trade_calendar``, checks which dates
    already exist in the plugin's target table, and runs the pipeline only
    for missing dates.

    Use ``--dry-run`` to preview pending dates without inserting data.
    """
    plugins = _discover_plugins()
    cls = plugins.get(name)
    if not cls:
        click.echo(f"Plugin '{name}' not found. Use 'plugin list' to see available plugins.")
        raise click.Abort()

    try:
        inst = cls()
    except Exception as e:
        click.echo(f"Failed to instantiate plugin '{name}': {e}")
        raise click.Abort()

    if dry_run:
        # Dry-run: just report what would be done
        from datetime import date, datetime

        if not inst.db:
            click.echo("No database connection — cannot load trading calendar for dry-run.")
            return

        config = inst.get_config()
        start_str = start_date or config.get("default_start_date")
        end_str = end_date or config.get("default_end_date", datetime.now().strftime("%Y%m%d"))

        if not start_str:
            click.echo("No start_date configured — set it via --start-date or config.json")
            return

        try:
            start_d = datetime.strptime(start_str, "%Y%m%d").date()
            end_d = datetime.strptime(end_str, "%Y%m%d").date()
        except ValueError as e:
            click.echo(f"Invalid date format: {e}")
            return

        schema = inst.get_schema()
        table_name = schema.get("table_name", "") if schema else ""

        try:
            cal_df = inst.db.execute_query(
                "SELECT cal_date FROM ods_trade_calendar "
                "WHERE cal_date >= %(s)s AND cal_date <= %(e)s AND is_open = 1 "
                "ORDER BY cal_date",
                params={"s": start_d, "e": end_d},
            )
            days = cal_df["cal_date"].tolist() if not cal_df.empty else []
        except Exception as e:
            click.echo(f"Failed to load trading calendar: {e}")
            return

        loaded: set = set()
        if table_name:
            try:
                loaded_df = inst.db.execute_query(
                    f"SELECT DISTINCT trade_date FROM {table_name} FINAL "
                    "WHERE trade_date >= %(s)s AND trade_date <= %(e)s",
                    params={"s": start_d, "e": end_d},
                )
                if not loaded_df.empty and "trade_date" in loaded_df.columns:
                    loaded = set(loaded_df["trade_date"].tolist())
            except Exception:
                pass

        pending = [d for d in days if d not in loaded]

        click.echo(f"\n  Plugin:      {name}")
        click.echo(f"  Table:       {table_name or '(unknown)'}")
        click.echo(f"  Date range:  {start_str} ~ {end_str}")
        click.echo(f"  Trading d:   {len(days)}")
        click.echo(f"  Loaded:      {len(loaded)}")
        click.echo(f"  Pending:     {len(pending)}")
        if pending:
            sample = [d.strftime("%Y%m%d") for d in pending[:5]]
            click.echo(f"  Samples:     {', '.join(sample)}{'...' if len(pending) > 5 else ''}")
        return

    # Real execution
    click.echo(f"Starting backfill for plugin '{name}' ...")
    result = inst.run_backfill(
        start_date=start_date,
        end_date=end_date,
        max_retries=max_retries,
        max_fails=max_fails,
    )
    status = result.get("status", "unknown")
    if status == "success":
        click.echo(f"  Status: {click.style(status, fg='green')}")
    elif status == "warning":
        click.echo(f"  Status: {click.style('completed with errors', fg='yellow')}")
    else:
        click.echo(f"  Status: {click.style(status, fg='red')}")

    bf = result.get("steps", {}).get("backfill", {})
    if bf:
        click.echo(f"  Range:   {bf.get('start_date')} ~ {bf.get('end_date')}")
        click.echo(f"  Days:    {bf.get('ok', 0)} ok / {bf.get('fail', 0)} fail / {bf.get('pending_days', 0)} pending")
        errors = bf.get("errors", [])
        if errors:
            click.echo(f"  Errors (first {len(errors)}):")
            for e in errors[:5]:
                click.echo(f"    - {e.get('trade_date')}: {e.get('error', '')}")
    error = result.get("error")
    if error:
        click.echo(f"  Error: {error}")
