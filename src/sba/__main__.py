"""
SBA Framework Main CLI Entry Point

Usage:
  python -m sba [COMMAND] [OPTIONS]

Main Commands:
  brain   : Brain hot-swap operations (swap/load/save/list/create/export/status)
  daemon  : Manage SBA daemon/scheduler
  config  : Show configuration
  status  : Show system status
  version : Show SBA version
"""

from __future__ import annotations

import typer

try:
    from sba.cli.brain_cmds import app as brain_app
    from sba.config import SBAConfig
except ImportError:
    from .cli.brain_cmds import app as brain_app
    from .config import SBAConfig


main_app = typer.Typer(
    name="sba",
    help="SBA Framework - Self-Learning Brain Agent",
    no_args_is_help=True,
)

main_app.add_typer(brain_app, name="brain", help="Brain hot-swap management")


# ============================================================================
# Global Commands
# ============================================================================

@main_app.command(name="version")
def show_version() -> None:
    """Show SBA version"""
    typer.echo("SBA Framework v0.1.0")
    typer.echo("Phase 1: Brain Management Base — Complete")


@main_app.command(name="config")
def show_config(
    show_all: bool = typer.Option(False, "--all", "-a", help="Show full config"),
) -> None:
    """Show SBA configuration (loaded from sba_config.yaml)"""
    try:
        cfg = SBAConfig.load_env()
        typer.echo(cfg.summary())
    except FileNotFoundError as e:
        typer.secho(str(e), fg=typer.colors.RED)
        raise typer.Exit(code=1)


@main_app.command(name="status")
def show_status() -> None:
    """Show system status"""
    try:
        cfg = SBAConfig.load_env()
        typer.secho("SBA System Status", bold=True)
        typer.echo("-" * 60)
        typer.echo(f"Project Root: {cfg.project_root}")
        typer.echo(f"Brain Bank  : {cfg.brain_bank}")
        typer.echo("\nUse 'sba brain status' to check active Brain")
    except FileNotFoundError as e:
        typer.secho(str(e), fg=typer.colors.RED)
        raise typer.Exit(code=1)


# ============================================================================
# Entry Points
# ============================================================================

def main() -> None:
    main_app()


app = main_app

if __name__ == "__main__":
    main()
