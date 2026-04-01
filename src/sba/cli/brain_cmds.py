"""
Brain Management CLI Commands (Typer)

Implements subcommands for Brain hot-swap operations:
- sba brain swap <name>      : Load a saved Brain
- sba brain load <name>      : Explicit load (alias for swap)
- sba brain list             : List all saved Brains  
- sba brain export <name>    : Export Brain to exports/ directory
- sba brain status           : Show current active Brain status
"""

import typer
from pathlib import Path
from typing import Optional
from datetime import datetime
import json
import shutil

from ..brain.brain_manager import BrainHotSwapManager, BrainManagerError
from ..brain.blank_template import BlankTemplate, BlankTemplateError
from ..brain.brain_package import BrainPackage


app = typer.Typer(help="Brain hot-swap management commands")


# ============================================================================
# Global configuration (load from sba_config.yaml later)
# ============================================================================

BRAIN_BANK_PATH = Path("C:\\TH_Works\\SBA\\brain_bank")
ACTIVE_PATH = Path("C:\\TH_Works\\SBA\\brain_bank\\[active]")
TEMPLATE_PATH = Path("C:\\TH_Works\\SBA\\brain_bank\\blank_template")
EXPORTS_PATH = Path("C:\\TH_Works\\SBA\\exports")


def _emit_error(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED)


def _resolve_brain_path(brain_name: str) -> Path:
    candidate = BRAIN_BANK_PATH / brain_name
    if candidate.exists():
        return candidate

    manager = BrainHotSwapManager(BRAIN_BANK_PATH, ACTIVE_PATH)
    matches = manager._find_brain_fuzzy(brain_name)
    if not matches:
        raise BrainManagerError(
            f"Brain not found: {brain_name}. Available: {manager.list_brains_names()}"
        )
    if len(matches) > 1:
        raise BrainManagerError(f"Ambiguous Brain name '{brain_name}'. Matches: {matches}")
    return BRAIN_BANK_PATH / matches[0]


def _brain_api_template() -> str:
    return '''"""Standalone Brain API for exported SBA Brain packages."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class BrainClient:
    def __init__(self, path: str):
        self.root = Path(path)
        self.metadata = self._load_json("metadata.json")
        self.subskill_manifest = self._load_json("subskill_manifest.json")
        self.self_eval = self._load_json("self_eval.json")

    def _load_json(self, name: str) -> dict:
        return json.loads((self.root / name).read_text(encoding="utf-8"))

    def info(self) -> dict:
        return {
            "domain": self.metadata.get("domain"),
            "version": self.metadata.get("version"),
            "level": self.metadata.get("level"),
            "subskills": [item.get("id") for item in self.subskill_manifest.get("subskills", [])],
        }

    def eval_scores(self) -> dict:
        return self.self_eval.get("subskills", {})

    def list_subskill(self, subskill_id: str) -> list[dict]:
        return [
            item for item in self.subskill_manifest.get("subskills", [])
            if item.get("id") == subskill_id or item.get("display_name") == subskill_id
        ]

    def query(self, question: str, subskill: str | None = None, top_k: int = 5) -> list[dict]:
        hits = []
        for item in self.subskill_manifest.get("subskills", []):
            if subskill and item.get("id") != subskill and item.get("display_name") != subskill:
                continue
            hits.append(
                {
                    "text": item.get("description", ""),
                    "subskill": item.get("id"),
                    "score": 1.0 if subskill else 0.5,
                    "source": "subskill_manifest.json",
                    "question": question,
                }
            )
        return hits[:top_k]
'''


# ============================================================================
# Command: brain list
# ============================================================================

@app.command(name="list")
def brain_list(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed info")
):
    """List all saved Brains in brain_bank."""
    try:
        manager = BrainHotSwapManager(BRAIN_BANK_PATH, ACTIVE_PATH)
        
        if verbose:
            typer.echo(manager.format_brain_stats())
            typer.echo("")
        
        typer.echo(manager.format_brain_list_table())
        
    except BrainManagerError as e:
        _emit_error(f"Error: {e}")
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        _emit_error(f"Unexpected error: {e}")
        raise typer.Exit(code=1)


