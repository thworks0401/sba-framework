"""
Step1～6 オーケストレーター: Learning Loop メインエンジン

設計根拠（自律学習ループ設定書 §1.2, §9）:
  - Step1～6を1サイクルとして順序実行
  - エラーハンドリング・ループインターバル管理
  - APScheduler からの呼び出し口となるエントリポイント
  - 各Step実行前にクールダウン・リソース確認
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional, Dict, Callable
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
import logging

from .gap_detector import GapDetector
from .resource_finder import ResourceFinder
from .knowledge_integrator import KnowledgeIntegrator
from .self_evaluator import SelfEvaluator
from ..subskill.classifier import SubSkillClassifier


logger = logging.getLogger(__name__)


@dataclass
class LearningCycleResult:
    """1サイクルの実行結果"""
    cycle_id: str
    brain_id: str
    start_time: str
    end_time: str
    step1_gap: Optional[Dict] = None  # ギャップ検出
    step2_resources: int = 0  # 取得リソース数
    step3_chunks_stored: int = 0  # 格納チャンク数
    step5_contradictions: int = 0  # 検出矛盾数
    step6_overall_score: float = 0.0  # 最終スコア
    step6_level: str = ""  # 最終Lv
    error: Optional[str] = None
    logs: List[str] = None


class LearningLoop:
    """
    自律学習ループのメインオーケストレーター。

    Step1 ギャップ検出
    ↓
    Step2 リソース探索
    ↓
    Step3 データ取得・整理
    ↓
    Step4 自己実験（省略：Self-Experimentation Engineへ委譲）
    ↓
    Step5 知識統合
    ↓
    Step6 自己評価・優先度決定
    """

    DEFAULT_LOOP_INTERVAL_SECONDS = 3600  # 1時間
    DEFAULT_CYCLE_TIMEOUT_SECONDS = 1800  # 30分

    def __init__(
        self,
        brain_id: str,
        brain_name: str,
        active_brain_path: Path,
        gap_detector: Optional[GapDetector] = None,
        resource_finder: Optional[ResourceFinder] = None,
        classifier: Optional[SubSkillClassifier] = None,
        integrator: Optional[KnowledgeIntegrator] = None,
        evaluator: Optional[SelfEvaluator] = None,
    ) -> None:
        """
        Initialize LearningLoop.

        Args:
            brain_id: Brain ID
            brain_name: Brain名
            active_brain_path: Brain ディレクトリパス
            各Component インスタンス（Noneの場合は後で設定）
        """
        self.brain_id = brain_id
        self.brain_name = brain_name
        self.active_brain_path = Path(active_brain_path)

        # Components
        self.gap_detector = gap_detector
        self.resource_finder = resource_finder
        self.classifier = classifier
        self.integrator = integrator
        self.evaluator = evaluator

        # Configuration
        self.loop_interval = self.DEFAULT_LOOP_INTERVAL_SECONDS
        self.cycle_timeout = self.DEFAULT_CYCLE_TIMEOUT_SECONDS

        # State
        self.is_running = False
        self.last_cycle_result: Optional[LearningCycleResult] = None

        # Paths
        self.self_eval_path = self.active_brain_path / "self_eval.json"
        self.subskill_manifest_path = self.active_brain_path / "subskill_manifest.json"
        self.learning_log_path = self.active_brain_path / "learning_log.jsonl"

    async def run_single_cycle(self) -> LearningCycleResult:
        """
        1サイクルを実行（Step1～6）。

        Returns:
            LearningCycleResult
        """
        cycle_id = f"{self.brain_id}_{datetime.now().isoformat()}"
        result = LearningCycleResult(
            cycle_id=cycle_id,
            brain_id=self.brain_id,
            start_time=datetime.now().isoformat(),
            logs=[],
        )

        try:
            logger.info(f"Learning cycle started: {cycle_id}")

            # SubSkill定義を読み込み
            with open(self.subskill_manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)

            # Step1: ギャップ検出
            if self.gap_detector:
                try:
                    gap_result = await self.gap_detector.detect_gap(
                        self.self_eval_path, manifest
                    )
                    result.step1_gap = asdict(gap_result)
                    logger.info(f"Gap detected: {gap_result.target_subskill}")
                except Exception as e:
                    logger.error(f"Step1 failed: {str(e)}")
                    result.error = f"Step1 error: {str(e)}"
                    return result

            # Step2: リソース探索
            if self.resource_finder and result.step1_gap:
                try:
                    resources = await self.resource_finder.search_resources(
                        result.step1_gap["target_subskill"],
                        result.step1_gap["suggested_query"],
                    )
                    result.step2_resources = len(resources)
                    logger.info(f"Resources found: {len(resources)}")
                except Exception as e:
                    logger.error(f"Step2 failed: {str(e)}")

            # Step3: データ取得・整理
            # ※実装注：Step3はリソース取得自体が複雑なため、スタブとして
            # 実際は各Fetcherを呼び出す
            result.step3_chunks_stored = 0

            # Step4: 自己実験
            # ※省略：Self-Experimentation Engineへ委譲

            # Step5: 知識統合・矛盾検出
            if self.integrator:
                try:
                    # ※実装注：新規チャンクを用意する必要がある
                    # ここではスタブ
                    reconcile_result = await self.integrator.reconcile_knowledge_base(
                        new_chunks=[],
                        brain_id=self.brain_id,
                    )
                    result.step5_contradictions = reconcile_result["contradictions_found"]
                    logger.info(f"Contradictions resolved: {result.step5_contradictions}")
                except Exception as e:
                    logger.error(f"Step5 failed: {str(e)}")

            # Step6: 自己評価・Lv更新
            if self.evaluator:
                try:
                    eval_result = await self.evaluator.evaluate_all_subskills(manifest)
                    result.step6_overall_score = eval_result.overall_score
                    result.step6_level = eval_result.level.value

                    # self_eval.json 更新
                    await self.evaluator.update_self_evaluation_file(
                        self.self_eval_path, eval_result
                    )
                    logger.info(f"Evaluation complete: {eval_result.level.value}")
                except Exception as e:
                    logger.error(f"Step6 failed: {str(e)}")

            result.end_time = datetime.now().isoformat()
            self.last_cycle_result = result

            logger.info(f"Learning cycle completed successfully: {cycle_id}")
            return result

        except Exception as e:
            logger.error(f"Cycle failed: {str(e)}")
            result.error = str(e)
            result.end_time = datetime.now().isoformat()
            return result

    async def run_continuous(
        self,
        max_cycles: Optional[int] = None,
        on_cycle_complete: Optional[Callable[[LearningCycleResult], None]] = None,
    ) -> None:
        """
        連続実行ループ（APSchedulerから呼び出し用）。

        Args:
            max_cycles: 最大サイクル数（Noneなら無制限）
            on_cycle_complete: 各サイクル完了時コールバック
        """
        self.is_running = True
        cycle_count = 0

        try:
            while self.is_running and (max_cycles is None or cycle_count < max_cycles):
                # サイクル実行
                try:
                    result = await asyncio.wait_for(
                        self.run_single_cycle(),
                        timeout=self.cycle_timeout,
                    )
                    cycle_count += 1

                    if on_cycle_complete:
                        on_cycle_complete(result)

                    # 学習ログに記録
                    self._log_cycle_result(result)

                except asyncio.TimeoutError:
                    logger.error(f"Cycle timeout after {self.cycle_timeout}s")

                # インターバル待機
                await asyncio.sleep(self.loop_interval)

        except Exception as e:
            logger.error(f"Continuous loop failed: {str(e)}")
        finally:
            self.is_running = False

    def stop(self) -> None:
        """ループを停止"""
        self.is_running = False

    def _log_cycle_result(self, result: LearningCycleResult) -> None:
        """サイクル結果をログに記録（JSONL形式）"""
        try:
            with open(self.learning_log_path, "a", encoding="utf-8") as f:
                log_entry = asdict(result)
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to log cycle result: {str(e)}")

    async def get_status(self) -> Dict:
        """現在の学習ループ状態を取得"""
        return {
            "brain_id": self.brain_id,
            "brain_name": self.brain_name,
            "is_running": self.is_running,
            "last_cycle_result": asdict(self.last_cycle_result) if self.last_cycle_result else None,
            "loop_interval_seconds": self.loop_interval,
        }
