"""
Brain Management CLI Commands (Typer)

Implements subcommands for Brain hot-swap operations:
  sba brain list              : List all saved Brains
  sba brain status            : Show current active Brain status
  sba brain create <domain>   : Create a new Brain from blank template
  sba brain save              : Save active Brain to brain_bank
  sba brain swap <name>       : Load a saved Brain to [active]
  sba brain load <name>       : Alias for swap
  sba brain export <name>     : Export Brain to exports/ directory
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

from ..brain.brain_manager import BrainHotSwapManager, BrainManagerError
from ..brain.blank_template import BlankTemplate, BlankTemplateError
from ..brain.brain_package import BrainPackage
from ..config import SBAConfig


app = typer.Typer(help="Brain hot-swap management commands")


# ----------------------------------------------------------------------------
# 設定ロード（起動時に一度だけ実行）
# ハードコードパスを廃止し、sba_config.yaml から全パスを取得する
# ----------------------------------------------------------------------------

def _load_cfg() -> SBAConfig:
    """SBAConfig をロードする。失敗時はわかりやすいエラーメッセージを出す。"""
    try:
        return SBAConfig.load_env()
    except FileNotFoundError as e:
        typer.secho(str(e), fg=typer.colors.RED)
        raise typer.Exit(code=1)
    except Exception as e:
        typer.secho(f"設定ファイルの読み込みに失敗しました: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)


def _emit_error(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED)


def _resolve_brain_path(brain_name: str, cfg: SBAConfig) -> Path:
    candidate = cfg.brain_bank / brain_name
    if candidate.exists():
        return candidate

    manager = BrainHotSwapManager(cfg.brain_bank, cfg.active)
    matches = manager._find_brain_fuzzy(brain_name)
    if not matches:
        raise BrainManagerError(
            f"Brain not found: {brain_name}. Available: {manager.list_brains_names()}"
        )
    if len(matches) > 1:
        raise BrainManagerError(
            f"Ambiguous Brain name '{brain_name}'. Matches: {matches}"
        )
    return cfg.brain_bank / matches[0]


def _brain_api_template() -> str:
    """
    エクスポートされた Brain に同梱する brain_api.py のテンプレート。

    TODO (Phase 2 完了後に更新):
        現在は subskill_manifest.json からの単純検索のみ。
        Phase 2 でストレージ層（Qdrant + Kuzu）が完成したら、
        以下のメソッドを本実装に差し替えること:
          - query(): Qdrant ベクトル検索 + Kuzu グラフ検索を使ったセマンティック検索
          - graph_neighbors(): KnowledgeChunk の関連ノードをグラフトラバーサルで取得
        設計書: エクスポート・外部利用仕様書（設計書No.11）
    """
    return '''\
"""Standalone Brain API for exported SBA Brain packages.

Usage:
    from brain_api import BrainClient
    client = BrainClient("path/to/exported_brain")
    print(client.info())

TODO (Phase 2):
    query() は現在 subskill_manifest.json からの単純検索のみ。
    Phase 2 完了後に Qdrant ベクトル検索 + Kuzu グラフ検索を実装する。
    設計書: エクスポート・外部利用仕様書（No.11）
