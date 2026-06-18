"""
Stock Datasource CLI — root entry point.

Usage::

    stock-ds setup           Interactive first-time setup
    stock-ds doctor          Environment health check
    stock-ds server start    Start service
    stock-ds config show     View configuration
    stock-ds plugin list     List all plugins
    stock-ds plugin run      Run plugin by name
    stock-ds plugin backfill Backfill missing historical data
"""

import click


@click.group()
def cli():
    """Stock Datasource — local financial database & API gateway."""
    pass


def register_commands(cli_group):
    """Register all CLI subcommand groups onto the root Click group."""
    from .config_manager import config
    from .doctor import doctor
    from .plugin_manager import plugin
    from .server_manager import server
    from .setup_wizard import setup

    cli_group.add_command(setup)
    cli_group.add_command(doctor)
    cli_group.add_command(server)
    cli_group.add_command(config)
    cli_group.add_command(plugin)


register_commands(cli)
