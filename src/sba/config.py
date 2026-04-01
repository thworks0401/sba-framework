"""
SBA Configuration Loader

sba_config.yaml を読み込み、全パスを解決した SBAConfig オブジェクトを提供する。
各コンポーネントはこのオブジェクトを通じてパスを取得し、ハードコードを排除する。

使い方:
    from sba.config import SBAConfig
    cfg = SBAConfig.load()          # デフォルト: C:/TH_Works/SBA/sba_config.yaml
    cfg = SBAConfig.load(path)      # カスタムパス指定
    cfg = SBAConfig.load_env()      # 環境変数 SBA_CONFIG_PATH 優先
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, model_validator


# デフォルト config パス（環境変数で上書き可能）
DEFAULT_CONFIG_PATH = Path("C:/TH_Works/SBA/sba_config.yaml")
ENV_CONFIG_PATH_KEY = "SBA_CONFIG_PATH"


class ApiKeysConfig(BaseModel):
    """APIキー設定"""
    gemini: str = Field(default="", description="Gemini API Key")
    youtube: str = Field(default="", description="YouTube Data API Key")
    github:  str = Field(default="", description="GitHub API Token")


class ThresholdsConfig(BaseModel):
    """各種閾値設定"""
    default:             float = Field(default=0.5,  ge=0.0, le=1.0)
    similarity_dedup:    float = Field(default=0.92, ge=0.0, le=1.0, description="重複チェック閾値（補足設計書§2.1）")
    subskill_weak:       float = Field(default=0.6,  ge=0.0, le=1.0, description="弱点SubSkill判定閾値")
    trust_score_default: float = Field(default=0.7,  ge=0.0, le=1.0, description="新規チャンクのデフォルト信頼スコア")


class SBAConfig(BaseModel):
    """
    SBA Framework 設定オブジェクト。
    sba_config.yaml から生成され、全コンポーネントに渡す。
    """

    # --- プロジェクトルート ---
    project_root: Path = Field(description="SBAのルートディレクトリ")

    # --- パス群（全て project_root 相対で自動解決） ---
    brain_bank:     Path = Field(description="Brain Bank ディレクトリ")
    active:         Path = Field(description="[active] Brain ディレクトリ")
    blank_template: Path = Field(description="Blank Template ディレクトリ")
    exports:        Path = Field(description="エクスポート出力先")
    logs:           Path = Field(description="ログ出力先")
    data:           Path = Field(description="SQLite DB 等のデータ格納先")
    scripts:        Path = Field(description="PowerShell スクリプト等")

    # --- APIキー ---
    api_keys: ApiKeysConfig = Field(default_factory=ApiKeysConfig)

    # --- 閾値 ---
    thresholds: ThresholdsConfig = Field(default_factory=ThresholdsConfig)

    @model_validator(mode="before")
    @classmethod
    def _resolve_paths(cls, values: dict) -> dict:
        """project_root を基準に未指定パスをデフォルト値で補完する"""
        root = Path(values.get("project_root", DEFAULT_CONFIG_PATH.parent))
        paths = values.get("paths", {})

        def _p(key: str, default_rel: str) -> Path:
            raw = paths.get(key)
            return Path(raw) if raw else root / default_rel

        values["brain_bank"]     = _p("brain_bank",     "brain_bank")
        values["active"]         = _p("active",         "brain_bank/[active]")
        values["blank_template"] = _p("blank_template", "brain_bank/blank_template")
        values["exports"]        = _p("exports",        "exports")
        values["logs"]           = _p("logs",           "logs")
        values["data"]           = _p("data",           "data")
        values["scripts"]        = _p("scripts",        "scripts")

        # api_keys / thresholds をネストしたまま渡す
        if "api_keys" not in values:
            values["api_keys"] = values.pop("api_keys", {})
        if "thresholds" not in values:
            values["thresholds"] = {}

        return values

    # ------------------------------------------------------------------
    # ファクトリメソッド
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, config_path: Optional[Path | str] = None) -> "SBAConfig":
        """
        指定パスの sba_config.yaml を読み込んで SBAConfig を返す。

        Args:
            config_path: yaml ファイルのパス。省略時はデフォルト。

        Raises:
            FileNotFoundError: ファイルが見つからない場合
            ValueError: YAML のパースに失敗した場合
        """
        path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH

        if not path.exists():
            raise FileNotFoundError(
                f"sba_config.yaml が見つかりません: {path}\n"
                f"補足設計書§4.2 の手順で初期セットアップを完了させてください。"
            )

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
        except yaml.YAMLError as e:
            raise ValueError(f"sba_config.yaml のパースに失敗しました: {e}")

        if not isinstance(raw, dict):
            raise ValueError(f"sba_config.yaml の形式が不正です（dictが必要）: {path}")

        return cls.model_validate(raw)

    @classmethod
    def load_env(cls) -> "SBAConfig":
        """
        環境変数 SBA_CONFIG_PATH があればそれを使い、なければデフォルトを使う。
        CI / 複数環境での切り替えに便利。
        """
        env_path = os.environ.get(ENV_CONFIG_PATH_KEY)
        return cls.load(env_path if env_path else None)

    # ------------------------------------------------------------------
    # ユーティリティ
    # ------------------------------------------------------------------

    def ensure_dirs(self) -> None:
        """必要なディレクトリを全て作成する（存在しない場合のみ）"""
        for attr in ("brain_bank", "active", "blank_template", "exports", "logs", "data", "scripts"):
            p: Path = getattr(self, attr)
            p.mkdir(parents=True, exist_ok=True)

    def summary(self) -> str:
        """設定内容のサマリを文字列で返す（CLI の config コマンド用）"""
        lines = [
            "=" * 60,
            "SBA Configuration",
            "=" * 60,
            f"Project Root  : {self.project_root}",
            f"Brain Bank    : {self.brain_bank}",
            f"Active        : {self.active}",
            f"Blank Template: {self.blank_template}",
            f"Exports       : {self.exports}",
            f"Logs          : {self.logs}",
            f"Data          : {self.data}",
            "-" * 60,
            f"Gemini API    : {'設定済み' if self.api_keys.gemini else '未設定'}",
            f"YouTube API   : {'設定済み' if self.api_keys.youtube else '未設定'}",
            f"GitHub Token  : {'設定済み' if self.api_keys.github else '未設定'}",
            "-" * 60,
            f"Similarity Dedup Threshold : {self.thresholds.similarity_dedup}",
            f"Weak SubSkill Threshold    : {self.thresholds.subskill_weak}",
        ]
        return "\n".join(lines)
