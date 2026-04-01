"""
SBA Framework Main CLI Entry Point

Usage:
  python -m sba  [COMMAND] [OPTIONS]
  
Main Commands:
  brain        : Brain hot-swap operations (swap/load/save/list/create/export/status)
  daemon       : Manage SBA daemon/scheduler
  config       : Show/edit configuration
  status       : Show system status
"""

import sys
import typer
from typing import Optional
from pathlib import Path

try:
    from sba.cli.brain_cmds import app as brain_app
except ImportError:
    from .cli.brain_cmds import app as brain_app


# Create main app
main_app = typer.Typer(
    name="sba",
    help="SBA Framework - Self-Learning Brain Agent",
    no_args_is_help=True,
)


# Add brain subcommand group
main_app.add_typer(brain_app, name="brain", help="Brain hot-swap management")


# ============================================================================
# Global Commands
# ============================================================================

@main_app.command(name="version")
def show_version():
    """Show SBA version"""
    typer.echo("SBA Framework v0.1.0 (Preliminary)")
    typer.echo("Phase 1: Brain Management Base (In Development)")


@main_app.command(name="config")
def show_config(
    show_all: bool = typer.Option(False, "--all", "-a", help="Show full config")
):
    """Show SBA configuration"""
    typer.secho("SBA Configuration", bold=True)
    typer.echo("-" * 60)
    
    # For now, just show placeholder
    typer.echo("Brain Bank: C:\\TH_Works\\SBA\\brain_bank")
    typer.echo("Active Path: C:\\TH_Works\\SBA\\brain_bank\\[active]")
    typer.echo("Exports: C:\\TH_Works\\SBA\\exports")
    typer.echo("Config: C:\\TH_Works\\SBA\\config\\sba_config.yaml")


@main_app.command(name="status")
def show_status():
    """Show system status"""
    typer.secho("SBA System Status", bold=True)
    typer.echo("-" * 60)
    typer.echo("Phase 1: Brain Management Base (In Development)")
    typer.echo("Status: Initializing...")
    typer.echo("\nUse 'sba brain status' to check active Brain")


# ============================================================================
# Entry point for console script
# ============================================================================

def main():
    """Main entry point"""
    main_app()


# Export for direct module invocation
app = main_app


# ============================================================================
# Entry point for python -m sba
# ============================================================================

if __name__ == "__main__":
    main()