# ============================================================================
# Command: brain swap / load
# ============================================================================

@app.command(name="swap")
def brain_swap(
    brain_name: str = typer.Argument(..., help="Name of Brain to load (e.g., 'Python開発_v1.0')"),
    force: bool = typer.Option(False, "--force", "-f", help="Force swap even if current Brain unsaved")
):
    """Load (swap) a saved Brain from brain_bank to [active]."""
    try:
        manager = BrainHotSwapManager(BRAIN_BANK_PATH, ACTIVE_PATH)
        
        # Check if current active has unsaved changes (optional, depends on design)
        
        typer.secho(f"Loading Brain: {brain_name}...", fg=typer.colors.CYAN)
        result = manager.load(brain_name, rollback_on_error=True)
        
        typer.secho(f"[OK] {result['message']}", fg=typer.colors.GREEN)
        typer.echo(f"  Domain: {result['domain']}")
        typer.echo(f"  Version: {result['version']}")
        typer.echo(f"  Loaded at: {result['loaded_at']}")
        
    except BrainManagerError as e:
        _emit_error(f"[FAIL] Load failed: {e}")
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        _emit_error(f"[FAIL] Unexpected error: {e}")
        raise typer.Exit(code=1)


@app.command(name="load")
def brain_load(
    brain_name: str = typer.Argument(..., help="Name of Brain to load")
):
    """Alias for 'swap' - load a saved Brain."""
    brain_swap(brain_name, force=False)


# ============================================================================
# Command: brain save
# ============================================================================

@app.command(name="save")
def brain_save(
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Custom name for this save"),
    description: str = typer.Option("", "--desc", "-d", help="Save description/notes")
):
    """Save the current active Brain to brain_bank."""
    try:
        manager = BrainHotSwapManager(BRAIN_BANK_PATH, ACTIVE_PATH)
        
        typer.secho("Saving active Brain...", fg=typer.colors.CYAN)
        result = manager.save(brain_name=name, description=description)
        
        typer.secho(f"[OK] {result['message']}", fg=typer.colors.GREEN)
        typer.echo(f"  Brain ID: {result['brain_id']}")
        typer.echo(f"  Domain: {result['domain']}")
        typer.echo(f"  Version: {result['version']}")
        typer.echo(f"  Saved to: {result['saved_path']}")
        
    except BrainManagerError as e:
        _emit_error(f"[FAIL] Save failed: {e}")
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        _emit_error(f"[FAIL] Unexpected error: {e}")
        raise typer.Exit(code=1)


# ============================================================================
# Command: brain status
# ============================================================================

@app.command(name="status")
def brain_status():
    """Show status of currently active Brain."""
    try:
        manager = BrainHotSwapManager(BRAIN_BANK_PATH, ACTIVE_PATH)
        
        info = manager.get_active_brain()
        
        typer.echo("=" * 60)
        typer.echo("Active Brain Status")
        typer.echo("=" * 60)
        typer.echo(f"Domain: {info['domain'] or '(blank)'}")
        typer.echo(f"Version: {info['version']}")
        typer.echo(f"Brain ID: {info['brain_id']}")
        typer.echo(f"Level: {info['level']}")
        typer.echo(f"Size: {info['size_bytes'] / (1024*1024):.1f} MB")
        
    except BrainManagerError as e:
        _emit_error(f"[FAIL] Error: {e}")
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        _emit_error(f"[FAIL] Unexpected error: {e}")
        raise typer.Exit(code=1)


# ============================================================================
# Command: brain create
# ============================================================================

