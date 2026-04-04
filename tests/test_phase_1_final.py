# tests/test_phase_1_final.py
"""
Phase 1 最終確認テスト（P1-07 用）。
Brain 管理基盤の全コンポーネントが正常に import できること、
および設計書規定の 4 コマンドが CLI に登録されていることを確認する。
"""

from __future__ import annotations

import importlib

import pytest
import typer


class TestPhase1ModuleImports:
    """Phase 1 コンポーネントの import 確認。"""

    @pytest.mark.parametrize("module_path", [
        "sba",
        "sba.brain.brain_package",
        "sba.brain.blank_template",
        "sba.brain.brain_manager",
        "sba.cli.brain_cmds",
    ])
    def test_module_importable(self, module_path: str) -> None:
        """指定モジュールが例外なく import できること。"""
        mod = importlib.import_module(module_path)
        assert mod is not None


class TestPhase1CLICommands:
    """CLI に設計書規定の 4 コマンドが登録されていること。"""

    def test_brain_cmds_app_is_typer(self) -> None:
        """brain_cmds.app が Typer インスタンスであること。"""
        from sba.cli.brain_cmds import app
        assert isinstance(app, typer.Typer)

    def test_swap_command_registered(self) -> None:
        """swap コマンドが登録されていること。"""
        from sba.cli.brain_cmds import app
        names = [cmd.name for cmd in app.registered_commands]
        assert "swap" in names

    def test_load_command_registered(self) -> None:
        """load コマンドが登録されていること。"""
        from sba.cli.brain_cmds import app
        names = [cmd.name for cmd in app.registered_commands]
        assert "load" in names

    def test_list_command_registered(self) -> None:
        """list コマンドが登録されていること。"""
        from sba.cli.brain_cmds import app
        names = [cmd.name for cmd in app.registered_commands]
        assert "list" in names

    def test_export_command_registered(self) -> None:
        """export コマンドが登録されていること。"""
        from sba.cli.brain_cmds import app
        names = [cmd.name for cmd in app.registered_commands]
        assert "export" in names


class TestPhase1BrainBankConcept:
    """Brain のライフサイクル概念テスト（モック無しの構造確認）。"""

    def test_blank_template_class_exists(self) -> None:
        """BlankTemplate クラスが存在すること。"""
        from sba.brain.blank_template import BlankTemplate
        assert BlankTemplate is not None

    def test_brain_manager_class_exists(self) -> None:
        """BrainHotSwapManager クラスが存在すること。"""
        from sba.brain.brain_manager import BrainHotSwapManager
        assert BrainHotSwapManager is not None

    def test_brain_package_class_exists(self) -> None:
        """BrainPackage クラスが存在すること。"""
        from sba.brain.brain_package import BrainPackage
        assert BrainPackage is not None

    def test_brain_manager_error_class_exists(self) -> None:
        """BrainManagerError 例外クラスが存在すること。"""
        from sba.brain.brain_manager import BrainManagerError
        assert issubclass(BrainManagerError, Exception)