"""

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
            "domain":    self.metadata.get("domain"),
            "version":   self.metadata.get("version"),
            "level":     self.metadata.get("level"),
            "subskills": [s.get("id") for s in self.subskill_manifest.get("subskills", [])],
        }

    def eval_scores(self) -> dict:
        return self.self_eval.get("subskills", {})

    def list_subskill(self, subskill_id: str) -> list[dict]:
        return [
            s for s in self.subskill_manifest.get("subskills", [])
            if s.get("id") == subskill_id or s.get("display_name") == subskill_id
        ]

    def query(self, question: str, subskill: str | None = None, top_k: int = 5) -> list[dict]:
        """
        TODO (Phase 2): Qdrant ベクトル検索 + Kuzu グラフ検索に差し替える。
        現在は subskill_manifest からの単純フィルタのみ。
        """
        hits = []
        for s in self.subskill_manifest.get("subskills", []):
            if subskill and s.get("id") != subskill and s.get("display_name") != subskill:
                continue
            hits.append({
                "text":      s.get("description", ""),
                "subskill":  s.get("id"),
                "score":     1.0 if subskill else 0.5,
                "source":    "subskill_manifest.json",
                "question":  question,
            })
        return hits[:top_k]
'''


# ============================================================================
# Command: brain list
# ============================================================================

@app.command(name="list")
def brain_list(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed info"),
) -> None:
    """List all saved Brains in brain_bank."""
    cfg = _load_cfg()
    try:
        manager = BrainHotSwapManager(cfg.brain_bank, cfg.active)
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
    force: bool = typer.Option(False, "--force", "-f", help="Force swap even if current Brain unsaved"),
) -> None:
    """Load (swap) a saved Brain from brain_bank to [active]."""
    cfg = _load_cfg()
    try:
        manager = BrainHotSwapManager(cfg.brain_bank, cfg.active)
        typer.secho(f"Loading Brain: {brain_name}...", fg=typer.colors.CYAN)
        result = manager.load(brain_name, rollback_on_error=True)
        typer.secho(f"[OK] {result['message']}", fg=typer.colors.GREEN)
        typer.echo(f"  Domain : {result['domain']}")
        typer.echo(f"  Version: {result['version']}")
        typer.echo(f"  Loaded : {result['loaded_at']}")
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
    brain_name: str = typer.Argument(..., help="Name of Brain to load"),
) -> None:
    """Alias for 'swap' - load a saved Brain."""
    brain_swap(brain_name, force=False)


# ============================================================================
# Command: brain save
# ============================================================================

@app.command(name="save")
def brain_save(
    name: Optional[str] = typer.Option(None,  "--name", "-n", help="Custom name for this save"),
    description: str    = typer.Option("",    "--desc", "-d", help="Save description/notes"),
) -> None:
    """Save the current active Brain to brain_bank."""
    cfg = _load_cfg()
    try:
        manager = BrainHotSwapManager(cfg.brain_bank, cfg.active)
        typer.secho("Saving active Brain...", fg=typer.colors.CYAN)
        result = manager.save(brain_name=name, description=description)
        typer.secho(f"[OK] {result['message']}", fg=typer.colors.GREEN)
        typer.echo(f"  Brain ID  : {result['brain_id']}")
        typer.echo(f"  Domain    : {result['domain']}")
        typer.echo(f"  Version   : {result['version']}")
        typer.echo(f"  Saved to  : {result['saved_path']}")
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
def brain_status() -> None:
    """Show status of currently active Brain."""
    cfg = _load_cfg()
    try:
        manager = BrainHotSwapManager(cfg.brain_bank, cfg.active)
        info = manager.get_active_brain()
        typer.echo("=" * 60)
        typer.echo("Active Brain Status")
        typer.echo("=" * 60)
        typer.echo(f"Domain  : {info['domain'] or '(blank)'}")
        typer.echo(f"Version : {info['version']}")
        typer.echo(f"Brain ID: {info['brain_id']}")
        typer.echo(f"Level   : {info['level']}")
        typer.echo(f"Size    : {info['size_bytes'] / (1024 * 1024):.1f} MB")
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
    domain:  str           = typer.Argument(..., help="Domain name (e.g., 'Python開発')"),
    name:    Optional[str] = typer.Option(None,   "--name",    "-n", help="Custom Brain name"),
    version: str           = typer.Option("1.0",  "--version", "-v", help="Initial version (default: 1.0)"),
    load:    bool          = typer.Option(True,   "--load/--no-load", help="Load created Brain to [active]"),
) -> None:
    """Create a new Brain from blank template."""
    cfg = _load_cfg()
    try:
        if not domain.strip():
            _emit_error("Error: Domain must not be empty")
            raise typer.Exit(code=1)

        parts = version.split(".")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            _emit_error("Error: Version must be in format X.Y (e.g., 1.0)")
            raise typer.Exit(code=1)

        template = BlankTemplate(cfg.blank_template)
        manager  = BrainHotSwapManager(cfg.brain_bank, cfg.active)

        brain_name = name or f"{domain}_v{version}"
        target_path = cfg.brain_bank / brain_name

        if target_path.exists():
            _emit_error(f"[FAIL] Brain already exists: {brain_name}")
            raise typer.Exit(code=1)

        typer.secho(f"Creating new Brain: {brain_name} ({domain} v{version})...", fg=typer.colors.CYAN)

        cloned_path = template.clone_to(
            target_path,
            domain=domain,
            version=version,
            brain_name=brain_name,
        )

        typer.secho("[OK] Brain created successfully", fg=typer.colors.GREEN)
        typer.echo(f"  Name   : {brain_name}")
        typer.echo(f"  Domain : {domain}")
        typer.echo(f"  Version: {version}")
        typer.echo(f"  Path   : {cloned_path}")

        if load:
            typer.secho("\nLoading to [active]...", fg=typer.colors.CYAN)
            result = manager.load(brain_name, rollback_on_error=True)
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
# Command: brain export
# ============================================================================

@app.command(name="export")
def brain_export(
    brain_name: str            = typer.Argument(..., help="Name of Brain to export"),
    output_dir: Optional[Path] = typer.Option(None, "--output", "-o", help="Export destination"),
) -> None:
    """Export a Brain to exports/ directory."""
    cfg = _load_cfg()
    try:
        source_path = _resolve_brain_path(brain_name, cfg)
        brain       = BrainPackage.from_directory(source_path)
        export_root = output_dir or cfg.exports
        export_root.mkdir(parents=True, exist_ok=True)

        export_path = export_root / source_path.name
        if export_path.exists():
            shutil.rmtree(export_path)
        export_path.mkdir(parents=True, exist_ok=True)

        # Brain Package の 7 コンポーネントをコピー
        for item_name in (
            "knowledge_graph",
            "vector_index",
            "experiment_log.db",
            "learning_timeline.db",
            "subskill_manifest.json",
            "self_eval.json",
            "metadata.json",
        ):
            src = source_path / item_name
            dst = export_path / item_name
            if src.is_dir():
                shutil.copytree(src, dst)
            elif src.exists():
                shutil.copy2(src, dst)

        # exported_at を metadata に追記
        metadata_path = export_path / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        metadata["exported_at"] = datetime.utcnow().isoformat() + "Z"
        metadata_path.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # brain_api.py を同梱（Phase 2 完了後に本実装へ更新）
        (export_path / "brain_api.py").write_text(
            _brain_api_template(),
            encoding="utf-8",
        )

        typer.secho(f"[OK] Brain exported: {source_path.name}", fg=typer.colors.GREEN)
        typer.echo(f"  Output : {export_path}")
        typer.echo(f"  Domain : {brain.metadata.domain}")
        typer.echo(f"  Version: {brain.metadata.version}")

    except BrainManagerError as e:
        _emit_error(f"[FAIL] Export failed: {e}")
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        _emit_error(f"[FAIL] Unexpected error: {e}")
        raise typer.Exit(code=1)