@app.command(name="create")
def brain_create(
    domain: str = typer.Argument(..., help="Domain name (e.g., 'Python開発')"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Custom Brain name"),
    version: str = typer.Option("1.0", "--version", "-v", help="Initial version (default: 1.0)"),
    load: bool = typer.Option(True, "--load/--no-load", help="Load created Brain to [active]")
):
    """Create a new Brain from blank template."""
    try:
        if not domain.strip():
            _emit_error("Error: Domain must not be empty")
            raise typer.Exit(code=1)

        # Validate version format
        parts = version.split('.')
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            _emit_error("Error: Version must be in format X.Y (e.g., 1.0)")
            raise typer.Exit(code=1)
        
        template = BlankTemplate(TEMPLATE_PATH)
        manager = BrainHotSwapManager(BRAIN_BANK_PATH, ACTIVE_PATH)
        
        # Generate Brain name if not provided
        if not name:
            name = f"{domain}_v{version}"
        
        typer.secho(f"Creating new Brain: {name} ({domain} v{version})...", fg=typer.colors.CYAN)
        
        # Create target directory in brain_bank
        target_path = BRAIN_BANK_PATH / name
        
        if target_path.exists():
            _emit_error(f"[FAIL] Brain already exists: {name}")
            raise typer.Exit(code=1)
        
        # Clone to brain_bank
        cloned_path = template.clone_to(
            target_path,
            domain=domain,
            version=version,
            brain_name=name
        )
        
        typer.secho(f"[OK] Brain created successfully", fg=typer.colors.GREEN)
        typer.echo(f"  Name: {name}")
        typer.echo(f"  Domain: {domain}")
        typer.echo(f"  Version: {version}")
        typer.echo(f"  Path: {cloned_path}")
        
        # Load to active if requested
        if load:
            typer.secho(f"\nLoading to [active]...", fg=typer.colors.CYAN)
            result = manager.load(name, rollback_on_error=True)
            typer.secho(f"[OK] {result['message']}", fg=typer.colors.GREEN)
        
    except BlankTemplateError as e:
        _emit_error(f"[FAIL] Template error: {e}")
        raise typer.Exit(code=1)
    except BrainManagerError as e:
        _emit_error(f"[FAIL] Manager error: {e}")
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        _emit_error(f"[FAIL] Unexpected error: {e}")
        raise typer.Exit(code=1)


# ============================================================================
# Command: brain export (stub for now)
# ============================================================================

@app.command(name="export")
def brain_export(
    brain_name: str = typer.Argument(..., help="Name of Brain to export"),
    output_dir: Optional[Path] = typer.Option(None, "--output", "-o", help="Export destination")
):
    """Export a Brain to exports/ directory (Task 1-6)."""
    try:
        source_path = _resolve_brain_path(brain_name)
        brain = BrainPackage.from_directory(source_path)
        export_root = output_dir or EXPORTS_PATH
        export_root.mkdir(parents=True, exist_ok=True)

        export_path = export_root / source_path.name
        if export_path.exists():
            shutil.rmtree(export_path)
        export_path.mkdir(parents=True, exist_ok=True)

        for name in [
            "knowledge_graph",
            "vector_index",
            "experiment_log.db",
            "subskill_manifest.json",
            "self_eval.json",
            "metadata.json",
        ]:
            src = source_path / name
            dst = export_path / name
            if src.is_dir():
                shutil.copytree(src, dst)
            elif src.exists():
                shutil.copy2(src, dst)

        metadata_path = export_path / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        metadata["exported_at"] = datetime.utcnow().isoformat()
        metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

        (export_path / "brain_api.py").write_text(_brain_api_template(), encoding="utf-8")

        typer.secho(f"[OK] Brain exported: {source_path.name}", fg=typer.colors.GREEN)
        typer.echo(f"  Output: {export_path}")
        typer.echo(f"  Domain: {brain.metadata.domain}")
        typer.echo(f"  Version: {brain.metadata.version}")
    except BrainManagerError as e:
        _emit_error(f"[FAIL] Export failed: {e}")
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        _emit_error(f"[FAIL] Unexpected error: {e}")
        raise typer.Exit(code=1)


# ============================================================================
# Helper: display error with suggestions
# ============================================================================

def show_available_brains():
    """Show available Brains for user assistance"""
    try:
        manager = BrainHotSwapManager(BRAIN_BANK_PATH, ACTIVE_PATH)
        names = manager.list_brains_names()
        if names:
            typer.echo("\nAvailable Brains:")
            for name in names:
                typer.echo(f"  - {name}")
    except:
        pass
